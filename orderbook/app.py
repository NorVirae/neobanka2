from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os
from typing import Optional

from dotenv import load_dotenv

# import asyncio
import logging
from helper.api_service import APIService

# Import the TradeSettlementClient
from src.trade_settlement_client import (
    SettlementClientSame,
    # AllowanceChecker,
    # AllowanceManager,
)
from helper.api_helper import APIHelper
import httpx
from collections import deque
import json

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

order_books = {}  # Same-chain order books keyed by symbol
activity_log = deque(maxlen=1000)
ACTIVITY_LOG_PATH = os.getenv("ACTIVITY_LOG_PATH", "orderbook_activity.jsonl")

# Cross-chain isolated state
cross_order_books = {}  # Cross-chain order books keyed by symbol (same symbols, separate dict)
cross_activity_log = deque(maxlen=1000)
CROSS_ACTIVITY_LOG_PATH = os.getenv("CROSS_ACTIVITY_LOG_PATH", "orderbook_activity_cross.jsonl")
order_signatures = {}

def append_activity_file(entry: dict):
    try:
        with open(ACTIVITY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write activity file: {e}")

def append_cross_activity_file(entry: dict):
    try:
        with open(CROSS_ACTIVITY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.error(f"Failed to write cross activity file: {e}")

# Configuration - you should move these to environment variables
# WEB3_PROVIDER = os.getenv("WEB3_PROVIDER", "https://your-ethereum-node.com")
# Prefer specific Hedera address, then generic fallback if provided
TRADE_SETTLEMENT_CONTRACT_ADDRESS = (
    os.getenv("TRADE_SETTLE_CONTRACT_ADDRESS_HEDERA")
    or os.getenv("TRADE_SETTLE_CONTRACT_ADDRESS")
    or "0x237458E2cF7593084Ae397a50166A275A3928bA7"
)

# Supported networks mapping. Each entry contains the RPC URL, the numeric
# chain id (used when building the CrossChainTradeData struct) and an optional
# per-network settlement contract address. Values can be overridden with
# environment variables for deployment.
SUPPORTED_NETWORKS = {
    "hedera": {
        "rpc": os.getenv("WEB3_PROVIDER_HEDERA", "https://testnet.hashio.io/api"),
        "chain_id": int(os.getenv("WEB3_CHAIN_ID_HEDERA", "296")),
        "contract_address": os.getenv(
            "TRADE_SETTLE_CONTRACT_ADDRESS_HEDERA", TRADE_SETTLEMENT_CONTRACT_ADDRESS
        ),
        "tokens": {
            "HBAR": os.getenv(
                "HEDERA_HBAR_TOKEN_ADDRESS", os.getenv("HBAR_TOKEN_ADDRESS", "0x66B8244b08be8F4Cec1A23C5c57A1d7b8A27189D")
            ),
            "USDT": os.getenv(
                "HEDERA_USDT_TOKEN_ADDRESS", os.getenv("USDT_TOKEN_ADDRESS", "0x62bcF51859E23cc47ddc6C3144B045619476Be92")
            ),
        },
    },
    "ethereum": {
        # Support both ETHEREUM_* and SEPOLIA_* naming; prefer explicit ETHEREUM_* if set
        "rpc": os.getenv(
            "WEB3_PROVIDER_ETHEREUM",
            os.getenv("WEB3_PROVIDER_SEPOLIA", "https://ethereum-sepolia-rpc.publicnode.com"),
        ),
        "chain_id": int(
            os.getenv("WEB3_CHAIN_ID_ETHEREUM", os.getenv("WEB3_CHAIN_ID_SEPOLIA", "11155111"))
        ),
        "contract_address": os.getenv(
            "TRADE_SETTLE_CONTRACT_ADDRESS_ETHEREUM",
            os.getenv("TRADE_SETTLE_CONTRACT_ADDRESS_SEPOLIA", "0x10F0F2cb456BEd15655afB22ddd7d0EEE11FdBc9"),
        ),
        "tokens": {
            # Allow overriding USDT with SEPOLIA-specific variable if used
            "USDT": os.getenv(
                "USDT_ETH_ADDRESS",
                os.getenv("SEPOLIA_USDT_TOKEN_ADDRESS", "0x7169D38820dfd117C3FA1f22a697dBA58d90BA06"),
            ),

            "HBAR": os.getenv(
                "SEPOLIA_HBAR_TOKEN_ADDRESS",
                os.getenv("SEPOLIA_HBAR_TOKEN_ADDRESS", "0xb458260166d1456A5ffB46eBbC4270738A515286"),
            ),
            "xZAR": os.getenv("XZAR_ETH_ADDRESS", "0x48f07301e9e29c3c38a80ae8d9ae771f224f1054"),
            "cNGN": os.getenv("CNGN_ETH_ADDRESS", "0x17CDB2a01e7a34CbB3DD4b83260B05d0274C8dab"),
        },
    },
    "bsc": {
        "rpc": os.getenv("WEB3_PROVIDER_BSC", "https://bsc-dataseed.binance.org"),
        "chain_id": int(os.getenv("WEB3_CHAIN_ID_BSC", "56")),
        "contract_address": os.getenv(
            "TRADE_SETTLE_CONTRACT_ADDRESS_BSC", TRADE_SETTLEMENT_CONTRACT_ADDRESS
        ),
        "tokens": {
            "cNGN": os.getenv("CNGN_BSC_ADDRESS", "0xa8AEA66B361a8d53e8865c62D142167Af28Af058"),
        },
    },
    "celo": {
        "rpc": os.getenv("WEB3_PROVIDER_CELO", "https://forno.celo.org"),
        "chain_id": int(os.getenv("WEB3_CHAIN_ID_CELO", "42220")),
        "contract_address": os.getenv(
            "TRADE_SETTLE_CONTRACT_ADDRESS_CELO", TRADE_SETTLEMENT_CONTRACT_ADDRESS
        ),
        "tokens": {
            "cKES": os.getenv("CKES_CELO_ADDRESS", "0x456a3D042C0DbD3db53D5489e98dFb038553B0d0"),
            "cZAR": os.getenv("CZAR_CELO_ADDRESS", "0x4c35853A3B4e647fD266f4de678dCc8fEC410BF6"),
            "cGHS": os.getenv("CGHS_CELO_ADDRESS", "0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313"),
        },
    },
    "base": {
        "rpc": os.getenv("WEB3_PROVIDER_BASE", "https://mainnet.base.org"),
        "chain_id": int(os.getenv("WEB3_CHAIN_ID_BASE", "8453")),
        "contract_address": os.getenv(
            "TRADE_SETTLE_CONTRACT_ADDRESS_BASE", TRADE_SETTLEMENT_CONTRACT_ADDRESS
        ),
        "tokens": {
            "cNGN": os.getenv("CNGN_BASE_ADDRESS", "0x46C85152bFe9f96829aA94755D9f915F9B10EF5F"),
        },
    },
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.error("SHOULD RUN ON STARTUP!")
    api_service.register_startup_event(
        WEB3_PROVIDER=SUPPORTED_NETWORKS["hedera"]["rpc"],
        TRADE_SETTLEMENT_CONTRACT_ADDRESS=TRADE_SETTLEMENT_CONTRACT_ADDRESS,
        PRIVATE_KEY=PRIVATE_KEY,
    )
    yield


app = FastAPI(lifespan=lifespan)


api_service = APIService()
# Add CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Global settlement client - initialize on startup
settlement_client: Optional[SettlementClientSame] = None
# allowance_checker: Optional[AllowanceChecker] = None
# allowance_manager: Optional[AllowanceManager] = None


PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Should be loaded securely
try:
    CONTRACT_ABI = APIHelper.load_abi("settlement_abi.json")
except Exception:
    CONTRACT_ABI = []  # fallback

# Legacy token mapping (kept for compatibility); prefer SUPPORTED_NETWORKS[net]["tokens"]
TOKEN_ADDRESSES = {
    "HBAR": os.getenv("HBAR_TOKEN_ADDRESS", SUPPORTED_NETWORKS["hedera"]["tokens"]["HBAR"]),
    "USDT": os.getenv("USDT_TOKEN_ADDRESS", SUPPORTED_NETWORKS["hedera"]["tokens"]["USDT"]),
    "xZAR_ETH": os.getenv(
        "XZAR_ETH_ADDRESS", "0x48f07301e9e29c3c38a80ae8d9ae771f224f1054"
    ),
    "cNGN_ETH": os.getenv(
        "CNGN_ETH_ADDRESS", "0x17CDB2a01e7a34CbB3DD4b83260B05d0274C8dab"
    ),
    "cNGN_BSC": os.getenv(
        "CNGN_BSC_ADDRESS", "0xa8AEA66B361a8d53e8865c62D142167Af28Af058"
    ),
    "cNGN_BASE": os.getenv(
        "CNGN_BASE_ADDRESS", "0x46C85152bFe9f96829aA94755D9f915F9B10EF5F"
    ),
    "cKES_CELO": os.getenv(
        "CKES_CELO_ADDRESS", "0x456a3D042C0DbD3db53D5489e98dFb038553B0d0"
    ),
    "cZAR_CELO": os.getenv(
        "CZAR_CELO_ADDRESS", "0x4c35853A3B4e647fD266f4de678dCc8fEC410BF6"
    ),
    "cGHS_CELO": os.getenv(
        "CGHS_CELO_ADDRESS", "0xfAeA5F3404bbA20D3cc2f8C4B0A888F55a3c7313"
    ),
}


print(SUPPORTED_NETWORKS, "SUPPORTED NETWORKS IN APP.PY")


@app.post("/api/register_order")
async def register_order(request: Request):
    logger.info("Got here")
 
    settlement_client = SettlementClientSame(
        web3_provider=SUPPORTED_NETWORKS["hedera"]["rpc"],
        contract_address=TRADE_SETTLEMENT_CONTRACT_ADDRESS,
        private_key=PRIVATE_KEY,
    )
    return await api_service.register_order(
        request=request,
        order_books=order_books,
        WEB3PROVIDER=SUPPORTED_NETWORKS["hedera"]["rpc"],
        TOKEN_ADDRESSES=TOKEN_ADDRESSES,
        SUPPORTED_NETWORKS=SUPPORTED_NETWORKS,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS=TRADE_SETTLEMENT_CONTRACT_ADDRESS,
        CONTRACT_ABI=CONTRACT_ABI,
        PRIVATE_KEY=PRIVATE_KEY,
        settlement_client=settlement_client,
        activity_log=activity_log,
        activity_file_path=ACTIVITY_LOG_PATH,
        append_file=append_activity_file,
        order_signatures=order_signatures,
    )


@app.post("/api/register_order_crosses")
async def register_order_cross(request: Request):
    return await api_service.register_order_cross(
        request=request,
        supported_networks=SUPPORTED_NETWORKS,
        private_key=PRIVATE_KEY,
        order_books=cross_order_books,
        activity_log=activity_log,
        activity_file_path=ACTIVITY_LOG_PATH,
    )


@app.post("/api/cancel_order")
async def cancel_order(request: Request):
    return await api_service.cancel_order(
        request,
        order_books=order_books,
        activity_log=activity_log,
        activity_file_path=ACTIVITY_LOG_PATH,
        append_file=append_activity_file,
    )


@app.post("/api/order")
async def get_order(payload: str = Form(...)):
    return await api_service.get_order(payload=payload, order_books=order_books)


@app.post("/api/orderbook")
async def get_orderbook(request: Request):
    return await api_service.get_orderbook(request=request, order_books=order_books)

@app.post("/api/orderbook_cross")
async def get_orderbook_cross(request: Request):
    return await api_service.get_orderbook(request=request, order_books=cross_order_books)


@app.post("/api/trades")
async def get_trades(request: Request):
    return await api_service.get_trades(request=request, order_books=order_books)

@app.post("/api/trades_cross")
async def get_trades_cross(request: Request):
    return await api_service.get_trades(request=request, order_books=cross_order_books)


@app.get("/api/get_settlement_address")
async def get_settlement_address(network: str | None = None):
    try:
        key = (network or "hedera").lower()
        net = SUPPORTED_NETWORKS.get(key)
        print(net, "NET IN GET SETTLEMENT ADDRESS", network)
        if not net:
            return {"status_code": 0, "message": f"unknown network {key}"}
        addr = net.get("contract_address") or ""
        return {"status_code": 1, "data": {"settlement_address": addr}}
    except Exception as e:
        return {"status_code": 0, "message": str(e)}


@app.get("/api/networks")
async def get_networks():
    try:
        # Return a JSON-serializable view of supported networks (safe subset)
        def _filter(net: dict):
            if not isinstance(net, dict):
                return {}
            return {
                "rpc": net.get("rpc"),
                "chain_id": net.get("chain_id"),
                "contract_address": net.get("contract_address"),
                "tokens": net.get("tokens", {}),
            }

        data = {k: _filter(v) for k, v in SUPPORTED_NETWORKS.items()}
        return {
            "status_code": 200,
            "message": "Supported networks",
            "networks": data,
        }
    except Exception as e:
        logger.error(f"Failed to get networks: {e}")
        return {"status_code": 0, "message": str(e)}


@app.post("/api/check_available_funds")
async def check_available_funds(payload: str = Form(...)):
    return api_service.check_available_funds(
        order_books=order_books, payload=payload
    )


# Price proxy to avoid CORS from frontend
@app.get("/api/price")
async def get_price(currency_pair: str):
    url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={currency_pair}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            # Pass-through JSON
            return r.json()
    except httpx.HTTPError as e:
        logger.error(f"Price proxy error: {e}")
        return {"error": "failed_to_fetch_price", "details": str(e)}

# Candlestick proxy (Gate.io)
@app.get("/api/kline")
async def get_kline(currency_pair: str, interval: str = "1h", limit: int = 200):
    url = (
        "https://api.gateio.ws/api/v4/spot/candlesticks"
        f"?currency_pair={currency_pair}&interval={interval}&limit={limit}"
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.error(f"Kline proxy error: {e}")
        return {"error": "failed_to_fetch_kline", "details": str(e)}

@app.get("/api/order_history")
async def order_history(symbol: str | None = None, limit: int = 200):
    try:
        if not os.path.exists(ACTIVITY_LOG_PATH):
            return {"status_code": 1, "history": []}
        items = []
        with open(ACTIVITY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type")
                if t not in ("order_placed", "order_cancelled", "trade_executed"):
                    continue
                if symbol and obj.get("symbol") != symbol:
                    continue
                items.append(obj)
        if limit > 0:
            items = items[-limit:]
        return {"status_code": 1, "count": len(items), "history": items}
    except Exception as e:
        logger.error(f"order_history error: {e}")
        return {"status_code": 0, "message": str(e)}

# Cross-chain order history endpoint
@app.get("/api/order_history_cross")
async def order_history_cross(symbol: str | None = None, limit: int = 200):
    try:
        if not os.path.exists(CROSS_ACTIVITY_LOG_PATH):
            return {"status_code": 1, "history": []}
        items = []
        with open(CROSS_ACTIVITY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type")
                if t not in ("order_placed", "order_cancelled", "trade_executed"):
                    continue
                if symbol and obj.get("symbol") != symbol:
                    continue
                items.append(obj)
        if limit > 0:
            items = items[-limit:]
        return {"status_code": 1, "count": len(items), "history": items}
    except Exception as e:
        logger.error(f"order_history_cross error: {e}")
        return {"status_code": 0, "message": str(e)}
        
# Add a health check endpoint for the settlement system
@app.get("/api/settlement_health")
async def settlement_health():
    return await api_service.settlement_health(
        settlement_client=settlement_client,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS=TRADE_SETTLEMENT_CONTRACT_ADDRESS,
    )


@app.post("/api/settle_trades")
async def settle_trades(request: Request):
    return await api_service.settle_trades(
        request=request,
        SUPPORTED_NETWORKS=SUPPORTED_NETWORKS,
        TRADE_SETTLEMENT_CONTRACT_ADDRESS=TRADE_SETTLEMENT_CONTRACT_ADDRESS,
        CONTRACT_ABI=CONTRACT_ABI,
        PRIVATE_KEY=PRIVATE_KEY,
        TOKEN_ADDRESSES=TOKEN_ADDRESSES,
        settlement_client=settlement_client,
    )

@app.post("/api/faucet")
async def faucet(request: Request):
    try:
        payload = await APIHelper.handlePayloadJson(request)
        to = payload.get("to")
        asset = (payload.get("asset") or "HBAR").upper()
        network = (payload.get("network") or "hedera").lower()
        amount = float(payload.get("amount") or 100)
        if not to:
            return {"status_code": 0, "message": "missing 'to'"}
        net = SUPPORTED_NETWORKS.get(network)
        if not net:
            return {"status_code": 0, "message": f"unknown network {network}"}
        token_addr = (net.get("tokens") or {}).get(asset)
        if not token_addr:
            return {"status_code": 0, "message": f"token not configured for {asset} on {network}"}

        client = SettlementClientSame(net.get("rpc"), net.get("contract_address"), PRIVATE_KEY)
        # Default decimals: HBAR 18, USDT 6 in our setup
        decimals = 18 if asset == "HBAR" else 6
        res = client.mint_token(token_addr, to, amount, token_decimals=decimals)
        return {"status_code": 1 if res.get("success") else 0, "result": res}
    except Exception as e:
        return {"status_code": 0, "message": str(e)}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
