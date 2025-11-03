"""
Cross-Chain Trade Settlement Client

This client handles all interactions with the Trade Settlement smart contract,
including escrow management, token approvals, signature creation, and trade settlement.
"""

from decimal import Decimal
from enum import Enum
import json
import os
from time import time
from pydantic.dataclasses import dataclass
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from typing import Dict, Optional

# from src import settlement ERC20_ABI, TRADE_SETTLEMENT_ABI

_THIS_DIR = os.path.dirname(__file__)
_ORDERBOOK_ROOT = os.path.abspath(os.path.join(_THIS_DIR, os.pardir))
_ABIS_DIR = os.path.join(_ORDERBOOK_ROOT, "abis")

with open(os.path.join(_ABIS_DIR, "ERC20_abi.json"), "r", encoding="utf-8") as f:
    ERC20_ABI = json.load(f)

with open(os.path.join(_ABIS_DIR, "settlement_abi.json"), "r", encoding="utf-8") as f:
    TRADE_SETTLEMENT_ABI = json.load(f)


class NetworkType(Enum):
    HEDERA = "hedera"
    ETHEREUM = "ethereum"


@dataclass
class CrossChainOrder:
    """Represents an order that can be matched cross-chain"""

    order_id: str
    account: str
    side: str  # 'bid' or 'ask'
    base_asset: str
    quote_asset: str
    price: Decimal
    quantity: Decimal
    from_network: str
    to_network: str
    receive_wallet: str
    timestamp: int
    network: NetworkType


@dataclass
class CrossChainMatch:
    """Represents a matched cross-chain trade"""

    source_order: CrossChainOrder
    dest_order: CrossChainOrder
    trade_id: str
    matched_quantity: Decimal
    matched_price: Decimal


class SettlementClientSame:
    """
    Client for interacting with the Cross-Chain Trade Settlement contract

    This client provides methods for:
    - Balance checking
    - Cross-chain trade settlement
    """

    def __init__(
        self,
        web3_provider: str,
        contract_address: str,
        private_key: Optional[str] = None,
    ):
        """
        Initialize the Settlement Client

        Args:
            web3_provider: RPC URL for the blockchain network
            contract_address: Address of the deployed settlement contract
            private_key: Private key for signing transactions (optional)
        """
        self.web3 = Web3(Web3.HTTPProvider(web3_provider))

        if not self.web3.is_connected():
            raise ConnectionError(f"Failed to connect to {web3_provider}")

        self.contract_address = Web3.to_checksum_address(contract_address)
        self.contract = self.web3.eth.contract(
            address=self.contract_address, abi=TRADE_SETTLEMENT_ABI
        )

        self.account = Account.from_key(private_key) if private_key else None

        print(f"✅ Connected to network (Chain ID: {self.web3.eth.chain_id})")
        if self.account:
            print(f"✅ Account loaded: {self.account.address}")

    # ==================== ESCROW MANAGEMENT ====================

    def check_escrow_balance(
        self, user_address: str, token_address: str, token_decimals: int = 18
    ) -> Dict:
        """
        Check escrow balance for a user

        Args:
            user_address: Address of the user
            token_address: Address of the token
            token_decimals: Token decimals

        Returns:
            Dictionary with total, available, and locked balances
        """
        try:
            user_address = Web3.to_checksum_address(user_address)
            token_address = Web3.to_checksum_address(token_address)

            total, available, locked = self.contract.functions.checkEscrowBalance(
                user_address, token_address
            ).call()

            divisor = 10**token_decimals

            return {
                "total": total / divisor,
                "total_wei": total,
                "available": available / divisor,
                "available_wei": available,
                "locked": locked / divisor,
                "locked_wei": locked,
                "user": user_address,
                "token": token_address,
            }

        except Exception as e:
            print(f"❌ Error checking balance: {e}")
            return {"error": str(e)}

    # ==================== TOKEN OPERATIONS ====================

    def get_token_decimals(self, token_address: str) -> int:
        """
        Read ERC20 token decimals from chain. Falls back to 18 on error.
        """
        try:
            token_contract = self.web3.eth.contract(
                address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
            )
            return int(token_contract.functions.decimals().call())
        except Exception:
            return 18

    # ==================== NONCE MANAGEMENT ====================

    def get_user_nonce(self, user_address: str, token_address: str) -> int:
        """
        Get user's current nonce for a specific token

        Args:
            user_address: User's address
            token_address: Token address

        Returns:
            Current nonce value
        """
        try:
            nonce = self.contract.functions.getUserNonce(
                Web3.to_checksum_address(user_address),
                Web3.to_checksum_address(token_address),
            ).call()

            return nonce

        except Exception as e:
            print(f"❌ Error getting nonce: {e}")
            return 0

    # ==================== TRADE SETTLEMENT ====================

    def settle_same_chain_trade(
        self,
        order_id: str,
        party1: str,
        party2: str,
        party1_receive_wallet: str,
        party2_receive_wallet: str,
        base_asset: str,
        quote_asset: str,
        price: float,
        quantity: float,
        party1_side: str,
        party2_side: str,
        source_chain_id: int,
        destination_chain_id: int,
        timestamp: int,
        nonce1: int,
        nonce2: int,
        price_decimals: int = 18,
        quantity_decimals: int = 18,
        gas_price_gwei: int = 20,
    ) -> Dict:
        if not self.account:
            raise ValueError("No private key provided for transaction signing")

        try:
            if isinstance(order_id, str):
                if order_id.startswith("0x"):
                    order_id_bytes = bytes.fromhex(order_id[2:].zfill(64))
                else:
                    order_id_bytes = Web3.keccak(text=order_id)
            else:
                order_id_bytes = order_id

            price_wei = int(price * (10**price_decimals))
            quantity_wei = int(quantity * (10**quantity_decimals))

            trade_data = (
                order_id_bytes,
                Web3.to_checksum_address(party1),
                Web3.to_checksum_address(party2),
                Web3.to_checksum_address(party1_receive_wallet),
                Web3.to_checksum_address(party2_receive_wallet),
                Web3.to_checksum_address(base_asset),
                Web3.to_checksum_address(quote_asset),
                price_wei,
                quantity_wei,
                party1_side,
                party2_side,
                source_chain_id,
                destination_chain_id,
                timestamp,
                nonce1,
                nonce2,
            )

            function = self.contract.functions.settleSameChainTrade(trade_data)

            # Diagnostics
            diagnostics = {}
            try:
                total_b, avail_b, locked_b = self.contract.functions.checkEscrowBalance(
                    Web3.to_checksum_address(party1),
                    Web3.to_checksum_address(base_asset),
                ).call()
                diagnostics["party1_base"] = {
                    "total": int(total_b),
                    "available": int(avail_b),
                    "locked": int(locked_b),
                }
            except Exception:
                pass
            try:
                total_q, avail_q, locked_q = self.contract.functions.checkEscrowBalance(
                    Web3.to_checksum_address(party2),
                    Web3.to_checksum_address(quote_asset),
                ).call()
                diagnostics["party2_quote"] = {
                    "total": int(total_q),
                    "available": int(avail_q),
                    "locked": int(locked_q),
                }
            except Exception:
                pass

            # Preflight
            try:
                function.call({"from": self.account.address})
            except Exception as pre_err:
                return {
                    "success": False,
                    "error": f"preflight_revert: {pre_err}",
                    "diagnostics": diagnostics,
                }

            # Gas
            try:
                gas_estimate = function.estimate_gas({"from": self.account.address})
                gas_limit = int(gas_estimate * 1.3)
            except Exception:
                gas_limit = 700000

            # Build EIP-1559 transaction when supported (Hedera requires it on Hashio);
            # fallback to legacy gasPrice if baseFeePerGas is unavailable
            tx_common = {
                "from": self.account.address,
                "nonce": self.web3.eth.get_transaction_count(self.account.address),
                "gas": gas_limit,
                "chainId": self.web3.eth.chain_id,
            }

            try:
                latest_block = self.web3.eth.get_block("latest")
                base_fee = latest_block.get("baseFeePerGas")
            except Exception:
                base_fee = None

            if base_fee is not None:
                # Use conservative fee caps
                max_priority = self.web3.to_wei(1, "gwei")
                max_fee = int(base_fee) + int(max_priority) * 2
                tx = function.build_transaction(
                    {
                        **tx_common,
                        "maxFeePerGas": max_fee,
                        "maxPriorityFeePerGas": max_priority,
                    }
                )
            else:
                tx = function.build_transaction(
                    {
                        **tx_common,
                        "gasPrice": self.web3.to_wei(gas_price_gwei, "gwei"),
                    }
                )

            signed_tx = self.web3.eth.account.sign_transaction(tx, self.account.key)
            try:
                tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
            except Exception as send_err:
                err_msg = str(send_err)
                # Attempt to surface revert reason via static call
                try:
                    function.call({"from": self.account.address})
                except Exception as detail_err:
                    err_msg = f"{err_msg} | detail: {detail_err}"
                return {"success": False, "error": err_msg, "diagnostics": diagnostics}

            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

            return {
                "success": receipt.status == 1,
                "transaction_hash": receipt.transactionHash.hex(),
                "gas_used": receipt.gasUsed,
                "block_number": receipt.blockNumber,
                "diagnostics": diagnostics,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==================== VERIFICATION METHODS ====================

    def verify_trade_signature(
        self,
        signer: str,
        order_id: str,
        base_asset: str,
        quote_asset: str,
        price: float,
        quantity: float,
        side: str,
        receive_wallet: str,
        source_chain_id: int,
        destination_chain_id: int,
        timestamp: int,
        nonce: int,
        signature: str,
        price_decimals: int = 18,
        quantity_decimals: int = 18,
    ) -> bool:
        """
        Verify a trade signature on-chain

        Args:
            signer: Expected signer address
            order_id: Order identifier
            base_asset: Base asset address
            quote_asset: Quote asset address
            price: Trade price
            quantity: Trade quantity
            side: "bid" or "ask"
            receive_wallet: Receiving wallet address
            source_chain_id: Source chain ID
            destination_chain_id: Destination chain ID
            timestamp: Timestamp
            nonce: Nonce
            signature: Signature to verify
            price_decimals: Price decimals
            quantity_decimals: Quantity decimals

        Returns:
            True if signature is valid, False otherwise
        """
        try:
            # Convert order_id to bytes32
            if isinstance(order_id, str):
                if order_id.startswith("0x"):
                    order_id_bytes = bytes.fromhex(order_id[2:].zfill(64))
                else:
                    order_id_bytes = Web3.keccak(text=order_id)
            else:
                order_id_bytes = order_id

            price_wei = int(price * (10**price_decimals))
            quantity_wei = int(quantity * (10**quantity_decimals))
            sig_bytes = bytes.fromhex(signature.replace("0x", ""))

            result = self.contract.functions.verifyCrossChainTradeSignature(
                Web3.to_checksum_address(signer),
                order_id_bytes,
                Web3.to_checksum_address(base_asset),
                Web3.to_checksum_address(quote_asset),
                price_wei,
                quantity_wei,
                side,
                Web3.to_checksum_address(receive_wallet),
                source_chain_id,
                destination_chain_id,
                timestamp,
                nonce,
                sig_bytes,
            ).call()

            return result

        except Exception as e:
            print(f"❌ Error verifying signature: {e}")
            return False

    def check_trade_settled(self, order_id: str) -> bool:
        """
        Check if a trade has been settled on this chain

        Args:
            order_id: Order identifier

        Returns:
            True if settled, False otherwise
        """
        try:
            if isinstance(order_id, str):
                if order_id.startswith("0x"):
                    order_id_bytes = bytes.fromhex(order_id[2:].zfill(64))
                else:
                    order_id_bytes = Web3.keccak(text=order_id)
            else:
                order_id_bytes = order_id

            settled = self.contract.functions.settledCrossChainOrders(
                order_id_bytes
            ).call()

            return settled

        except Exception as e:
            print(f"❌ Error checking settlement status: {e}")
            return False

    # ==================== UTILITY METHODS ====================

    def get_contract_owner(self) -> str:
        """Get the contract owner address"""
        try:
            return self.contract.functions.owner().call()
        except Exception as e:
            print(f"❌ Error getting owner: {e}")
            return ""

    def get_signer_address(self) -> str:
        """Return the address of the signer (matching engine) if loaded"""
        try:
            return self.account.address if self.account else ""
        except Exception:
            return ""

    def display_account_info(self):
        """Display current account information"""
        if not self.account:
            print("⚠️  No account loaded")
            return

        print(f"\n{'='*60}")
        print(f"ACCOUNT INFORMATION")
        print(f"{'='*60}")
        print(f"Address: {self.account.address}")
        print(f"Chain ID: {self.web3.eth.chain_id}")
        print(f"Contract: {self.contract_address}")

        try:
            balance = self.web3.eth.get_balance(self.account.address)
            print(f"Native Balance: {self.web3.from_wei(balance, 'ether')} ETH")
        except:
            pass

        print(f"{'='*60}\n")


class SettlementClientCross:

    def __init__(self, blockchain_rpc, private_key) -> None:
        print(f"rpc {blockchain_rpc}, key {private_key}")
        if not blockchain_rpc:
            return "Blockchain RPC not provided"
        self.web3 = Web3(Web3.HTTPProvider(blockchain_rpc))
        if not self.web3.is_connected():
            raise ConnectionError(f"Failed to connect to {blockchain_rpc}")

        self.account = Account.from_key(private_key)
        self.private_key = private_key

    def check_escrow_balance(
        self, settlement_address, user_address, token_address, chain_name
    ):
        try:
            """Check escrow balance"""
            if self.web3.is_connected():
                print("its connected! ")
            settlement_contract = self.web3.eth.contract(
                address=Web3.to_checksum_address(settlement_address),
                abi=TRADE_SETTLEMENT_ABI,
            )
            print(settlement_contract, user_address, token_address, "CHECK")
            total, available, locked = settlement_contract.functions.checkEscrowBalance(
                Web3.to_checksum_address(user_address),
                Web3.to_checksum_address(token_address),
            ).call()

            print(f"📊 Escrow Balance on {chain_name}:")
            print(f"   Total: {total}")
            print(f"   Available: {available}")
            print(f"   Locked: {locked}")

            return total, available, locked

        except Exception as error:
            print(f"error {error}")

    def settle_cross_chain_trade(
        self,
        settlement_address,
        trade_data,
        is_source_chain,
        private_key,
        chain_name,
    ):
        """Settle cross-chain trade"""
        account = Account.from_key(private_key)
        settlement_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(settlement_address),
            abi=TRADE_SETTLEMENT_ABI,
        )

        chain_type = "SOURCE" if is_source_chain else "DESTINATION"
        print(f"🔄 Settling {chain_type} chain on {chain_name}...")

        nonce = self.web3.eth.get_transaction_count(account.address)

        # Convert dictionary to tuple in exact order matching the struct
        trade_tuple = (
            trade_data["orderId"],  # bytes32
            trade_data["party1"],  # address
            trade_data["party2"],  # address
            trade_data["party1ReceiveWallet"],  # address
            trade_data["party2ReceiveWallet"],  # address
            trade_data["baseAsset"],  # address
            trade_data["quoteAsset"],  # address
            trade_data["price"],  # uint256
            trade_data["quantity"],  # uint256
            trade_data["party1Side"],  # string
            trade_data["party2Side"],  # string
            trade_data["sourceChainId"],  # uint256
            trade_data["destinationChainId"],  # uint256
            trade_data["timestamp"],  # uint256
            trade_data["nonce1"],  # uint256
            trade_data["nonce2"],  # uint256
        )

        tx = settlement_contract.functions.settleCrossChainTrade(
            trade_tuple, is_source_chain
        ).build_transaction(
            {
                "from": account.address,
                "gas": 500000,
                "gasPrice": self.web3.eth.gas_price,
                "nonce": nonce,
                "chainId": self.web3.eth.chain_id,
            }
        )

        signed_tx = self.web3.eth.account.sign_transaction(tx, private_key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)

        return self.wait_for_tx(tx_hash, chain_name)

    def wait_for_tx(self, tx_hash, chain_name):
        """Wait for transaction confirmation"""
        print(f"⏳ Waiting for transaction on {chain_name}: {tx_hash.hex()}")
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status == 1:
            print(f"✅ Transaction confirmed on {chain_name}")
        else:
            print(f"❌ Transaction failed on {chain_name}")
        return {
            "transactionHash": tx_hash.hex(),
            "blockNumber": receipt.blockNumber,
            "status": receipt.status,
            "gasUsed": receipt.gasUsed,
        }

    def _prepare_trade_data(self, match: CrossChainMatch, supported_networks) -> Dict:
        """Prepare trade data structure for settlement"""
        return {
            "orderId": f"0x{match.trade_id}",  # Convert to bytes32
            "party1": match.source_order.account,
            "party2": match.dest_order.account,
            "party1ReceiveWallet": match.source_order.receive_wallet,
            "party2ReceiveWallet": match.dest_order.receive_wallet,
            "baseAsset": match.source_order.base_asset,  # Token address on source chain
            "quoteAsset": match.dest_order.quote_asset,  # Token address on dest chain
            "price": int(match.matched_price * Decimal("1e18")),  # Convert to wei
            "quantity": int(match.matched_quantity * Decimal("1e18")),  # Convert to wei
            "party1Side": match.source_order.side,
            "party2Side": match.dest_order.side,
            "sourceChainId": supported_networks[match.source_order.network]["chain_id"],
            "destinationChainId": supported_networks[match.dest_order.network][
                "chain_id"
            ],
            "timestamp": int(time.time()),
            "nonce1": 0,  # Get from settlement contract
            "nonce2": 0,  # Get from settlement contract
        }
