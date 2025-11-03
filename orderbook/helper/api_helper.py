from fastapi import HTTPException, Request
import json
from dotenv import load_dotenv
import asyncio
import logging
import time
import hashlib

from src.trade_settlement_client import SettlementClientSame

# Configure logging
root = logging.getLogger()
if root.handlers:
    root.handlers.clear()

LOG_FORMAT = (
    "%(asctime)s %(levelname)s " "[%(filename)s:%(lineno)d %(funcName)s] " "%(message)s"
)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)
load_dotenv()


class APIHelper:

    @staticmethod
    def get_token_address(symbol: str, network_key: str, SUPPORTED_NETWORKS: dict, TOKEN_ADDRESSES: dict | None = None) -> str:
        """Resolve token address by symbol and network key, fallback to legacy mapping."""
        symbol_up = symbol.upper()
        try:
            net = SUPPORTED_NETWORKS.get(network_key) or {}
            tokens = net.get("tokens") or {}
            addr = tokens.get(symbol_up)
            if addr:
                print(addr, "TOKEN_ADDRESS")
                return addr
        except Exception:
            pass
        # Fallback to legacy map with network-specific keys first (e.g., USDT_ETH, XZAR_ETH)
        if TOKEN_ADDRESSES:
            network_suffix = (network_key or "").upper()
            if network_suffix:
                key_with_net = f"{symbol_up}_{network_suffix}"
                if key_with_net in TOKEN_ADDRESSES:
                    token_address = TOKEN_ADDRESSES[key_with_net]
                    print(token_address, "TOKEN_ADDRESS")
                    return token_address
            # Only allow generic fallback for Hedera to avoid cross-chain mis-resolution
            if (network_key or "").lower() == "hedera":
                token_address = TOKEN_ADDRESSES.get(symbol_up, "")
                print(token_address, "TOKEN_ADDRESS")
                return token_address or ""
        return ""

    @staticmethod
    def load_abi(abi_path):
        """Load ABI from relative path and return it"""
        with open(abi_path, "r") as f:
            data = json.load(f)
        return data["abi"] if isinstance(data, dict) and "abi" in data else data

    @staticmethod
    async def validate_order_prerequisites(
        order_data: dict,
        SUPPORTED_NETWORKS: dict,
        TOKEN_ADDRESSES: dict,
        PRIVATE_KEY: str,
    ) -> dict:
        """
        Validate that user has sufficient escrow balance and locked funds for the order
        """
        results = {"valid": True, "errors": [], "checks": {}}

        try:
            account = order_data["account"]
            side = order_data["side"]
            quantity = float(order_data["quantity"])
            price = float(order_data["price"])

            # Resolve networks
            from_network = (order_data.get("from_network") or order_data.get("fromNetwork") or "").lower()
            to_network = (order_data.get("to_network") or order_data.get("toNetwork") or "").lower()
   

            # Get token addresses for each network
            base_asset_from = APIHelper.get_token_address(order_data["baseAsset"], from_network, SUPPORTED_NETWORKS, TOKEN_ADDRESSES)
            # For cross-chain bids we must validate quote token on the FROM network (escrow always on from_network)
            quote_asset_from = APIHelper.get_token_address(order_data["quoteAsset"], from_network, SUPPORTED_NETWORKS, TOKEN_ADDRESSES)
            print(f"base_asset_from: {base_asset_from} quote_asset_from: {quote_asset_from}")
            if side.lower() == "ask":
                # Seller must have base asset escrow on FROM network
                required_amount = quantity
                token_to_check = base_asset_from
                network_key = from_network
            else:
                # Buyer must have quote asset escrow on FROM network
                required_amount = quantity * price
                token_to_check = quote_asset_from
                network_key = from_network

            # Validate token configuration exists on selected network
            if not token_to_check or not str(token_to_check).startswith("0x") or len(str(token_to_check)) != 42:
                results["valid"] = False
                results["errors"].append(
                    f"Token {order_data['baseAsset'] if side.lower()=='ask' else order_data['quoteAsset']} not configured on {network_key}"
                )
                return results

            # Create a temporary client for the correct chain
            net_cfg = SUPPORTED_NETWORKS.get(network_key) or {}
            client = SettlementClientSame(
                net_cfg.get("rpc"),
                net_cfg.get("contract_address"),
                PRIVATE_KEY,
            )

            # Determine correct token decimals for accurate normalization with retries and sensible fallback
            symbol_for_decimals = order_data["baseAsset"] if side.lower() == "ask" else order_data["quoteAsset"]
            default_decimals_map = {"USDT": 6, "HBAR": 18}
            token_decimals = default_decimals_map.get(symbol_for_decimals.upper(), 18)
            for attempt in range(3):
                try:
                    token_decimals = client.get_token_decimals(token_to_check)
                    break
                except Exception:
                    if attempt == 2:
                        break
                    await asyncio.sleep(0.5 * (attempt + 1))

            # Check escrow balance on that chain with proper decimals, retry on transient RPC errors (e.g., 429)
            balance_info = {}
            for attempt in range(4):
                try:
                    balance_info = client.check_escrow_balance(account, token_to_check, token_decimals=token_decimals)
                    if "error" not in balance_info:
                        break
                except Exception as e:
                    balance_info = {"error": str(e)}
                await asyncio.sleep(0.5 * (attempt + 1))

            available = balance_info.get("available", 0)

            results["checks"] = {
                "account": account,
                "side": side,
                "token": token_to_check,
                "required_amount": required_amount,
                "available_escrow": available,
                "total_escrow": balance_info.get("total", 0),
                "locked_escrow": balance_info.get("locked", 0),
                "network_key": network_key,
                "rpc": net_cfg.get("rpc"),
                "contract_address": net_cfg.get("contract_address"),
                "token_decimals": token_decimals,
            }

            if available < required_amount:
                results["valid"] = False
                results["errors"].append(
                    f"Insufficient available escrow balance. Required: {required_amount}, Available: {available}"
                )

            return results

        except Exception as e:
            logger.error(f"Error validating prerequisites: {e}")
            results["valid"] = False
            results["errors"].append(f"Validation error: {str(e)}")
            return results

    @staticmethod
    def create_trade_signature_for_user(
        party_addr: str,
        order_id: int,
        base_asset: str,
        quote_asset: str,
        price: int,
        quantity: int,
        side: str,
        timestamp: int,
        nonce: int,
        settlement_client,
    ) -> str:
        """Create a signature for a party (in production, this would be done client-side)"""
        try:
            return settlement_client.create_trade_signature(
                party_addr,
                order_id,
                base_asset,
                quote_asset,
                price,
                quantity,
                side,
                timestamp,
                nonce,
            )
        except Exception as e:
            logger.error(f"Error creating signature: {e}")
            return ""

    @staticmethod
    async def settle_trades_if_any(
        order_dict: dict,
        SUPPORTED_NETWORKS: dict,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS: str,
        CONTRACT_ABI: list,
        PRIVATE_KEY: str,
        TOKEN_ADDRESSES: dict,
        settlement_client: SettlementClientSame,
        REQUIRE_CLIENT_SIGNATURES: bool = False,
    ) -> dict:
        """
        Settle cross-chain trades using the new settlement contract.
        Handles both source and destination chain settlements.
        """
        start_ts = time.time()
        req_id = order_dict.get("request_id") or f"req_{int(start_ts*1000)}"
        logger.info(f"[{req_id}] Settlement start | trades={len(order_dict.get('trades') or [])} base={order_dict.get('baseAsset')} quote={order_dict.get('quoteAsset')}")
        
        if not order_dict.get("trades"):
            logger.info(f"[{req_id}] No trades to settle")
            return {"settled": False, "reason": "No trades to settle"}

        settlement_results = []

        try:
            for idx, trade in enumerate(order_dict["trades"]):
                t0 = time.time()
                logger.info(f"[{req_id}] Trade[{idx}] start | price={trade.get('price')} qty={trade.get('quantity')}")
                
                # Extract party information
                party1_addr = trade["party1"][0]
                party1_side = trade["party1"][1]
                party1_from_network = trade["party1"][5] if len(trade["party1"]) > 5 else None
                party1_to_network = trade["party1"][6] if len(trade["party1"]) > 6 else None
                party1_receive_wallet = trade["party1"][7] if len(trade["party1"]) > 7 else party1_addr

                party2_addr = trade["party2"][0]
                party2_side = trade["party2"][1]
                party2_from_network = trade["party2"][5] if len(trade["party2"]) > 5 else None
                party2_to_network = trade["party2"][6] if len(trade["party2"]) > 6 else None
                party2_receive_wallet = trade["party2"][7] if len(trade["party2"]) > 7 else party2_addr

                # Normalize roles so that party1 is always ASK (seller of base) on the SOURCE chain,
                # and party2 is always BID (buyer with quote) on the DESTINATION chain.
                ask_is_p1 = str(party1_side).lower() == "ask" and str(party2_side).lower() == "bid"
                bid_is_p1 = str(party1_side).lower() == "bid" and str(party2_side).lower() == "ask"
                
                if not (ask_is_p1 or bid_is_p1):
                    settlement_results.append({
                        "trade": trade,
                        "settlement_result": {"success": False, "error": "invalid_sides"},
                    })
                    continue

                if ask_is_p1:
                    ask_addr, ask_from_network, ask_to_network, ask_recv = party1_addr, party1_from_network, party1_to_network, party1_receive_wallet
                    bid_addr, bid_from_network, bid_to_network, bid_recv = party2_addr, party2_from_network, party2_to_network, party2_receive_wallet
                else:  # bid_is_p1
                    ask_addr, ask_from_network, ask_to_network, ask_recv = party2_addr, party2_from_network, party2_to_network, party2_receive_wallet
                    bid_addr, bid_from_network, bid_to_network, bid_recv = party1_addr, party1_from_network, party1_to_network, party1_receive_wallet

                # Resolve network configurations using normalized roles
                source_network_cfg = SUPPORTED_NETWORKS.get(ask_from_network)
                dest_network_cfg = SUPPORTED_NETWORKS.get(bid_from_network)

                logger.info(f"[{req_id}] Trade[{idx}] networks | source={ask_from_network} dest={bid_from_network}")

                if not source_network_cfg or not dest_network_cfg:
                    settlement_results.append({
                        "trade": trade,
                        "settlement_result": {
                            "success": False,
                            "error": "Network configuration not found"
                        }
                    })
                    logger.warning(f"[{req_id}] Trade[{idx}] missing network configuration")
                    continue

                # Get contract addresses and RPCs
                source_rpc = source_network_cfg.get("rpc")
                dest_rpc = dest_network_cfg.get("rpc")
                source_contract = source_network_cfg.get("contract_address", TRADE_SETTLEMENT_CONTRACT_ADDRESS)
                dest_contract = dest_network_cfg.get("contract_address", TRADE_SETTLEMENT_CONTRACT_ADDRESS)
                source_chain_id = source_network_cfg.get("chain_id")
                dest_chain_id = dest_network_cfg.get("chain_id")

                # Create clients for both chains (using matching engine key)
                client_source = SettlementClientSame(source_rpc, source_contract, PRIVATE_KEY)
                client_dest = SettlementClientSame(dest_rpc, dest_contract, PRIVATE_KEY)
                
                logger.info(f"[{req_id}] Trade[{idx}] clients ready | source_chain_id={source_chain_id} dest_chain_id={dest_chain_id}")

                # Check owner vs signer (must be owner for onlyOwner functions)
                try:
                    owner_addr = client_source.get_contract_owner()
                    signer_addr = client_source.get_signer_address()
                    if owner_addr and signer_addr and owner_addr.lower() != signer_addr.lower():
                        logger.warning(f"[{req_id}] Trade[{idx}] signer is not contract owner | owner={owner_addr} signer={signer_addr}")
                        settlement_results.append({
                            "trade": trade,
                            "settlement_result": {
                                "success": False,
                                "error": "signer_not_owner",
                                "details": {"owner": owner_addr, "signer": signer_addr},
                            }
                        })
                        logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                        continue
                except Exception:
                    pass

                # Get token addresses for the source chain (ASK side network)
                base_token_src = APIHelper.get_token_address(
                    order_dict["baseAsset"],
                    ask_from_network,
                    SUPPORTED_NETWORKS,
                    TOKEN_ADDRESSES,
                )
                quote_token_src = APIHelper.get_token_address(
                    order_dict["quoteAsset"],
                    ask_from_network,
                    SUPPORTED_NETWORKS,
                    TOKEN_ADDRESSES,
                )
                # Get token addresses for the destination chain (BID side network)
                base_token_dest = APIHelper.get_token_address(
                    order_dict["baseAsset"],
                    bid_from_network,
                    SUPPORTED_NETWORKS,
                    TOKEN_ADDRESSES,
                )
                quote_token_dest = APIHelper.get_token_address(
                    order_dict["quoteAsset"],
                    bid_from_network,
                    SUPPORTED_NETWORKS,
                    TOKEN_ADDRESSES,
                )

                # Validate ASK party has base tokens on source chain
                try:
                    ask_base_check = client_source.check_escrow_balance(
                        ask_addr, 
                        base_token_src, 
                        token_decimals=18
                    )
                    ask_base_avail = ask_base_check.get("available", 0)
                    ask_base_needed = float(trade["quantity"])
                    
                    if ask_base_avail < ask_base_needed:
                        logger.error(
                            f"[{req_id}] Trade[{idx}] ASK party insufficient escrow | "
                            f"addr={ask_addr} needs {ask_base_needed} base but has {ask_base_avail} on {ask_from_network}"
                        )
                        settlement_results.append({
                            "trade": trade,
                            "settlement_result": {
                                "success": False,
                                "error": "insufficient_ask_base_escrow",
                                "details": {"required": ask_base_needed, "available": ask_base_avail}
                            }
                        })
                        logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                        continue
                except Exception as e:
                    logger.warning(f"[{req_id}] ASK escrow check failed: {e}")

                # Validate BID party has quote tokens on destination chain
                try:
                    bid_quote_check = client_dest.check_escrow_balance(
                        bid_addr, 
                        quote_token_dest, 
                        token_decimals=18
                    )
                    bid_quote_avail = bid_quote_check.get("available", 0)
                    bid_quote_needed = float(trade["quantity"]) * float(trade["price"])
                    
                    if bid_quote_avail < bid_quote_needed:
                        logger.error(
                            f"[{req_id}] Trade[{idx}] BID party insufficient escrow | "
                            f"addr={bid_addr} needs {bid_quote_needed} quote but has {bid_quote_avail} on {bid_from_network}"
                        )
                        settlement_results.append({
                            "trade": trade,
                            "settlement_result": {
                                "success": False,
                                "error": "insufficient_bid_quote_escrow",
                                "details": {"required": bid_quote_needed, "available": bid_quote_avail}
                            }
                        })
                        logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                        continue
                except Exception as e:
                    logger.warning(f"[{req_id}] BID escrow check failed: {e}")

                # Get nonces with basic retry/backoff to handle rate limits
                async def _retry_get_nonce(client, user, token, attempts=3):
                    last = 0
                    for a in range(attempts):
                        try:
                            val = client.get_user_nonce(user, token)
                            return val
                        except Exception:
                            last = 0
                            await asyncio.sleep(0.5 * (a + 1))
                    return last

                # Nonces: source (ask) uses base token on source; destination (bid) uses quote token on destination
                nonce1 = await _retry_get_nonce(client_source, ask_addr, base_token_src)
                nonce2 = await _retry_get_nonce(client_dest, bid_addr, quote_token_dest)
                logger.info(f"[{req_id}] Trade[{idx}] nonces | source_base_nonce={nonce1} dest_quote_nonce={nonce2}")

                # Trade parameters - Create UNIQUE order ID to prevent replay attacks
                base_order_id = str(order_dict["orderId"]).strip()
                timestamp = int(trade["timestamp"])
                # Generate unique order ID using hash of base_order_id + timestamp + trade index
                order_id_hash = hashlib.sha256(f"{base_order_id}:{timestamp}:{idx}".encode()).hexdigest()
                order_id = order_id_hash[:32]  # Use first 32 chars of hash
                
                price = float(trade["price"])
                quantity = float(trade["quantity"])

                same_chain = source_chain_id == dest_chain_id
                
                if same_chain:
                    # Same-chain settlement
                    party1_addr, party2_addr = ask_addr, bid_addr
                    party1_side, party2_side = "ask", "bid"
                    party1_receive_wallet, party2_receive_wallet = ask_recv, bid_recv
                    
                    logger.info(
                        f"[{req_id}] Trade[{idx}] same-chain settlement on chain_id={source_chain_id} | "
                        f"party1={party1_addr} side={party1_side} party2={party2_addr} side={party2_side}"
                    )

                    # Preflight escrow checks for clearer errors
                    try:
                        base_needed = float(quantity)
                        quote_needed = float(quantity) * float(price)
                        base_check = client_source.check_escrow_balance(ask_addr, base_token_src, token_decimals=18)
                        quote_check = client_source.check_escrow_balance(bid_addr, quote_token_src, token_decimals=18)
                        base_avail = base_check.get("available", 0)
                        quote_avail = quote_check.get("available", 0)
                        
                        if base_avail < base_needed:
                            settlement_results.append({
                                "trade": trade,
                                "settlement_result": {
                                    "success": False,
                                    "error": "insufficient_escrow_base_same_chain",
                                    "details": {"available": base_avail, "required": base_needed},
                                }
                            })
                            logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                            continue
                        
                        if quote_avail < quote_needed:
                            settlement_results.append({
                                "trade": trade,
                                "settlement_result": {
                                    "success": False,
                                    "error": "insufficient_escrow_quote_same_chain",
                                    "details": {"available": quote_avail, "required": quote_needed},
                                }
                            })
                            logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                            continue
                    except Exception:
                        pass

                    result_same = client_source.settle_same_chain_trade(
                        order_id, party1_addr, party2_addr,
                        party1_receive_wallet, party2_receive_wallet,
                        base_token_src, quote_token_src, price, quantity,
                        party1_side, party2_side,
                        source_chain_id, dest_chain_id,
                        timestamp, nonce1, nonce2,
                    )
                    
                    if not result_same.get("success"):
                        logger.error(f"[{req_id}] Same-chain settlement failed | error={result_same.get('error')} diag={result_same.get('diagnostics')} chain={source_chain_id}")
                    
                    result_source = result_same
                    result_dest = {"success": True, "skipped": True, "reason": "same_chain_atomic"}
                    
                else:
                    # Cross-chain settlement
                    # STEP 1: Check matching engine has sufficient HBAR on destination chain BEFORE attempting settlement
                    logger.info(f"[{req_id}] Trade[{idx}] checking HBAR balance on destination chain")
                    # try:
                    #     from web3 import Web3
                    #     w3_dest = Web3(Web3.HTTPProvider(dest_rpc))
                    #     engine_addr = client_dest.get_signer_address()
                    #     engine_balance = w3_dest.eth.get_balance(engine_addr)
                    #     min_required = w3_dest.to_wei(0.5, 'ether')  # 0.5 HBAR minimum
                        
                    #     balance_hbar = float(w3_dest.from_wei(engine_balance, 'ether'))
                    #     logger.info(f"[{req_id}] Trade[{idx}] matching engine HBAR balance | balance={balance_hbar} HBAR chain={dest_chain_id}")
                        
                    #     if engine_balance < min_required:
                    #         logger.error(
                    #             f"[{req_id}] Trade[{idx}] INSUFFICIENT HBAR for transaction fees | "
                    #             f"have={balance_hbar} HBAR, need=0.5+ HBAR on chain={dest_chain_id}"
                    #         )
                    #         settlement_results.append({
                    #             "trade": trade,
                    #             "settlement_result": {
                    #                 "success": False,
                    #                 "error": "insufficient_hbar_for_fees",
                    #                 "details": {
                    #                     "balance_hbar": balance_hbar,
                    #                     "required_hbar": 0.5,
                    #                     "chain_id": dest_chain_id,
                    #                     "engine_address": engine_addr
                    #                 }
                    #             }
                    #         })
                    #         logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                    #         continue
                    # except Exception as e:
                    #     logger.warning(f"[{req_id}] Trade[{idx}] could not verify HBAR balance: {e}")
                        # Continue anyway - let the transaction fail with proper error if insufficient
                    
                    # STEP 2: Settle on source chain (contract auto-locks)
                    logger.info(f"[{req_id}] Trade[{idx}] settling on source chain | chain_id={source_chain_id}")
                    logger.info(
                        f"[{req_id}] Trade[{idx}] source params | "
                        f"party1={ask_addr} side=ask party2={bid_addr} side=bid "
                        f"order_id={order_id}"
                    )

                    print(f"order_id: {order_id}")
                    print(f"ask_addr: {ask_addr}")
                    print(f"bid_addr: {bid_addr}")
                    print(f"ask_recv: {ask_recv}")
                    print(f"bid_recv: {bid_recv}")
                    print(f"base_token_src: {base_token_src}")
                    print(f"quote_token_src: {quote_token_dest}")
                    print(f"price: {price}")
                    print(f"quantity: {quantity}")
                    print(f"party1_side: ask")
                    print(f"party2_side: bid")
                    print(f"source_chain_id: {source_chain_id}")
                    print(f"dest_chain_id: {dest_chain_id}")
                    print(f"timestamp: {timestamp}")
                    print(f"nonce1: {nonce1}")
                    print(f"nonce2: {nonce2}")
                    print(f"is_source_chain: True")
                    print("================================================")

                    result_source = client_source.settle_cross_chain_trade(
                        order_id, ask_addr, bid_addr,
                        ask_recv, bid_recv,
                        base_token_src, quote_token_dest, price, quantity,
                        "ask", "bid",
                        source_chain_id, dest_chain_id,
                        timestamp, nonce1, nonce2,
                        is_source_chain=True
                    )

                    if not result_source.get("success"):
                        logger.error(
                            f"[{req_id}] Trade[{idx}] source-chain settlement FAILED | "
                            f"error={result_source.get('error')} "
                            f"diag={result_source.get('diagnostics')} "
                            f"chain={source_chain_id}"
                        )
                        # Don't proceed to destination if source failed
                        settlement_results.append({
                            "trade": trade,
                            "settlement_result": {
                                "success": False,
                                "source_chain": result_source,
                                "destination_chain": {"success": False, "skipped": True, "reason": "source_failed"}
                            }
                        })
                        logger.info(f"[{req_id}] Trade[{idx}] done | ok_source=False ok_dest=False elapsed={time.time()-t0:.2f}s")
                        continue

                    # STEP 3: Settle on destination chain (contract auto-locks)
                    logger.info(f"[{req_id}] Trade[{idx}] settling on destination chain | chain_id={dest_chain_id}")

                    result_dest = client_dest.settle_cross_chain_trade(
                        order_id, ask_addr, bid_addr,
                        ask_recv, bid_recv,
                        base_token_src, quote_token_dest, price, quantity,
                        "ask", "bid",
                        source_chain_id, dest_chain_id,
                        timestamp, nonce1, nonce2,
                        is_source_chain=False
                    )

                    print(f"order_id: {order_id}")
                    print(f"ask_addr: {ask_addr}")
                    print(f"bid_addr: {bid_addr}")
                    print(f"ask_recv: {ask_recv}")
                    print(f"bid_recv: {bid_recv}")
                    print(f"base_token_dest: {base_token_src}")
                    print(f"quote_token_dest: {quote_token_dest}")
                    print(f"price: {price}")
                    print(f"quantity: {quantity}")
                    print(f"party1_side: ask")
                    print(f"party2_side: bid")
                    print(f"source_chain_id: {source_chain_id}")
                    print(f"dest_chain_id: {dest_chain_id}")
                    print(f"timestamp: {timestamp}")
                    print(f"nonce1: {nonce1}")
                    print(f"nonce2: {nonce2}")
                    print(f"is_source_chain: False")
                    print("================================================")
                    if not result_dest.get("success"):
                        logger.error(
                            f"[{req_id}] Trade[{idx}] destination-chain settlement FAILED | "
                            f"error={result_dest.get('error')} "
                            f"diag={result_dest.get('diagnostics')} "
                            f"chain={dest_chain_id}"
                        )

                settlement_results.append({
                    "trade": trade,
                    "settlement_result": {
                        "success": result_source["success"] and result_dest["success"],
                        "source_chain": result_source,
                        "destination_chain": result_dest,
                        "order_id": order_id
                    }
                })
                
                logger.info(
                    f"[{req_id}] Trade[{idx}] done | "
                    f"ok_source={result_source.get('success')} "
                    f"ok_dest={result_dest.get('success')} "
                    f"elapsed={time.time()-t0:.2f}s"
                )

            total_elapsed = time.time() - start_ts
            successful_count = sum(1 for r in settlement_results if r['settlement_result'].get('success'))
            
            logger.info(
                f"[{req_id}] Settlement finished | "
                f"trades={len(order_dict['trades'])} "
                f"elapsed={total_elapsed:.2f}s "
                f"ok={successful_count}"
            )
            
            return {
                "settled": True,
                "settlement_results": settlement_results,
                "total_trades": len(order_dict["trades"]),
                "successful_settlements": successful_count
            }

        except Exception as e:
            logger.error(f"[{req_id}] Error during trade settlement: {e}", exc_info=True)
            return {"settled": False, "error": str(e)}

    @staticmethod
    async def settle_trades_if_any_same(
        order_dict: dict,
        SUPPORTED_NETWORKS: dict,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS: str,
        CONTRACT_ABI: list,
        PRIVATE_KEY: str,
        TOKEN_ADDRESSES: dict,
        settlement_client: SettlementClientSame,
        REQUIRE_CLIENT_SIGNATURES: bool = False,
    ) -> dict:
        """Wrapper to process only same-chain trades using settle_trades_if_any logic."""
        try:
            trades = order_dict.get("trades") or []
            if not trades:
                return {"settled": False, "reason": "No trades to settle"}
            
            t0 = trades[0]
            p1_from = (t0.get("party1") or [None]*8)[5]
            p2_from = (t0.get("party2") or [None]*8)[5]
            
            if not p1_from or not p2_from:
                return {"settled": False, "error": "missing_networks"}
            
            if SUPPORTED_NETWORKS.get(p1_from, {}).get("chain_id") != SUPPORTED_NETWORKS.get(p2_from, {}).get("chain_id"):
                return {"settled": False, "error": "not_same_chain"}
            
            return await APIHelper.settle_trades_if_any(
                order_dict,
                SUPPORTED_NETWORKS,
                TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                CONTRACT_ABI,
                PRIVATE_KEY,
                TOKEN_ADDRESSES,
                settlement_client,
                REQUIRE_CLIENT_SIGNATURES=REQUIRE_CLIENT_SIGNATURES,
            )
        except Exception as e:
            return {"settled": False, "error": str(e)}

    @staticmethod
    async def settle_trades_if_any_cross(
        order_dict: dict,
        SUPPORTED_NETWORKS: dict,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS: str,
        CONTRACT_ABI: list,
        PRIVATE_KEY: str,
        TOKEN_ADDRESSES: dict,
        settlement_client: SettlementClientSame,
        REQUIRE_CLIENT_SIGNATURES: bool = False,
    ) -> dict:
        """Wrapper to process only cross-chain trades using settle_trades_if_any logic."""
        try:
            trades = order_dict.get("trades") or []
            if not trades:
                return {"settled": False, "reason": "No trades to settle"}
            
            t0 = trades[0]
            p1_from = (t0.get("party1") or [None]*8)[5]
            p2_from = (t0.get("party2") or [None]*8)[5]
            
            if not p1_from or not p2_from:
                return {"settled": False, "error": "missing_networks"}
            
            if SUPPORTED_NETWORKS.get(p1_from, {}).get("chain_id") == SUPPORTED_NETWORKS.get(p2_from, {}).get("chain_id"):
                return {"settled": False, "error": "not_cross_chain"}
            
            return await APIHelper.settle_trades_if_any(
                order_dict,
                SUPPORTED_NETWORKS,
                TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                CONTRACT_ABI,
                PRIVATE_KEY,
                TOKEN_ADDRESSES,
                settlement_client,
                REQUIRE_CLIENT_SIGNATURES=REQUIRE_CLIENT_SIGNATURES,
            )
        except Exception as e:
            return {"settled": False, "error": str(e)}

    @staticmethod
    async def validate_order_prerequisites_cross(
        order_data: dict,
        SUPPORTED_NETWORKS: dict,
        TOKEN_ADDRESSES: dict,
        PRIVATE_KEY: str,
    ) -> dict:
        """Cross-chain oriented prerequisite check (ask: base on from; bid: quote on to)."""
        return await APIHelper.validate_order_prerequisites(order_data, SUPPORTED_NETWORKS, TOKEN_ADDRESSES, PRIVATE_KEY)

    @staticmethod
    async def handlePayloadJson(request: Request):
        content_type = request.headers.get("content-type", "")

        if "application/json" in content_type:
            payload_json = await request.json()
            return payload_json
        elif (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            form = await request.form()
            # form['payload'] is expected to be a JSON string
            payload_field = form.get("payload")
            if not payload_field:
                raise HTTPException(
                    status_code=422, detail="Missing 'payload' form field"
                )
            payload_json = json.loads(payload_field)
            return payload_json
        else:
            # try json fallback
            try:
                payload_json = await request.json()
                return payload_json
            except Exception:
                raise HTTPException(status_code=415, detail="Unsupported content type")