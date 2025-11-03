"""
Cross-Chain Trade Settlement Script
Tests TradeSettlement contract between Hedera (source) and Sepolia (destination)
"""

from web3 import Web3
from eth_account import Account
import json
import time
from decimal import Decimal

# ============================================================================
# CONFIGURATION - Fill in your values here
# ============================================================================

CONFIG = {
    # Private Keys
    "PARTY1_PRIVATE_KEY": "95e3cc92f9d68c3a0a25c06a226b2faa12136d67798d180e39984563c59baa0c",  # Party1 (seller on Hedera/source chain)
    "PARTY2_PRIVATE_KEY": "69321f7166c9bbbaf2cc1492f747c93e7a60d0273fccf8a4823df05ed683c932",  # Party2 (buyer on Sepolia/destination chain)
    "OWNER_PRIVATE_KEY": "95e3cc92f9d68c3a0a25c06a226b2faa12136d67798d180e39984563c59baa0c",   # Contract owner (can be same as party1 or party2)
    
    # RPC Endpoints
    "HEDERA_RPC": "https://testnet.hedera.validationcloud.io/v1/BeAXOBDow1uicay5NacgIBPWxn5sS2CeYyd99M1m6OA",  # Hedera Testnet
    "SEPOLIA_RPC": "https://ethereum-sepolia-rpc.publicnode.com",  # Replace with your key
    
    # Contract Addresses
    "HEDERA_SETTLEMENT_CONTRACT": "0x36516ec2D2B1DafEc919c4c25778ba47A9B352Bb",  # TradeSettlement on Hedera
    "SEPOLIA_SETTLEMENT_CONTRACT": "0x7179D8F413D61ec32161ed159213Fa07B0725932",  # TradeSettlement on Sepolia
    
    # Token Addresses
    "HEDERA_BASE_TOKEN": "0x04a6532799118BFF060b66fFe1732778765a43Dd",   # Base asset token on Hedera (e.g., HBAR-wrapped or ERC20)
    "SEPOLIA_QUOTE_TOKEN": "0x68A47b99505303430d85dCFeA0BC5e2BFdBfDA70",  # Quote asset token on Sepolia (e.g., USDT)
    
    # Receive Wallets (where each party receives their assets)
    "PARTY1_RECEIVE_WALLET": "0x01b144754760f89dc0179a22bafa426427ad9592",  # Party1's wallet on Sepolia to receive quote
    "PARTY2_RECEIVE_WALLET": "0x19bea165cc295752d0fce4851af77420f163e279",  # Party2's wallet on Hedera to receive base
    
    # Chain IDs
    "HEDERA_CHAIN_ID": 296,  # Hedera Testnet
    "SEPOLIA_CHAIN_ID": 11155111,  # Sepolia Testnet
    
    # Trade Parameters
    "QUANTITY": "1000000000000000000",  # 1 token (18 decimals) - base amount
    "PRICE": "2000000000000000000",     # 2.0 (18 decimals) - price per base token
    "ESCROW_DEPOSIT_AMOUNT": "5000000000000000000",  # 5 tokens for escrow
}

# ============================================================================
# CONTRACT ABI (Minimal - only functions we need)
# ============================================================================

SETTLEMENT_ABI = json.loads('''[
    {
        "inputs": [{"internalType": "address","name": "token","type": "address"},{"internalType": "uint256","name": "amount","type": "uint256"}],
        "name": "depositToEscrow",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "address","name": "user","type": "address"},{"internalType": "address","name": "token","type": "address"}],
        "name": "checkEscrowBalance",
        "outputs": [{"internalType": "uint256","name": "total","type": "uint256"},{"internalType": "uint256","name": "available","type": "uint256"},{"internalType": "uint256","name": "locked","type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "bytes32","name": "orderId","type": "bytes32"},
                    {"internalType": "address","name": "party1","type": "address"},
                    {"internalType": "address","name": "party2","type": "address"},
                    {"internalType": "address","name": "party1ReceiveWallet","type": "address"},
                    {"internalType": "address","name": "party2ReceiveWallet","type": "address"},
                    {"internalType": "address","name": "baseAsset","type": "address"},
                    {"internalType": "address","name": "quoteAsset","type": "address"},
                    {"internalType": "uint256","name": "price","type": "uint256"},
                    {"internalType": "uint256","name": "quantity","type": "uint256"},
                    {"internalType": "string","name": "party1Side","type": "string"},
                    {"internalType": "string","name": "party2Side","type": "string"},
                    {"internalType": "uint256","name": "sourceChainId","type": "uint256"},
                    {"internalType": "uint256","name": "destinationChainId","type": "uint256"},
                    {"internalType": "uint256","name": "timestamp","type": "uint256"},
                    {"internalType": "uint256","name": "nonce1","type": "uint256"},
                    {"internalType": "uint256","name": "nonce2","type": "uint256"}
                ],
                "internalType": "struct TradeSettlement.CrossChainTradeData",
                "name": "tradeData",
                "type": "tuple"
            },
            {"internalType": "bool","name": "isSourceChain","type": "bool"}
        ],
        "name": "settleCrossChainTrade",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32","name": "orderId","type": "bytes32"}],
        "name": "getSettlementStatus",
        "outputs": [
            {"internalType": "bool","name": "sourceChainSettled","type": "bool"},
            {"internalType": "bool","name": "destinationChainSettled","type": "bool"},
            {"internalType": "uint256","name": "sourceChainTimestamp","type": "uint256"},
            {"internalType": "uint256","name": "destinationChainTimestamp","type": "uint256"},
            {"internalType": "bool","name": "refunded","type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]''')

ERC20_ABI = json.loads('''[
    {
        "inputs": [{"internalType": "address","name": "spender","type": "address"},{"internalType": "uint256","name": "amount","type": "uint256"}],
        "name": "approve",
        "outputs": [{"internalType": "bool","name": "","type": "bool"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "address","name": "account","type": "address"}],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256","name": "","type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "address","name": "owner","type": "address"},{"internalType": "address","name": "spender","type": "address"}],
        "name": "allowance",
        "outputs": [{"internalType": "uint256","name": "","type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]''')

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def generate_order_id():
    """Generate unique order ID"""
    return Web3.keccak(text=f"order_{int(time.time())}_{int(time.time() * 1000000) % 1000000}")

def wait_for_tx(web3, tx_hash, chain_name):
    """Wait for transaction confirmation"""
    print(f"⏳ Waiting for transaction on {chain_name}: {tx_hash.hex()}")
    receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt.status == 1:
        print(f"✅ Transaction confirmed on {chain_name}")
    else:
        print(f"❌ Transaction failed on {chain_name}")
    return receipt

def approve_token(web3, token_address, spender_address, amount, private_key, chain_name):
    """Approve token spending"""
    account = Account.from_key(private_key)
    token_contract = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
    
    # Check current allowance
    allowance = token_contract.functions.allowance(account.address, spender_address).call()
    print(f"📋 Current allowance on {chain_name}: {allowance}")
    
    if allowance >= amount:
        print(f"✅ Already approved on {chain_name}")
        return None
    
    # Build approval transaction
    print(f"🔓 Approving {amount} tokens on {chain_name}...")
    nonce = web3.eth.get_transaction_count(account.address)
    
    tx = token_contract.functions.approve(
        Web3.to_checksum_address(spender_address),
        amount
    ).build_transaction({
        'from': account.address,
        'gas': 100000,
        'gasPrice': web3.eth.gas_price,
        'nonce': nonce,
        'chainId': web3.eth.chain_id
    })
    
    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    return wait_for_tx(web3, tx_hash, chain_name)

def deposit_to_escrow(web3, settlement_address, token_address, amount, private_key, chain_name):
    """Deposit tokens to escrow"""
    account = Account.from_key(private_key)
    settlement_contract = web3.eth.contract(
        address=Web3.to_checksum_address(settlement_address),
        abi=SETTLEMENT_ABI
    )
    
    print(f"💰 Depositing {amount} to escrow on {chain_name}...")
    nonce = web3.eth.get_transaction_count(account.address)
    
    tx = settlement_contract.functions.depositToEscrow(
        Web3.to_checksum_address(token_address),
        amount
    ).build_transaction({
        'from': account.address,
        'gas': 200000,
        'gasPrice': web3.eth.gas_price,
        'nonce': nonce,
        'chainId': web3.eth.chain_id
    })
    
    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    return wait_for_tx(web3, tx_hash, chain_name)

def check_escrow_balance(web3, settlement_address, user_address, token_address, chain_name):
    """Check escrow balance"""
    settlement_contract = web3.eth.contract(
        address=Web3.to_checksum_address(settlement_address),
        abi=SETTLEMENT_ABI
    )
    
    total, available, locked = settlement_contract.functions.checkEscrowBalance(
        Web3.to_checksum_address(user_address),
        Web3.to_checksum_address(token_address)
    ).call()
    
    print(f"📊 Escrow Balance on {chain_name}:")
    print(f"   Total: {total}")
    print(f"   Available: {available}")
    print(f"   Locked: {locked}")
    
    return total, available, locked

def settle_cross_chain_trade(web3, settlement_address, trade_data, is_source_chain, private_key, chain_name):
    """Settle cross-chain trade"""
    account = Account.from_key(private_key)
    settlement_contract = web3.eth.contract(
        address=Web3.to_checksum_address(settlement_address),
        abi=SETTLEMENT_ABI
    )
    
    chain_type = "SOURCE" if is_source_chain else "DESTINATION"
    print(f"🔄 Settling {chain_type} chain on {chain_name}...")
    
    nonce = web3.eth.get_transaction_count(account.address)
    
    tx = settlement_contract.functions.settleCrossChainTrade(
        trade_data,
        is_source_chain
    ).build_transaction({
        'from': account.address,
        'gas': 500000,
        'gasPrice': web3.eth.gas_price,
        'nonce': nonce,
        'chainId': web3.eth.chain_id
    })
    
    signed_tx = web3.eth.account.sign_transaction(tx, private_key)
    tx_hash = web3.eth.send_raw_transaction(signed_tx.raw_transaction)
    
    return wait_for_tx(web3, tx_hash, chain_name)

def check_settlement_status(web3, settlement_address, order_id, chain_name):
    """Check settlement status"""
    settlement_contract = web3.eth.contract(
        address=Web3.to_checksum_address(settlement_address),
        abi=SETTLEMENT_ABI
    )
    
    status = settlement_contract.functions.getSettlementStatus(order_id).call()
    source_settled, dest_settled, source_ts, dest_ts, refunded = status
    
    print(f"📈 Settlement Status on {chain_name}:")
    print(f"   Source Chain Settled: {source_settled}")
    print(f"   Destination Chain Settled: {dest_settled}")
    print(f"   Source Timestamp: {source_ts}")
    print(f"   Destination Timestamp: {dest_ts}")
    print(f"   Refunded: {refunded}")
    
    return status

# ============================================================================
# MAIN EXECUTION
# ============================================================================

def main():
    print("=" * 80)
    print("CROSS-CHAIN TRADE SETTLEMENT TEST")
    print("=" * 80)
    print()
    
    # Validate configuration
    if not CONFIG["PARTY1_PRIVATE_KEY"] or not CONFIG["PARTY2_PRIVATE_KEY"]:
        print("❌ ERROR: Please fill in private keys in CONFIG")
        return
    
    if not CONFIG["HEDERA_SETTLEMENT_CONTRACT"] or not CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"]:
        print("❌ ERROR: Please fill in settlement contract addresses in CONFIG")
        return
    
    # Initialize Web3 connections
    print("🌐 Connecting to chains...")
    hedera_w3 = Web3(Web3.HTTPProvider(CONFIG["HEDERA_RPC"]))
    sepolia_w3 = Web3(Web3.HTTPProvider(CONFIG["SEPOLIA_RPC"]))
    
    if not hedera_w3.is_connected():
        print("❌ Failed to connect to Hedera")
        return
    
    if not sepolia_w3.is_connected():
        print("❌ Failed to connect to Sepolia")
        return
    
    print("✅ Connected to both chains")
    print()
    
    # Get accounts
    party1_account = Account.from_key(CONFIG["PARTY1_PRIVATE_KEY"])
    party2_account = Account.from_key(CONFIG["PARTY2_PRIVATE_KEY"])
    owner_account = Account.from_key(CONFIG["OWNER_PRIVATE_KEY"] or CONFIG["PARTY1_PRIVATE_KEY"])
    
    print(f"👤 Party1 (Seller): {party1_account.address}")
    print(f"👤 Party2 (Buyer): {party2_account.address}")
    print(f"👤 Owner: {owner_account.address}")
    print()
    
    # Generate order ID
    order_id = generate_order_id()
    print(f"🆔 Order ID: {order_id.hex()}")
    print()
    
    # Parse amounts
    quantity = int(CONFIG["QUANTITY"])
    price = int(CONFIG["PRICE"])
    deposit_amount = int(CONFIG["ESCROW_DEPOSIT_AMOUNT"])
    
    # Calculate quote amount
    quote_amount = (quantity * price) // 10**18
    print(f"📊 Trade Details:")
    print(f"   Quantity (base): {quantity}")
    print(f"   Price: {price}")
    print(f"   Quote Amount: {quote_amount}")
    print()
    
    # ========================================================================
    # STEP 1: Setup Hedera (Source Chain) - Party1 deposits base asset
    # ========================================================================
    print("=" * 80)
    print("STEP 1: HEDERA (SOURCE CHAIN) SETUP")
    print("=" * 80)
    print()
    
    # Approve and deposit for Party1 on Hedera
    approve_token(
        hedera_w3,
        CONFIG["HEDERA_BASE_TOKEN"],
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        deposit_amount,
        CONFIG["PARTY1_PRIVATE_KEY"],
        "Hedera"
    )
    
    deposit_to_escrow(
        hedera_w3,
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        CONFIG["HEDERA_BASE_TOKEN"],
        deposit_amount,
        CONFIG["PARTY1_PRIVATE_KEY"],
        "Hedera"
    )
    
    check_escrow_balance(
        hedera_w3,
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        party1_account.address,
        CONFIG["HEDERA_BASE_TOKEN"],
        "Hedera"
    )
    print()
    
    # ========================================================================
    # STEP 2: Setup Sepolia (Destination Chain) - Party2 deposits quote asset
    # ========================================================================
    print("=" * 80)
    print("STEP 2: SEPOLIA (DESTINATION CHAIN) SETUP")
    print("=" * 80)
    print()
    
    # Approve and deposit for Party2 on Sepolia
    approve_token(
        sepolia_w3,
        CONFIG["SEPOLIA_QUOTE_TOKEN"],
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        deposit_amount,
        CONFIG["PARTY2_PRIVATE_KEY"],
        "Sepolia"
    )
    
    deposit_to_escrow(
        sepolia_w3,
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        CONFIG["SEPOLIA_QUOTE_TOKEN"],
        deposit_amount,
        CONFIG["PARTY2_PRIVATE_KEY"],
        "Sepolia"
    )
    
    check_escrow_balance(
        sepolia_w3,
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        party2_account.address,
        CONFIG["SEPOLIA_QUOTE_TOKEN"],
        "Sepolia"
    )
    print()
    
    # ========================================================================
    # STEP 3: Build Trade Data Structure
    # ========================================================================
    print("=" * 80)
    print("STEP 3: BUILDING TRADE DATA")
    print("=" * 80)
    print()
    
    trade_data = (
        order_id,  # orderId
        Web3.to_checksum_address(party1_account.address),  # party1
        Web3.to_checksum_address(party2_account.address),  # party2
        Web3.to_checksum_address(CONFIG["PARTY1_RECEIVE_WALLET"] or party1_account.address),  # party1ReceiveWallet
        Web3.to_checksum_address(CONFIG["PARTY2_RECEIVE_WALLET"] or party2_account.address),  # party2ReceiveWallet
        Web3.to_checksum_address(CONFIG["HEDERA_BASE_TOKEN"]),  # baseAsset
        Web3.to_checksum_address(CONFIG["SEPOLIA_QUOTE_TOKEN"]),  # quoteAsset
        price,  # price
        quantity,  # quantity
        "ask",  # party1Side (seller)
        "bid",  # party2Side (buyer)
        CONFIG["HEDERA_CHAIN_ID"],  # sourceChainId
        CONFIG["SEPOLIA_CHAIN_ID"],  # destinationChainId
        int(time.time()),  # timestamp
        0,  # nonce1
        0   # nonce2
    )
    
    print("✅ Trade data structure built")
    print()
    
    # ========================================================================
    # STEP 4: Settle Source Chain (Hedera)
    # ========================================================================
    print("=" * 80)
    print("STEP 4: SETTLING SOURCE CHAIN (HEDERA)")
    print("=" * 80)
    print()
    
    settle_cross_chain_trade(
        hedera_w3,
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        trade_data,
        True,  # isSourceChain = True
        CONFIG["OWNER_PRIVATE_KEY"] or CONFIG["PARTY1_PRIVATE_KEY"],
        "Hedera"
    )
    
    check_settlement_status(
        hedera_w3,
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        order_id,
        "Hedera"
    )
    print()
    
    # ========================================================================
    # STEP 5: Settle Destination Chain (Sepolia)
    # ========================================================================
    print("=" * 80)
    print("STEP 5: SETTLING DESTINATION CHAIN (SEPOLIA)")
    print("=" * 80)
    print()
    
    settle_cross_chain_trade(
        sepolia_w3,
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        trade_data,
        False,  # isSourceChain = False
        CONFIG["OWNER_PRIVATE_KEY"] or CONFIG["PARTY2_PRIVATE_KEY"],
        "Sepolia"
    )
    
    check_settlement_status(
        sepolia_w3,
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        order_id,
        "Sepolia"
    )
    print()
    
    # ========================================================================
    # STEP 6: Final Status Check
    # ========================================================================
    print("=" * 80)
    print("FINAL STATUS CHECK")
    print("=" * 80)
    print()
    
    print("🔍 Checking final escrow balances...")
    print()
    
    print("Hedera - Party1:")
    check_escrow_balance(
        hedera_w3,
        CONFIG["HEDERA_SETTLEMENT_CONTRACT"],
        party1_account.address,
        CONFIG["HEDERA_BASE_TOKEN"],
        "Hedera"
    )
    print()
    
    print("Sepolia - Party2:")
    check_escrow_balance(
        sepolia_w3,
        CONFIG["SEPOLIA_SETTLEMENT_CONTRACT"],
        party2_account.address,
        CONFIG["SEPOLIA_QUOTE_TOKEN"],
        "Sepolia"
    )
    print()
    
    print("=" * 80)
    print("✅ CROSS-CHAIN SETTLEMENT COMPLETE!")
    print("=" * 80)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        traceback.print_exc()