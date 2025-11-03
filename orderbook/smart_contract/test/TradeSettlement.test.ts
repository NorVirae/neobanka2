import { expect } from "chai";
import { ethers } from "hardhat";
import { TradeSettlement, MockToken } from "../typechain-types";
import { SignerWithAddress } from "@nomicfoundation/hardhat-ethers/signers";

describe("Cross-Chain Trade Settlement", function () {
  let tradeSettlement: TradeSettlement;
  let mockHBAR: MockToken;
  let mockUSDT: MockToken;
  let owner: SignerWithAddress;
  let traderA: SignerWithAddress;
  let traderB: SignerWithAddress;
  let party1ReceiveWallet: SignerWithAddress;
  let party2ReceiveWallet: SignerWithAddress;
  
  const CHAIN_A_ID = 31337n;
  const CHAIN_B_ID = 31337n;
  const PRICE = ethers.parseEther("5");
  const QUANTITY = ethers.parseEther("100");
  const QUOTE_AMOUNT = (QUANTITY * PRICE) / ethers.parseEther("1");

  beforeEach(async function () {
    [owner, traderA, traderB, party1ReceiveWallet, party2ReceiveWallet] = await ethers.getSigners();

    // Deploy mock tokens
    const MockERC20Factory = await ethers.getContractFactory("MockToken");
    mockHBAR = await MockERC20Factory.deploy("HBAR", "HBAR", ethers.parseEther("10000"));
    mockUSDT = await MockERC20Factory.deploy("USDT", "USDT", ethers.parseEther("10000"));

    // Deploy settlement contract
    const TradeSettlementFactory = await ethers.getContractFactory("TradeSettlement");
    tradeSettlement = await TradeSettlementFactory.deploy();

    // Fund traders
    await mockHBAR.transfer(traderA.address, ethers.parseEther("1000"));
    await mockUSDT.transfer(traderB.address, ethers.parseEther("1000"));

    // Approve contract
    await mockHBAR.connect(traderA).approve(await tradeSettlement.getAddress(), ethers.parseEther("1000"));
    await mockUSDT.connect(traderB).approve(await tradeSettlement.getAddress(), ethers.parseEther("1000"));
  });

  function createTradeData(orderId: string, timestamp: number, nonce1 = 0n, nonce2 = 0n) {
    return {
      orderId: ethers.id(orderId),
      party1: traderA.address,
      party2: traderB.address,
      party1ReceiveWallet: party1ReceiveWallet.address,
      party2ReceiveWallet: party2ReceiveWallet.address,
      baseAsset: mockHBAR.target,
      quoteAsset: mockUSDT.target,
      price: PRICE,
      quantity: QUANTITY,
      party1Side: "ask",
      party2Side: "bid",
      sourceChainId: CHAIN_A_ID,
      destinationChainId: CHAIN_B_ID,
      timestamp: BigInt(timestamp),
      nonce1,
      nonce2
    };
  }

  // Signatures are no longer required

  it("1. Should deposit and lock funds for both traders", async function () {
    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);
    await tradeSettlement.connect(traderB).depositToEscrow(mockUSDT.target, QUOTE_AMOUNT);

    const balanceA = await tradeSettlement.checkEscrowBalance(traderA.address, mockHBAR.target);
    const balanceB = await tradeSettlement.checkEscrowBalance(traderB.address, mockUSDT.target);

    expect(balanceA[0]).to.equal(QUANTITY);
    expect(balanceA[1]).to.equal(QUANTITY);
    expect(balanceB[0]).to.equal(QUOTE_AMOUNT);
    expect(balanceB[1]).to.equal(QUOTE_AMOUNT);
  });

  it("2. Should settle on source chain - TraderA sends HBAR to party2ReceiveWallet", async function () {
    const orderId = "order1";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);
    
    // Auto-lock now happens inside settleCrossChainTrade
    
    const initialBalance = await mockHBAR.balanceOf(party2ReceiveWallet.address);

    await tradeSettlement.settleCrossChainTrade(tradeData, true);

    const finalBalance = await mockHBAR.balanceOf(party2ReceiveWallet.address);
    expect(finalBalance - initialBalance).to.equal(QUANTITY);

    const [total, , locked] = await tradeSettlement.checkEscrowBalance(traderA.address, mockHBAR.target);
    expect(total).to.equal(0n);
    expect(locked).to.equal(0n);
  });

  // it("3. Should settle on destination chain - TraderB sends USDT to party1ReceiveWallet", async function () {
  //   const orderId = "order2";
  //   const timestamp = Math.floor(Date.now() / 1000);
  //   const tradeData = createTradeData(orderId, timestamp);

  //   await tradeSettlement.connect(traderB).depositToEscrow(mockUSDT.target, QUOTE_AMOUNT);

  //   // Auto-lock now happens inside settleCrossChainTrade

  //   const initialBalance = await mockUSDT.balanceOf(party1ReceiveWallet.address);

  //   await tradeSettlement.settleCrossChainTrade(tradeData, false);

  //   const finalBalance = await mockUSDT.balanceOf(party1ReceiveWallet.address);
  //   expect(finalBalance - initialBalance).to.equal(QUOTE_AMOUNT);

  //   const [total, , locked] = await tradeSettlement.checkEscrowBalance(traderB.address, mockUSDT.target);
  //   expect(total).to.equal(0n);
  //   expect(locked).to.equal(0n);
  // });

  it("3b. Should settle same-chain by transferring both legs in one tx", async function () {
    const orderId = "order2b";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    // Deposit both legs to escrow
    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);
    await tradeSettlement.connect(traderB).depositToEscrow(mockUSDT.target, QUOTE_AMOUNT);

    const baseBefore = await mockHBAR.balanceOf(party2ReceiveWallet.address);
    const quoteBefore = await mockUSDT.balanceOf(party1ReceiveWallet.address);

    await (tradeSettlement as any).settleSameChainTrade(tradeData);

    const baseAfter = await mockHBAR.balanceOf(party2ReceiveWallet.address);
    const quoteAfter = await mockUSDT.balanceOf(party1ReceiveWallet.address);
    expect(baseAfter - baseBefore).to.equal(QUANTITY);
    expect(quoteAfter - quoteBefore).to.equal(QUOTE_AMOUNT);
  });

  it("4. Should revert when non-owner tries to settle", async function () {
    const orderId = "order3";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);

    await expect(
      tradeSettlement.connect(traderA).settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWithCustomError(tradeSettlement, "OwnableUnauthorizedAccount");
  });

  it("5. Should revert on wrong chain id vs tradeData", async function () {
    const orderId = "order4";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);
    // Flip source/destination to trigger chain check failure if isSourceChain flag doesn't match
    tradeData.sourceChainId = 99999n;
    await expect(
      tradeSettlement.settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWith("Not source chain");
  });

  it("6. Should prevent replay attacks", async function () {
    const orderId = "order5";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY * 2n);

    // Auto-lock will occur in the first settle

    await tradeSettlement.settleCrossChainTrade(tradeData, true);

    await expect(
      tradeSettlement.settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWith("Order already settled on this chain");
  });

  it("7. Should revert with insufficient escrow to lock", async function () {
    const orderId = "order6";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);

    // Deposit less than required
    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY / 2n);

    await expect(
      tradeSettlement.settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWith("Insufficient escrow to lock (source)");
  });

  it("8. Should revert with invalid receive wallet", async function () {
    const orderId = "order7";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);
    tradeData.party1ReceiveWallet = ethers.ZeroAddress;

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);

    await expect(
      tradeSettlement.settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWith("Invalid party1 receive wallet");
  });

  it("9. Should revert when parties are on same side", async function () {
    const orderId = "order8";
    const timestamp = Math.floor(Date.now() / 1000);
    const tradeData = createTradeData(orderId, timestamp);
    tradeData.party2Side = "ask"; // Both asking

    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY);

    await expect(
      tradeSettlement.settleCrossChainTrade(tradeData, true)
    ).to.be.revertedWith("Parties must be on opposite sides");
  });

  it("10. Should allow withdrawal of unlocked funds", async function () {
    await tradeSettlement.connect(traderA).depositToEscrow(mockHBAR.target, QUANTITY * 2n);

    const initialBalance = await mockHBAR.balanceOf(traderA.address);

    await tradeSettlement.connect(traderA).withdrawFromEscrow(mockHBAR.target, QUANTITY);

    const finalBalance = await mockHBAR.balanceOf(traderA.address);
    expect(finalBalance - initialBalance).to.equal(QUANTITY);

    const [total, available] = await tradeSettlement.checkEscrowBalance(traderA.address, mockHBAR.target);
    expect(total).to.equal(QUANTITY);
    expect(available).to.equal(QUANTITY);
  });
});