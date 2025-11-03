#!/usr/bin/env python3
"""
Cross-Chain Market Maker Bot for NeoBanka Orderbook

This bot monitors orders across different networks (Hedera, Ethereum)
and facilitates cross-chain trading by:
1. Monitoring orderbooks on each network
2. Matching complementary orders across chains
3. Executing cross-chain settlements
4. Handling the atomic swap coordination

Example: User wants to trade HBAR (Hedera) for USDT (Ethereum)
- Bot finds matching orders and coordinates the cross-chain settlement
"""

import asyncio
import logging
import os
import json
import time
from decimal import Decimal
from typing import Dict, List, Optional, Any
import httpx
from dataclasses import dataclass
from enum import Enum

from src.trade_settlement_client import SettlementClientSame
from helper.api_helper import APIHelper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

class CrossChainMarketMaker:
    def __init__(self):
        self.api_base_url = os.getenv("ORDERBOOK_API_URL", "http://localhost:8001")
        self.supported_networks = {
            NetworkType.HEDERA: {
                "rpc": os.getenv("HEDERA_RPC_URL", "https://testnet.hashio.io/api"),
                "chain_id": 296,
                "settlement": os.getenv("HEDERA_SETTLEMENT", "0xEBDe923E00809f9203256beBD59249df1c49Ec48")
            },
            NetworkType.ETHEREUM: {
                "rpc": os.getenv("ETHEREUM_RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com"),
                "chain_id": 11155111,
                "settlement": os.getenv("ETHEREUM_SETTLEMENT", "0x10F0F2cb456BEd15655afB22ddd7d0EEE11FdBc9")
            }
        }
        
        # Initialize settlement clients for each network
        self.settlement_clients: Dict[NetworkType, SettlementClientSame] = {}
        self.private_key = os.getenv("PRIVATE_KEY")
        
        if not self.private_key:
            raise ValueError("PRIVATE_KEY environment variable is required")
            
        self._initialize_settlement_clients()
        
        # Track active cross-chain orders and matches
        self.active_orders: Dict[str, CrossChainOrder] = {}
        self.pending_matches: Dict[str, CrossChainMatch] = {}
        
        # Bot configuration
        self.matching_tolerance = Decimal("0.005")  # 0.5% price tolerance
        self.min_trade_amount = Decimal("1.0")  # Minimum trade amount
        self.polling_interval = 5  # seconds
        
    def _initialize_settlement_clients(self):
        """Initialize settlement clients for each supported network"""
        for network_type, config in self.supported_networks.items():
            try:
                client = SettlementClientSame(
                    web3_provider=config["rpc"],
                    contract_address=config["settlement"],
                    private_key=self.private_key
                )
                self.settlement_clients[network_type] = client
                logger.info(f"Initialized settlement client for {network_type.value}")
            except Exception as e:
                logger.error(f"Failed to initialize settlement client for {network_type.value}: {e}")
                
    async def get_orderbook_orders(self, symbol: str = None) -> List[Dict]:
        """Fetch orders from the main orderbook API"""
        try:
            url = f"{self.api_base_url}/api/orderbook"
            data = {"symbol": symbol or "ALL"}
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=data)
                response.raise_for_status()
                result = response.json()
                
            if result.get("status_code") == 1:
                orders = []
                # Extract bids and asks
                for bid in result.get("bids", []):
                    orders.append({**bid, "side": "bid"})
                for ask in result.get("asks", []):
                    orders.append({**ask, "side": "ask"})
                return orders
            return []
        except Exception as e:
            logger.error(f"Failed to fetch orderbook orders: {e}")
            return []
    
    def is_cross_chain_order(self, order: Dict) -> bool:
        """Check if an order is a cross-chain order"""
        from_network = order.get("from_network", "").lower()
        to_network = order.get("to_network", "").lower()
        return from_network != to_network and from_network and to_network
    
    def parse_cross_chain_order(self, order: Dict) -> Optional[CrossChainOrder]:
        """Parse a raw order into a CrossChainOrder object"""
        try:
            if not self.is_cross_chain_order(order):
                return None
                
            from_network = order.get("from_network", "").lower()
            to_network = order.get("to_network", "").lower()
            
            # Map network strings to NetworkType
            network_map = {
                "hedera": NetworkType.HEDERA,
                "ethereum": NetworkType.ETHEREUM
            }
            
            from_net = network_map.get(from_network)
            to_net = network_map.get(to_network)
            
            if not from_net or not to_net:
                return None
                
            return CrossChainOrder(
                order_id=str(order.get("orderId", order.get("order_id", ""))),
                account=order.get("account", ""),
                side=order.get("side", ""),
                base_asset=order.get("baseAsset", ""),
                quote_asset=order.get("quoteAsset", ""),
                price=Decimal(str(order.get("price", 0))),
                quantity=Decimal(str(order.get("quantity", 0))),
                from_network=from_network,
                to_network=to_network,
                receive_wallet=order.get("receiveWallet", order.get("receive_wallet", "")),
                timestamp=int(order.get("timestamp", time.time())),
                network=from_net  # The network where the order originates
            )
        except Exception as e:
            logger.error(f"Failed to parse cross-chain order: {e}")
            return None
    
    def find_matching_orders(self, orders: List[CrossChainOrder]) -> List[CrossChainMatch]:
        """Find orders that can be matched cross-chain"""
        matches = []
        
        for i, order1 in enumerate(orders):
            for j, order2 in enumerate(orders[i+1:], i+1):
                match = self._check_order_compatibility(order1, order2)
                if match:
                    matches.append(match)
                    
        return matches
    
    def _check_order_compatibility(self, order1: CrossChainOrder, order2: CrossChainOrder) -> Optional[CrossChainMatch]:
        """Check if two orders can be matched for cross-chain trading"""
        # Must be opposite sides
        if order1.side == order2.side:
            return None
            
        # Must involve same assets but on opposite networks
        if not (order1.base_asset == order2.base_asset and order1.quote_asset == order2.quote_asset):
            return None
            
        # Network compatibility: order1's to_network should match order2's from_network and vice versa
        if not (order1.to_network == order2.from_network and order1.from_network == order2.to_network):
            return None
            
        # Price compatibility check
        if order1.side == "bid" and order2.side == "ask":
            # order1 is buying, order2 is selling
            if order1.price < order2.price * (1 - self.matching_tolerance):
                return None
            matched_price = order2.price  # Take the ask price
        elif order1.side == "ask" and order2.side == "bid":
            # order1 is selling, order2 is buying  
            if order2.price < order1.price * (1 - self.matching_tolerance):
                return None
            matched_price = order1.price  # Take the ask price
        else:
            return None
            
        # Quantity compatibility
        matched_quantity = min(order1.quantity, order2.quantity)
        if matched_quantity < self.min_trade_amount:
            return None
            
        # Generate trade ID
        trade_id = f"cross_{order1.order_id}_{order2.order_id}_{int(time.time())}"
        
        return CrossChainMatch(
            source_order=order1 if order1.side == "ask" else order2,
            dest_order=order2 if order1.side == "ask" else order1,
            trade_id=trade_id,
            matched_quantity=matched_quantity,
            matched_price=matched_price
        )
    
    async def execute_cross_chain_trade(self, match: CrossChainMatch) -> bool:
        """Execute a cross-chain trade settlement"""
        try:
            logger.info(f"Executing cross-chain trade: {match.trade_id}")
            
            # Prepare cross-chain trade data
            trade_data = self._prepare_trade_data(match)
            
            # Execute settlement on source chain (where base asset comes from)
            source_network = NetworkType(match.source_order.network.value)
            source_client = self.settlement_clients.get(source_network)
            
            if not source_client:
                logger.error(f"No settlement client for source network: {source_network}")
                return False
                
            # Execute settlement on destination chain (where quote asset comes from)
            dest_network = NetworkType(match.dest_order.network.value)
            dest_client = self.settlement_clients.get(dest_network)
            
            if not dest_client:
                logger.error(f"No settlement client for destination network: {dest_network}")
                return False
            
            # Execute settlements (this would call the smart contract settlement functions)
            source_success = await self._settle_on_chain(source_client, trade_data, is_source=True)
            dest_success = await self._settle_on_chain(dest_client, trade_data, is_source=False)
            
            if source_success and dest_success:
                logger.info(f"Cross-chain trade {match.trade_id} executed successfully")
                return True
            else:
                logger.error(f"Cross-chain trade {match.trade_id} failed")
                # TODO: Implement rollback mechanism
                return False
                
        except Exception as e:
            logger.error(f"Failed to execute cross-chain trade {match.trade_id}: {e}")
            return False
    
    def _prepare_trade_data(self, match: CrossChainMatch) -> Dict:
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
            "sourceChainId": self.supported_networks[match.source_order.network]["chain_id"],
            "destinationChainId": self.supported_networks[match.dest_order.network]["chain_id"],
            "timestamp": int(time.time()),
            "nonce1": 0,  # Get from settlement contract
            "nonce2": 0   # Get from settlement contract
        }
    
    async def _settle_on_chain(self, client: SettlementClientSame, trade_data: Dict, is_source: bool) -> bool:
        """Execute settlement on a specific chain"""
        try:
            # This would call the settleCrossChainTrade function on the smart contract
            # Implementation depends on your SettlementClient interface
            result = await client.settle_cross_chain_trade(trade_data, is_source)
            return result.get("success", False)
        except Exception as e:
            logger.error(f"Settlement failed on {'source' if is_source else 'destination'} chain: {e}")
            return False
    
    async def monitor_and_match(self):
        """Main monitoring loop for cross-chain order matching"""
        logger.info("Starting cross-chain market maker...")
        
        while True:
            try:
                # Fetch current orders
                raw_orders = await self.get_orderbook_orders()
                
                # Filter and parse cross-chain orders
                cross_chain_orders = []
                for raw_order in raw_orders:
                    parsed_order = self.parse_cross_chain_order(raw_order)
                    if parsed_order:
                        cross_chain_orders.append(parsed_order)
                
                logger.info(f"Found {len(cross_chain_orders)} cross-chain orders")
                
                # Find matching orders
                matches = self.find_matching_orders(cross_chain_orders)
                
                if matches:
                    logger.info(f"Found {len(matches)} potential cross-chain matches")
                    
                    # Execute matches
                    for match in matches:
                        success = await self.execute_cross_chain_trade(match)
                        if success:
                            logger.info(f"Successfully executed cross-chain trade: {match.trade_id}")
                        else:
                            logger.error(f"Failed to execute cross-chain trade: {match.trade_id}")
                
                # Wait before next iteration
                await asyncio.sleep(self.polling_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(self.polling_interval)

async def main():
    """Main entry point for the cross-chain market maker bot"""
    try:
        market_maker = CrossChainMarketMaker()
        await market_maker.monitor_and_match()
    except KeyboardInterrupt:
        logger.info("Shutting down cross-chain market maker...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    asyncio.run(main())