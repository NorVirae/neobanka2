from decimal import Decimal
from itertools import chain
import json
import logging
import os
import asyncio
import secrets
import sys
from time import time
import traceback
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from web3 import Web3

from helper.api_helper import APIHelper
from src.trade_settlement_client import SettlementClientSame, SettlementClientCross
from src import OrderBook

# from src.trade_settlement_client import AllowanceChecker, TradeSettlementClient

root = logging.getLogger()
if root.handlers:
    root.handlers.clear()

LOG_FORMAT = (
    "%(asctime)s %(levelname)s " "[%(filename)s:%(lineno)d %(funcName)s] " "%(message)s"
)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)


class APIService:
    def __init__(self):
        pass

    def register_startup_event(
        self, WEB3_PROVIDER, TRADE_SETTLEMENT_CONTRACT_ADDRESS, PRIVATE_KEY
    ) -> SettlementClientSame:
        """Initialize settlement client on startup"""
        global settlement_client, allowance_checker, allowance_manager

        # CONTRACT_ABI = APIHelper.load_abi("orderbook/settlement_abi.json")
        # print(CONTRACT_ABI)

        try:
            settlement_client = SettlementClientSame(
                WEB3_PROVIDER,
                TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                PRIVATE_KEY,
            )

            # allowance_checker = AllowanceChecker(WEB3_PROVIDER)
            logger.info("Settlement client initialized successfully")
            return settlement_client
        except Exception as e:
            logger.error(f"Failed to initialize settlement client: {e}")
            # You might want to exit here if settlement is critical

    async def register_order(
        self,
        request: Request,
        order_books,
        WEB3PROVIDER,
        TOKEN_ADDRESSES,
        SUPPORTED_NETWORKS,
        settlement_client,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS=None,
        CONTRACT_ABI=None,
        PRIVATE_KEY=None,
        activity_log=None,
        activity_file_path: str | None = None,
        append_file=None,
        order_signatures: dict | None = None,
    ):
        try:
            payload_json = await APIHelper.handlePayloadJson(request)

            # Signature attach path: allow client to POST signature after orderId is known
            if payload_json.get("attach_signature"):
                try:
                    order_id = str(payload_json["orderId"]).strip()
                    signer = str(payload_json["account"]).lower().strip()
                    signature = str(payload_json["signature"]).strip()
                    if not order_id or not signer or not signature:
                        return JSONResponse(
                            content={
                                "message": "Missing orderId/account/signature",
                                "status_code": 0,
                            },
                            status_code=400,
                        )
                    if order_signatures is not None:
                        if order_id not in order_signatures:
                            order_signatures[order_id] = {}
                        order_signatures[order_id][signer] = signature
                    return JSONResponse(
                        content={"message": "Signature attached", "status_code": 1}
                    )
                except Exception as e:
                    return JSONResponse(
                        content={"message": f"Attach failed: {e}", "status_code": 0},
                        status_code=400,
                    )

            # Same-chain service: force same-network and use canonical symbol only
            from_net = (
                payload_json.get("from_network")
                or payload_json.get("fromNetwork")
                or ""
            ).lower()
            to_net = (
                payload_json.get("to_network") or payload_json.get("toNetwork") or ""
            ).lower()
            if from_net and to_net and from_net != to_net:
                return JSONResponse(
                    content={
                        "message": "Use /api/register_order_cross for cross-chain orders",
                        "status_code": 0,
                    },
                    status_code=400,
                )
            symbol = "%s_%s" % (payload_json["baseAsset"], payload_json["quoteAsset"])

            # Step 1: Validate order prerequisites (balance and allowance)
            logger.info(f"Validating prerequisites for order: {payload_json}")
            validation_result = await APIHelper.validate_order_prerequisites(
                order_data=payload_json,
                SUPPORTED_NETWORKS=SUPPORTED_NETWORKS,
                TOKEN_ADDRESSES=TOKEN_ADDRESSES,
                PRIVATE_KEY=PRIVATE_KEY,
            )

            if not validation_result["valid"]:
                logger.warning(f"Order validation failed: {validation_result}")
                return JSONResponse(
                    content={
                        "message": "Order validation failed",
                        "errors": validation_result.get("errors", []),
                        "validation_details": validation_result.get("checks", {}),
                        "status_code": 0,
                    },
                    status_code=400,
                )

            logger.info(f"Order validation passed: {validation_result['checks']}")

            # Step 2: Process the order in the order book
            if symbol not in order_books:
                order_books[symbol] = OrderBook()

            order_book: OrderBook = order_books[symbol]

            _order = {
                "type": payload_json.get("type", "limit"),
                "trade_id": payload_json["account"],
                "from_network": from_net
                or payload_json.get("from_network")
                or payload_json.get("fromNetwork"),
                "to_network": to_net
                or payload_json.get("to_network")
                or payload_json.get("toNetwork"),
                "receive_wallet": payload_json.get("receive_wallet")
                or payload_json.get("receiveWallet"),
                "account": payload_json["account"],
                "price": Decimal(payload_json["price"]),
                "quantity": Decimal(payload_json["quantity"]),
                "side": payload_json["side"],
                "baseAsset": payload_json["baseAsset"],
                "quoteAsset": payload_json["quoteAsset"],
                # Optional; frontend no longer sends user private key. Keep None/empty for demo auto-sign.
                "private_key": payload_json.get("privateKey")
                or payload_json.get("private_key")
                or None,
            }

            process_result = order_book.process_order(_order, False, False)

            # This is the Failure case
            if not process_result["success"]:
                return JSONResponse(
                    content={"message": process_result["data"], "status_code": 0},
                    status_code=400,
                )

            trades, order, task_id, next_best_order = process_result["data"]

            if order is None:
                order = _order.copy()
                order["order_id"] = 1

            assert order is not None

            # Convert trades to the expected format
            converted_trades = []
            # Optional client-provided signatures for demo/frontend signing
            client_sig1 = payload_json.get("signature1") or payload_json.get(
                "signature_1"
            )
            client_sig2 = payload_json.get("signature2") or payload_json.get(
                "signature_2"
            )
            for trade in trades:
                party1 = [
                    trade["party1"][0],
                    trade["party1"][1],
                    int(trade["party1"][2]) if trade["party1"][2] is not None else None,
                    (
                        float(trade["party1"][3])
                        if trade["party1"][3] is not None
                        else None
                    ),
                    trade["party1"][4],
                    # source network for party1
                    trade["party1"][5] if len(trade["party1"]) > 5 else None,
                    # destination network for party1
                    trade["party1"][6] if len(trade["party1"]) > 6 else None,
                    # receive wallet on destination chain for party1
                    trade["party1"][7] if len(trade["party1"]) > 7 else None,
                ]
                party2 = [
                    trade["party2"][0],
                    trade["party2"][1],
                    int(trade["party2"][2]) if trade["party2"][2] is not None else None,
                    (
                        float(trade["party2"][3])
                        if trade["party2"][3] is not None
                        else None
                    ),
                    trade["party2"][4],
                    # source network for party2 (where their assets originate)
                    trade["party2"][5] if len(trade["party2"]) > 5 else None,
                    # destination network for party2
                    trade["party2"][6] if len(trade["party2"]) > 6 else None,
                    # receive wallet on destination chain for party2
                    trade["party2"][7] if len(trade["party2"]) > 7 else None,
                ]

                converted_trade = {
                    "timestamp": int(trade["timestamp"]),
                    "price": float(trade["price"]),
                    "quantity": float(trade["quantity"]),
                    "time": int(trade["time"]),
                    "party1": party1,
                    "party2": party2,
                    # Pass through client signatures if present (applied for all trades in this order)
                    **({"signature1": client_sig1} if client_sig1 else {}),
                    **({"signature2": client_sig2} if client_sig2 else {}),
                }
                # Overlay stored signatures by signer address if present
                try:
                    stored = (order_signatures or {}).get(
                        str((order or {}).get("order_id")), {}
                    )
                    sig_p1 = stored.get(str(party1[0]).lower())
                    sig_p2 = stored.get(str(party2[0]).lower())
                    if sig_p1 and not converted_trade.get("signature1"):
                        converted_trade["signature1"] = sig_p1
                    if sig_p2 and not converted_trade.get("signature2"):
                        converted_trade["signature2"] = sig_p2
                except Exception:
                    pass
                converted_trades.append(converted_trade)

            # Convert order to a serializable format
            order_dict = {
                "orderId": int(order["order_id"]),
                "account": order["account"],
                "price": float(order["price"]),
                "quantity": float(order["quantity"]),
                "side": order["side"],
                "baseAsset": order["baseAsset"],
                "quoteAsset": order["quoteAsset"],
                "trade_id": order["trade_id"],
                "trades": converted_trades,
                "isValid": True if order["order_id"] != 0 else True,
                "timestamp": order["timestamp"],
            }

            # Log placement to in-memory and file
            try:
                placement = {
                    "type": "order_placed",
                    "symbol": symbol,
                    "orderId": order_dict["orderId"],
                    "account": order_dict["account"],
                    "side": order_dict["side"],
                    "price": order_dict["price"],
                    "quantity": order_dict["quantity"],
                    "timestamp": order_dict["timestamp"],
                }
                if activity_log is not None:
                    activity_log.append(placement)
                if append_file is not None:
                    append_file(placement)
            except Exception:
                pass

            next_best_order_dict = None
            if next_best_order is not None:
                next_best_order_dict = {
                    "orderId": int(next_best_order.order_id),
                    "account": next_best_order.account,
                    "price": float(next_best_order.price),
                    "quantity": float(next_best_order.quantity),
                    "side": next_best_order.side,
                    "baseAsset": next_best_order.baseAsset,
                    "quoteAsset": next_best_order.quoteAsset,
                    "trade_id": next_best_order.trade_id,
                    "trades": [],
                    "isValid": True if next_best_order.order_id != 0 else True,
                    "timestamp": next_best_order.timestamp,
                }

            # Step 3: Settle trades if any exist (synchronous required)
            settlement_info = {"settled": False}
            if converted_trades:
                logger.info(f"Attempting to settle {len(converted_trades)} trade(s)")
                try:
                    # Force synchronous settlement so matching only succeeds if on-chain settlement succeeds
                    sync_flag = True
                    timeout_s = int(os.getenv("SETTLEMENT_SYNC_TIMEOUT", "20"))

                    async def _run_settlement_offthread():
                        loop = asyncio.get_running_loop()

                        def runner():
                            require_clients = os.getenv(
                                "REQUIRE_CLIENT_SIGNATURES", "false"
                            ).lower() in ("1", "true", "yes")
                            return asyncio.run(
                                APIHelper.settle_trades_if_any_same(
                                    order_dict,
                                    SUPPORTED_NETWORKS,
                                    TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                                    CONTRACT_ABI,
                                    PRIVATE_KEY,
                                    TOKEN_ADDRESSES,
                                    settlement_client,
                                    REQUIRE_CLIENT_SIGNATURES=require_clients,
                                )
                            )

                        return await loop.run_in_executor(None, runner)

                    if sync_flag:
                        try:
                            settlement_info = await asyncio.wait_for(
                                _run_settlement_offthread(), timeout=timeout_s
                            )
                        except asyncio.TimeoutError:
                            settlement_info = {"settled": False, "reason": "timeout"}
                    else:
                        # run in background and return immediately
                        asyncio.create_task(_run_settlement_offthread())
                        settlement_info = {
                            "settled": False,
                            "reason": "processing_async",
                        }
                except Exception as e:
                    logger.error(f"Settlement error: {e}")
                    settlement_info = {"settled": False, "error": str(e)}

                # Do not persist trades off-chain unless on-chain settlement succeeded
                # We defer recording until after we verify 'ok' below

            # No signature gating; settlement is owner-authorized on-chain now
            # If settlement did not fully succeed, roll back the match and return error
            try:
                if converted_trades:
                    ok = bool(settlement_info.get("settled")) and (
                        settlement_info.get("successful_settlements", 0)
                        >= len(converted_trades)
                    )
                    if not ok:
                        try:
                            # Log full settlement info for debugging
                            logger.error(f"Settlement failed | info={settlement_info}")
                        except Exception:
                            pass
                        try:
                            # best-effort cancel of the just-placed order in the book
                            if order and order.get("order_id"):
                                order_book.cancel_order(
                                    order.get("side"), order.get("order_id")
                                )
                        except Exception:
                            pass
                        return JSONResponse(
                            content={
                                "message": "On-chain settlement failed; order not accepted",
                                "order": None,
                                "status_code": 0,
                                "settlement_info": settlement_info,
                            },
                            status_code=400,
                        )
                    else:
                        # Only now record trades as executed since on-chain settlement succeeded
                        try:
                            # Prefer detailed per-trade success when available
                            results = (settlement_info or {}).get(
                                "settlement_results"
                            ) or []
                            for idx, tr in enumerate(converted_trades):
                                tr_ok = True
                                try:
                                    tr_ok = bool(
                                        (results[idx] or {})
                                        .get("settlement_result", {})
                                        .get("success", True)
                                    )
                                except Exception:
                                    tr_ok = True
                                if not tr_ok:
                                    continue
                                # Attach transaction hashes for verification in history
                                res_entry = {}
                                try:
                                    res_entry = (results[idx] or {}).get(
                                        "settlement_result", {}
                                    )
                                except Exception:
                                    res_entry = {}
                                src = res_entry.get("source_chain") or {}
                                dst = res_entry.get("destination_chain") or {}
                                src_hash = src.get("transaction_hash")
                                dst_hash = dst.get("transaction_hash")

                                # Build record with optional tx hash fields; for same-chain we set txHash
                                rec = {
                                    "type": "trade_executed",
                                    "symbol": symbol,
                                    "price": float(tr["price"]),
                                    "quantity": float(tr["quantity"]),
                                    "timestamp": int(tr["timestamp"]),
                                }
                                if src_hash and not dst_hash:
                                    rec["txHash"] = src_hash
                                if src_hash:
                                    rec["txHashSource"] = src_hash
                                if dst_hash:
                                    rec["txHashDestination"] = dst_hash
                                if activity_log is not None:
                                    activity_log.append(rec)
                                if append_file is not None:
                                    append_file(rec)
                        except Exception:
                            pass
            except Exception:
                pass

            logger.info(
                f"Order processed successfully with {len(converted_trades)} trades"
            )

            return JSONResponse(
                content={
                    "message": "Order registered successfully",
                    "order": order_dict,
                    "nextBest": next_best_order_dict,
                    "taskId": task_id,
                    "validation_details": validation_result.get("checks", {}),
                    "settlement_info": settlement_info,
                    "status_code": 1,
                },
                status_code=200,
            )

        except Exception as e:
            logger.error(f"Error in register_order: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def register_order_cross(
        self,
        request: Request,
        supported_networks,
        private_key,
        order_books,
        activity_log,
        activity_file_path,
    ):
        try:
            body = await request.json()

            from_network = body["from_network"]

            # get all variables
            chain_settlement_contract_address = supported_networks[from_network][
                "contract_address"
            ]

            print(chain_settlement_contract_address, "OMOOA")

            # handle token address
            if body["side"] == "ask":
                token_address = supported_networks[from_network]["tokens"][
                    body["baseAsset"]
                ]
            else:
                token_address = supported_networks[from_network]["tokens"][
                    body["quoteAsset"]
                ]

            # order has come through
            #  -- instantiate a web3 instance
            #  -- create settlement client contract
            client = SettlementClientCross(
                supported_networks[body["from_network"]]["rpc"], private_key=private_key
            )

            account = body["account"]
            from_network = body["from_network"]
            to_network = body["to_network"]

            print(account, from_network, "NETWORK")
            #  -- check if the escrow is enough
            total, available, locked = client.check_escrow_balance(
                settlement_address=chain_settlement_contract_address,
                user_address=account,
                token_address=token_address,
                chain_name=from_network,
            )

            # print(f"total: {Web3.from_wei(total, "ether")} available:{Web3.from_wei(available, "ether")}  locked: {Web3.from_wei(locked, "ether")} ")
            #  -- if there is enough is escrow allow order to move on to next step.
            amount = 0
            quantity = body["quantity"]
            price = body["price"]
            side_type = "base"
            if body["side"] == "ask":
                amount = float(quantity)

            else:
                amount = float(quantity) * float(price)
                side_type = "quote"

            symbol = "%s_%s" % (body["baseAsset"], body["quoteAsset"])
            print(amount)

            # check orders prequesite to make sure they have enough escrow
            if float(available) < float(amount):
                raise Exception(f"insufficient {side_type} for trade")

            print(order_books, "ORDER_BOOKS")
            # send order for matching or to be added to orderbook
            if symbol not in order_books:
                order_books[symbol] = OrderBook()

            order_book: OrderBook = order_books[symbol]
            _order = {
                "type": body.get("type", "limit"),
                "trade_id": body["account"],
                "from_network": from_network,
                "to_network": to_network,
                "receive_wallet": body.get("receiveWallet"),
                "account": body["account"],
                "price": Decimal(body["price"]),
                "quantity": Decimal(body["quantity"]),
                "side": body["side"],
                "baseAsset": body["baseAsset"],
                "quoteAsset": body["quoteAsset"],
            }

            logger.info("GOT HERE")
            process_result = order_book.process_order(_order, False, False)

            print(order_books["HBAR_USDT"].asks.__dict__, "ORDERB BOOKS")

            trades, order, task_id, next_best_order = process_result["data"]

            print(trades, "TRADES")
            result = []
            if len(trades) > 0:
                for trade in trades:
                    base_qty = Decimal(trade["quantity"])
                    print(trade["quantity"], price)
                    quote_qty = Decimal(trade["quantity"]) * Decimal(price)
                    party1 = trade["party1"]
                    party1_order = party1[7]
                    party1_account = party1[0]
                    party1_side = party1[1]
                    party1_orderid = party1[2]
                    party1_qty = party1[3]
                    party1_source_chain = party1[4]
                    party1_dest_chain = party1[5]
                    party1_receive_wallet = party1[6]
                    if party1_side == "bid":
                        print(party1_dest_chain, party1_source_chain, "DEST CHAIN")
                        party1_base_asset = supported_networks[party1_dest_chain][
                            "tokens"
                        ][party1_order["baseAsset"]]
                        party1_quote_asset = supported_networks[party1_source_chain][
                            "tokens"
                        ][party1_order["quoteAsset"]]

                    else:
                        party1_base_asset = supported_networks[party1_source_chain][
                            "tokens"
                        ]["tokens"][party1_order["baseAsset"]]
                        party1_quote_asset = supported_networks[party1_dest_chain][
                            "tokens"
                        ]["tokens"][party1_order["quoteAsset"]]

                    party2 = trade["party2"]
                    party2_order = party2[7]
                    party2_account = party2[0]
                    party2_side = party2[1]
                    party2_orderid = party2[2]
                    party2_qty = party2[3]
                    party2_source_chain = party2[4]
                    party2_dest_chain = party2[5]
                    party2_receive_wallet = party2[6]

                    cross_base_asset = ""
                    cross_quote_asset = ""
                    if party2_side == "bid":
                        party2_base_asset = supported_networks[party2_dest_chain][
                            "tokens"
                        ][party2_order["baseAsset"]]
                        party2_quote_asset = supported_networks[party2_source_chain][
                            "tokens"
                        ][party2_order["quoteAsset"]]

                    else:
                        party2_base_asset = supported_networks[party2_source_chain][
                            "tokens"
                        ][party2_order["baseAsset"]]
                        party2_quote_asset = supported_networks[party2_dest_chain][
                            "tokens"
                        ][party2_order["quoteAsset"]]

                    if party1_side == "bid" and party2_side == "ask":
                        cross_quote_asset = party1_quote_asset
                        cross_base_asset = party2_base_asset

                    else:
                        cross_quote_asset = party2_quote_asset
                        cross_base_asset = party1_base_asset

                    # After extracting party1 and party2 data...

                    # Determine who should be party1 (ask on source) and party2 (bid on dest)
                    # Based on contract requirements:
                    # - party1 = seller (ask) on source chain
                    # - party2 = buyer (bid) on destination chain

                    if party1_side == "ask" and party2_side == "bid":
                        # party1 is already ask, party2 is already bid - CORRECT ORDER
                        source_party = {
                            "account": party1_account,
                            "side": party1_side,
                            "source_chain": party1_source_chain,
                            "dest_chain": party1_dest_chain,
                            "receive_wallet": party1_receive_wallet,
                            "base_asset": party1_base_asset,
                            "quote_asset": party1_quote_asset,
                        }
                        dest_party = {
                            "account": party2_account,
                            "side": party2_side,
                            "source_chain": party2_source_chain,
                            "dest_chain": party2_dest_chain,
                            "receive_wallet": party2_receive_wallet,
                            "base_asset": party2_base_asset,
                            "quote_asset": party2_quote_asset,
                        }
                        source_chain = party1_source_chain
                        dest_chain = party2_source_chain
                        cross_base_asset = party1_base_asset
                        cross_quote_asset = party2_quote_asset

                    elif party1_side == "bid" and party2_side == "ask":
                        # party1 is bid, party2 is ask - SWAP THEM
                        # party2 becomes party1 (ask on source), party1 becomes party2 (bid on dest)
                        source_party = {
                            "account": party2_account,
                            "side": party2_side,  # "ask"
                            "source_chain": party2_source_chain,
                            "dest_chain": party2_dest_chain,
                            "receive_wallet": party2_receive_wallet,
                            "base_asset": party2_base_asset,
                            "quote_asset": party2_quote_asset,
                        }
                        dest_party = {
                            "account": party1_account,
                            "side": party1_side,  # "bid"
                            "source_chain": party1_source_chain,
                            "dest_chain": party1_dest_chain,
                            "receive_wallet": party1_receive_wallet,
                            "base_asset": party1_base_asset,
                            "quote_asset": party1_quote_asset,
                        }
                        source_chain = party2_source_chain
                        dest_chain = party1_source_chain
                        cross_base_asset = party2_base_asset
                        cross_quote_asset = party1_quote_asset
                    else:
                        raise Exception(
                            "Invalid trade sides - both parties on same side"
                        )

                    # Now create trade_data with properly ordered parties
                    trade_id_hash = secrets.token_hex(32)
                    order_id_bytes = Web3.to_bytes(hexstr=f"0x{trade_id_hash}")

                    trade_data = {
                        "orderId": order_id_bytes,
                        "party1": Web3.to_checksum_address(
                            source_party["account"]
                        ),  # ask seller
                        "party2": Web3.to_checksum_address(
                            dest_party["account"]
                        ),  # bid buyer
                        "party1ReceiveWallet": Web3.to_checksum_address(
                            source_party["receive_wallet"]
                        ),
                        "party2ReceiveWallet": Web3.to_checksum_address(
                            dest_party["receive_wallet"]
                        ),
                        "baseAsset": Web3.to_checksum_address(cross_base_asset),
                        "quoteAsset": Web3.to_checksum_address(cross_quote_asset),
                        "price": int(Decimal(price) * Decimal("1e18")),
                        "quantity": int(base_qty * Decimal("1e18")),
                        "party1Side": "ask",  # Always "ask" for party1
                        "party2Side": "bid",  # Always "bid" for party2
                        "sourceChainId": int(
                            supported_networks[source_chain]["chain_id"]
                        ),
                        "destinationChainId": int(
                            supported_networks[dest_chain]["chain_id"]
                        ),
                        "timestamp": int(time()),
                        "nonce1": 0,
                        "nonce2": 0,
                    }

                    # Create clients for the correct chains
                    source_chain_client = SettlementClientCross(
                        supported_networks[source_chain]["rpc"], private_key
                    )

                    dest_chain_client = SettlementClientCross(
                        supported_networks[dest_chain]["rpc"], private_key
                    )

                    # Settle on source chain (where party1/ask is)
                    result_source = source_chain_client.settle_cross_chain_trade(
                        settlement_address=supported_networks[source_chain][
                            "contract_address"
                        ],
                        trade_data=trade_data,
                        is_source_chain=True,
                        private_key=private_key,
                        chain_name=source_chain,
                    )

                    # Settle on destination chain (where party2/bid is)
                    result_dest = dest_chain_client.settle_cross_chain_trade(
                        settlement_address=supported_networks[dest_chain][
                            "contract_address"
                        ],
                        trade_data=trade_data,
                        is_source_chain=False,
                        private_key=private_key,
                        chain_name=dest_chain,
                    )
                    trade_result = {
                        "trade_source_receipt": result_source,
                        "trade_dest_receipt": result_dest,
                    }
                    result.append(trade_result)

            # finish
            return {
                "available": Web3.from_wei(available, "ether"),
                "total": Web3.from_wei(total, "ether"),
                "locked": Web3.from_wei(locked, "ether"),
                # "trades": trades,
                "order": order,
                "task_id": task_id,
                "next_best_order": next_best_order,
                "result": result,
            }
        except Exception as error:
            self.full_system_traceback(error)

            return JSONResponse(status_code=400, content=str(error))

    def full_system_traceback(self, error):
        # Get the full traceback
        exc_type, exc_value, exc_tb = sys.exc_info()

        # Format the full traceback
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        full_traceback = "".join(tb_lines)

        # Get the line where the error actually occurred
        tb_list = traceback.extract_tb(exc_tb)

        # Log each frame in the traceback to see the call stack
        logger.error(f"Error in register_order_cross: {error}")
        logger.error("Full traceback:")
        for frame in tb_list:
            logger.error(
                f"  File: {frame.filename}, Line: {frame.lineno}, Function: {frame.name}"
            )
            logger.error(f"  Code: {frame.line}")

        # Or just log the entire formatted traceback
        logger.error(full_traceback)

    async def cancel_order(
        self,
        request: Request,
        order_books,
        activity_log=None,
        activity_file_path: str | None = None,
        append_file=None,
    ):
        try:
            payload_json = await APIHelper.handlePayloadJson(request)
            order_id = payload_json["orderId"]
            side = payload_json["side"]
            symbol = "%s_%s" % (payload_json["baseAsset"], payload_json["quoteAsset"])

            order_book = order_books[symbol]
            order = (
                order_book.bids.get_order(order_id)
                if order_id in order_book.bids.order_map
                else order_book.asks.get_order(order_id)
            )
            order_book.cancel_order(side, order_id)

            # Convert order to a serializable format
            order_dict = {
                "orderId": int(order_id),
                "account": order.account,
                "price": float(order.price),
                "quantity": float(order.quantity),
                "side": order.side,
                "baseAsset": order.baseAsset,
                "quoteAsset": order.quoteAsset,
                "trade_id": order.trade_id,
                "trades": [],
                "isValid": False,
                "timestamp": order.timestamp,
            }

            # Log cancellation
            try:
                cancel_rec = {
                    "type": "order_cancelled",
                    "symbol": symbol,
                    "orderId": int(order_id),
                    "side": side,
                    "timestamp": order.timestamp,
                }
                if activity_log is not None:
                    activity_log.append(cancel_rec)
                if append_file is not None:
                    append_file(cancel_rec)
            except Exception:
                pass

            return JSONResponse(
                content={
                    "message": "Order cancelled successfully",
                    "order": order_dict,
                    "status_code": 1,
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_order(self, payload: str, order_books):
        try:
            payload_json = json.loads(payload)
            order_id = payload_json["orderId"]

            order = None
            for symbol, order_book in order_books.items():
                if (
                    order_id in order_book.bids.order_map
                    or order_id in order_book.asks.order_map
                ):
                    order = (
                        order_book.bids.get_order(order_id)
                        if order_id in order_book.bids.order_map
                        else order_book.asks.get_order(order_id)
                    )

            if order is not None:
                order_dict = {
                    "orderId": (
                        int(order.order_id) if order.order_id is not None else None
                    ),
                    "account": order.account,
                    "price": float(order.price),
                    "quantity": float(order.quantity),
                    "side": order.side,
                    "baseAsset": order.baseAsset,
                    "quoteAsset": order.quoteAsset,
                    "trade_id": order.trade_id,
                    "trades": [],
                    "isValid": True if order.order_id is not None else False,
                    "timestamp": order.timestamp,
                }

                return JSONResponse(
                    content={
                        "message": "Order retrieved successfully",
                        "order": order_dict,
                        "status_code": 1,
                    }
                )
            else:
                return JSONResponse(
                    content={
                        "message": "Order not found",
                        "order": None,
                        "status_code": 0,
                    }
                )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def get_orderbook(self, request: Request, order_books):
        try:
            payload_json = await APIHelper.handlePayloadJson(request)

            symbol = payload_json["symbol"]

            if symbol not in order_books:
                order_book = OrderBook()
                order_books[symbol] = order_book
            else:
                order_book = order_books[symbol]

            result = order_book.get_orderbook(payload_json["symbol"])

            return JSONResponse(
                content={
                    "message": "Order book retrieved successfully",
                    "orderbook": result,
                    "status_code": 1,
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def get_trades(self, request: Request, order_books):
        try:
            payload_json = await APIHelper.handlePayloadJson(request)

            symbol = payload_json["symbol"]
            limit = int(payload_json.get("limit", 200))

            if symbol not in order_books:
                order_book = OrderBook()
                order_books[symbol] = order_book
            else:
                order_book = order_books[symbol]

            # Convert tape (trade history) to serializable list
            trades_list = [
                {
                    "timestamp": int(trade.get("timestamp", 0)),
                    "time": int(trade.get("time", 0)),
                    "price": float(trade.get("price", 0)),
                    "quantity": float(trade.get("quantity", 0)),
                }
                for trade in list(order_book.tape)
            ]

            # Sort by time ascending and trim to last N
            trades_list.sort(key=lambda t: t.get("time", t.get("timestamp", 0)))
            if limit > 0:
                trades_list = trades_list[-limit:]

            return JSONResponse(
                content={
                    "message": "Trades retrieved successfully",
                    "symbol": symbol,
                    "trades": trades_list,
                    "status_code": 1,
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def settle_trades(
        self,
        request: Request,
        SUPPORTED_NETWORKS,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS,
        CONTRACT_ABI,
        PRIVATE_KEY,
        TOKEN_ADDRESSES,
        settlement_client,
    ):
        try:
            payload_json = await APIHelper.handlePayloadJson(request)

            # Accept either a full order_dict or separate fields
            order_dict = payload_json.get("order") or {}
            if not order_dict:
                raise HTTPException(
                    status_code=422, detail="Missing 'order' in payload"
                )

            trades = payload_json.get("trades")
            if trades is None:
                trades = order_dict.get("trades")
            if not isinstance(trades, list):
                trades = []
            order_dict["trades"] = trades

            # Trigger settlement (route dynamically based on trade networks)
            # Determine using first trade; fallback to cross-chain
            use_cross = True
            try:
                t0 = (order_dict.get("trades") or [])[0]
                p1_from = (t0.get("party1") or [None] * 8)[5]
                p2_from = (t0.get("party2") or [None] * 8)[5]
                if p1_from and p2_from:
                    c1 = SUPPORTED_NETWORKS.get(p1_from, {}).get("chain_id")
                    c2 = SUPPORTED_NETWORKS.get(p2_from, {}).get("chain_id")
                    use_cross = c1 != c2
            except Exception:
                use_cross = True
            settlement_info = await (
                APIHelper.settle_trades_if_any_cross
                if use_cross
                else APIHelper.settle_trades_if_any_same
            )(
                order_dict,
                SUPPORTED_NETWORKS,
                TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                CONTRACT_ABI,
                PRIVATE_KEY,
                TOKEN_ADDRESSES,
                settlement_client,
                REQUIRE_CLIENT_SIGNATURES=os.getenv(
                    "REQUIRE_CLIENT_SIGNATURES", "false"
                ).lower()
                in ("1", "true", "yes"),
            )

            return JSONResponse(
                content={
                    "message": "Settlement processed",
                    "orderId": order_dict.get("orderId"),
                    "settlement_info": settlement_info,
                    "status_code": 1,
                }
            )
        except Exception as e:
            logger.error(f"Error in settle_trades: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def get_settlement_address(self, TRADE_SETTLEMENT_CONTRACT_ADDRESS):
        try:
            if not TRADE_SETTLEMENT_CONTRACT_ADDRESS:
                raise "Trade settlement address not set"
            return JSONResponse(
                content={
                    "status_code": 200,
                    "message": "Settlement Address",
                    "data": {"settlement_address": TRADE_SETTLEMENT_CONTRACT_ADDRESS},
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def check_available_funds(self, order_books, payload):
        try:
            payload_json = json.loads(payload)
            account = payload_json["account"]
            asset = payload_json["asset"]

            # Calculate total locked funds across all order books
            total_locked_amount = Decimal("0")

            # Iterate through all order books
            for symbol, order_book in order_books.items():
                # Check if this order book involves the asset we're looking for
                base_asset, quote_asset = symbol.split("_")

                # Check bids (buying orders)
                if quote_asset == asset:  # If quote asset matches, check bids
                    for order_id, order in order_book.bids.order_map.items():
                        if order["account"].lower() == account.lower():
                            # For bids, the locked amount is price * quantity in quote asset
                            locked_amount = order["price"] * order["quantity"]
                            total_locked_amount += locked_amount

                # Check asks (selling orders)
                if base_asset == asset:  # If base asset matches, check asks
                    for order_id, order in order_book.asks.order_map.items():
                        if order["account"].lower() == account.lower():
                            # For asks, the locked amount is just the quantity in base asset
                            total_locked_amount += order["quantity"]

            return JSONResponse(
                content={
                    "message": "Available funds checked successfully",
                    "account": account,
                    "asset": asset,
                    "lockedAmount": float(total_locked_amount),
                    "status_code": 1,
                }
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Add a health check endpoint for the settlement system
    async def settlement_health(
        self, settlement_client, TRADE_SETTLEMENT_CONTRACT_ADDRESS
    ):
        """Check if settlement system is operational"""
        try:
            if not settlement_client:
                return JSONResponse(
                    content={
                        "status": "unhealthy",
                        "message": "Settlement client not initialized",
                        "web3_connected": False,
                    },
                    status_code=503,
                )

            # Check if web3 is connected
            web3_connected = settlement_client.web3.isConnected()

            return JSONResponse(
                content={
                    "status": "healthy" if web3_connected else "degraded",
                    "message": (
                        "Settlement system operational"
                        if web3_connected
                        else "Web3 connection issues"
                    ),
                    "web3_connected": web3_connected,
                    "contract_address": TRADE_SETTLEMENT_CONTRACT_ADDRESS,
                },
                status_code=200 if web3_connected else 503,
            )
        except Exception as e:
            return JSONResponse(
                content={
                    "status": "unhealthy",
                    "message": f"Settlement health check failed: {str(e)}",
                    "web3_connected": False,
                },
                status_code=503,
            )

    async def check_escrow_balance(self, request: Request):
        """Check escrow balance for a user"""
        try:
            payload_json = await APIHelper.handlePayloadJson(request)

            user_address = payload_json["userAddress"]
            token_address = payload_json["tokenAddress"]

            balance = settlement_client.check_escrow_balance(
                user_address,
                token_address,
                token_decimals=payload_json.get("decimals", 18),
            )

            return JSONResponse(
                content={
                    "message": "Balance retrieved successfully",
                    "balance": balance,
                    "status_code": 1,
                }
            )
        except Exception as e:
            logger.error(f"Error checking escrow balance: {e}")
            raise HTTPException(status_code=500, detail=str(e))
