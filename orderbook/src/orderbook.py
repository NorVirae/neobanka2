import sys
import math
from collections import deque  # a faster insert/pop queue
from six.moves import cStringIO as StringIO
from decimal import Decimal
import json

from src.order import Order
from .ordertree import OrderTree
import time


class OrderBook(object):
    def __init__(self, tick_size=0.0001):
        self.tape = deque(maxlen=None)  # Index[0] is most recent trade
        self.bids = OrderTree()
        self.asks = OrderTree()
        self.last_tick = None
        self.last_timestamp = 0
        self.tick_size = tick_size
        self.time = 0
        self.next_order_id = 0

    def update_time(self):
        # self.time += 1
        self.time = int(time.time() * 1000)  # convert to milliseconds

    def process_order(self, quote, from_data, verbose):
        order_type = quote["type"]
        order_in_book = None
        task_id = 0
        next_best_order = None
        if from_data:
            self.time = quote["timestamp"]
        else:
            self.update_time()
            quote["timestamp"] = self.time
        if quote["quantity"] <= 0:
            sys.exit("process_order() given order of quantity <= 0")
        if not from_data:
            self.next_order_id += 1
        if order_type == "market":
            trades = self.process_market_order(quote, verbose)
        elif order_type == "limit":
            quote["price"] = Decimal(quote["price"])
            try:
                trades, order_in_book, task_id, next_best_order = (
                    self.process_limit_order(quote, from_data, verbose)
                )
            except Exception as e:
                raise Exception(str(e))
        else:
            sys.exit("order_type for process_order() is neither 'market' or 'limit'")

        return {
            "success": True,
            "data": [trades, order_in_book, task_id, next_best_order],
        }

    def process_order_list(
        self, side, order_list, quantity_still_to_trade, quote, verbose
    ):
        """
        Takes an OrderList (stack of orders at one price) and an incoming order and matches
        appropriate trades given the order's quantity.
        """
        trades = []
        quantity_to_trade = quantity_still_to_trade
        # Iterate through orders at this price level and try to match
        current_order: Order = order_list.head_order
        while current_order is not None and quantity_to_trade > 0:
            head_order = current_order
            next_order = head_order.next_order

            # Network compatibility check
            head_from = getattr(head_order, "from_network", None)
            head_to = getattr(head_order, "to_network", None)
            quote_from = quote.get("from_network") if isinstance(quote, dict) else None
            quote_to = quote.get("to_network") if isinstance(quote, dict) else None

            # Only match if both parties' networks are mutually compatible.
            # Required: head_from == quote_to AND head_to == quote_from
            networks_compatible = (head_from == quote_to and head_to == quote_from)

            if not networks_compatible:
                # Skip this order and continue to next available order at this price
                current_order = next_order
                continue

            traded_price = head_order.price
            counter_party = head_order.trade_id
            new_book_quantity = None

            if quantity_to_trade < head_order.quantity:
                traded_quantity = quantity_to_trade
                # Do the transaction (partial fill)
                new_book_quantity = head_order.quantity - quantity_to_trade
                head_order.update_quantity(new_book_quantity, head_order.timestamp)
                quantity_to_trade = 0
            elif quantity_to_trade == head_order.quantity:
                traded_quantity = quantity_to_trade
                # full fill - remove the order
                if side == "bid":
                    self.bids.remove_order_by_id(head_order.order_id)
                else:
                    self.asks.remove_order_by_id(head_order.order_id)
                quantity_to_trade = 0
            else:
                traded_quantity = head_order.quantity
                if side == "bid":
                    self.bids.remove_order_by_id(head_order.order_id)
                else:
                    self.asks.remove_order_by_id(head_order.order_id)
                quantity_to_trade -= traded_quantity

            if verbose:
                print(
                    (
                        "TRADE: Time - {}, Price - {}, Quantity - {}, TradeID - {}, Matching TradeID - {}".format(
                            self.time,
                            traded_price,
                            traded_quantity,
                            counter_party,
                            quote["trade_id"],
                        )
                    )
                )

            transaction_record = {
                "timestamp": self.time,
                "price": traded_price,
                "quantity": traded_quantity,
                "time": self.time,
            }

            # Build party arrays including network and receive wallet
            head_receive = getattr(head_order, "receive_wallet", None)
            quote_receive = quote.get("receive_wallet") if isinstance(quote, dict) else None

            if side == "bid":
                transaction_record["party1"] = [
                    counter_party,
                    "bid",
                    head_order.order_id,
                    new_book_quantity,
                    # head_order.private_key,
                    getattr(head_order, "from_network", None),
                    getattr(head_order, "to_network", None),
                    head_receive,
                    head_order.__dict__
                ]
                transaction_record["party2"] = [
                    quote["trade_id"],
                    "ask",
                    None,
                    None,
                    # quote["private_key"],
                    quote.get("from_network") if isinstance(quote, dict) else None,
                    quote.get("to_network") if isinstance(quote, dict) else None,
                    quote_receive,
                    quote
                ]
            else:
                transaction_record["party1"] = [
                    counter_party,
                    "ask",
                    head_order.order_id,
                    new_book_quantity,
                    # head_order.private_key,
                    getattr(head_order, "from_network", None),
                    getattr(head_order, "to_network", None),
                    head_receive,
                    head_order.__dict__
                ]
                transaction_record["party2"] = [
                    quote["trade_id"],
                    "bid",
                    None,
                    None,
                    # quote["private_key"],
                    quote.get("from_network") if isinstance(quote, dict) else None,
                    quote.get("to_network") if isinstance(quote, dict) else None,
                    quote_receive,
                    quote
                ]

            self.tape.append(transaction_record)
            trades.append(transaction_record)

            # Continue from next order
            current_order = next_order
        return quantity_to_trade, trades

    def process_market_order(self, quote, verbose):
        trades = []
        quantity_to_trade = quote["quantity"]
        side = quote["side"]
        if side == "bid":
            while quantity_to_trade > 0 and self.asks:
                best_price_asks = self.asks.min_price_list()
                quantity_to_trade, new_trades = self.process_order_list(
                    "ask", best_price_asks, quantity_to_trade, quote, verbose
                )
                trades += new_trades
        elif side == "ask":
            while quantity_to_trade > 0 and self.bids:
                best_price_bids = self.bids.max_price_list()
                quantity_to_trade, new_trades = self.process_order_list(
                    "bid", best_price_bids, quantity_to_trade, quote, verbose
                )
                trades += new_trades
        else:
            sys.exit('process_market_order() recieved neither "bid" nor "ask"')
        return trades

    def process_limit_order(self, quote, from_data, verbose):
        # Note: modified so that only one trade can happen
        # If the quote quantity is less than the best opposing side order quantity, partial fill
        # If the quote quantity is the same as the best opposing side order quantity, complete fill
        # If the quote quatity is larger than the best opposing side order quantity, reject this quote

        order_in_book = None
        trades = []
        quantity_to_trade = quote["quantity"]
        side = quote["side"]
        price = quote["price"]

        task_id = 0  # only set for partial or complete fills
        next_best_order = None

        if quantity_to_trade <= 0:
            raise Exception("No orders of size 0 or less")

        if side == "bid":
            # If we have asks, and we cross the spread, and we're covering more than one order, reject
            if self.asks and price >= self.asks.min_price():
                min_price_orders = self.asks.min_price_list()
                if quantity_to_trade < min_price_orders.head_order.quantity:
                    # Partial fill
                    task_id = 3
                elif quantity_to_trade == min_price_orders.head_order.quantity:
                    # Complete fill
                    task_id = 4
                    if len(min_price_orders) > 1:
                        next_best_order = min_price_orders[1]
                else:
                    # More than one order covered, reject this for now
                    # This is disabled for now as we only track and lock funds for the best order on-chain
                    raise Exception(
                        "Not currently accepting orders larger than best order"
                    )
            else:

                if (self.bids.max_price() is None) or (price > self.bids.max_price()):
                    task_id = 2
                else:
                    task_id = 1

            # Some of this is redundant now but should still work
            while (
                self.asks and price >= self.asks.min_price() and quantity_to_trade > 0
            ):
                best_price_asks = self.asks.min_price_list()
                quantity_to_trade, new_trades = self.process_order_list(
                    "ask", best_price_asks, quantity_to_trade, quote, verbose
                )
                trades += new_trades
            # If volume remains, need to update the book with new quantity
            if quantity_to_trade > 0:
                if not from_data:
                    quote["order_id"] = self.next_order_id
                quote["quantity"] = quantity_to_trade
                self.bids.insert_order(quote)
                order_in_book = quote
        elif side == "ask":
            # If we have bids, and we cross the spread, and we're covering more than one order, reject
            if self.bids and price <= self.bids.max_price():
                max_price_orders = self.bids.max_price_list()
                if quantity_to_trade < max_price_orders.head_order.quantity:
                    # Partial fill
                    task_id = 3
                elif quantity_to_trade == max_price_orders.head_order.quantity:
                    # Complete fill
                    task_id = 4
                    if len(max_price_orders) > 1:
                        next_best_order = max_price_orders.head_order.next_order
                else:
                    # More than one order covered, reject this for now
                    # This is disabled for now as we only track and lock funds for the best order on-chain
                    raise Exception(
                        "Not currently accepting orders larger than best order"
                    )
            else:
                if (self.asks.min_price() is None) or (price < self.asks.min_price()):
                    task_id = 2
                else:
                    task_id = 1

            # Partly redundant but should still work
            while (
                self.bids and price <= self.bids.max_price() and quantity_to_trade > 0
            ):
                best_price_bids = self.bids.max_price_list()
                quantity_to_trade, new_trades = self.process_order_list(
                    "bid", best_price_bids, quantity_to_trade, quote, verbose
                )
                trades += new_trades
            # If volume remains, need to update the book with new quantity
            if quantity_to_trade > 0:
                if not from_data:
                    quote["order_id"] = self.next_order_id
                quote["quantity"] = quantity_to_trade
                self.asks.insert_order(quote)
                order_in_book = quote
        else:
            sys.exit('process_limit_order() given neither "bid" nor "ask"')

        assert len(trades) == 1 or len(trades) == 0
        return trades, order_in_book, task_id, next_best_order

    def cancel_order(self, side, order_id, time=None):
        if time:
            self.time = time
        else:
            self.update_time()
        if side == "bid":
            if self.bids.order_exists(order_id):
                self.bids.remove_order_by_id(order_id)
        elif side == "ask":
            if self.asks.order_exists(order_id):
                self.asks.remove_order_by_id(order_id)
        else:
            sys.exit('cancel_order() given neither "bid" nor "ask"')

    def modify_order(self, order_id, order_update, time=None):
        if time:
            self.time = time
        else:
            self.update_time()
        side = order_update["side"]
        order_update["order_id"] = order_id
        order_update["timestamp"] = self.time
        if side == "bid":
            if self.bids.order_exists(order_update["order_id"]):
                self.bids.update_order(order_update)
        elif side == "ask":
            if self.asks.order_exists(order_update["order_id"]):
                self.asks.update_order(order_update)
        else:
            sys.exit('modify_order() given neither "bid" nor "ask"')

    def get_volume_at_price(self, side, price):
        price = Decimal(price)
        if side == "bid":
            volume = 0
            if self.bids.price_exists(price):
                volume = self.bids.get_price(price).volume
            return volume
        elif side == "ask":
            volume = 0
            if self.asks.price_exists(price):
                volume = self.asks.get_price(price).volume
            return volume
        else:
            sys.exit('get_volume_at_price() given neither "bid" nor "ask"')

    def get_best_bid(self):
        return self.bids.max_price()

    def get_worst_bid(self):
        return self.bids.min_price()

    def get_best_ask(self):
        return self.asks.min_price()

    def get_worst_ask(self):
        return self.asks.max_price()

    def tape_dump(self, filename, filemode, tapemode):
        dumpfile = open(filename, filemode)
        for tapeitem in self.tape:
            dumpfile.write(
                "Time: %s, Price: %s, Quantity: %s\n"
                % (tapeitem["time"], tapeitem["price"], tapeitem["quantity"])
            )
        dumpfile.close()
        if tapemode == "wipe":
            self.tape = []

    def __str__(self):
        tempfile = StringIO()
        tempfile.write("***Bids***\n")
        if self.bids != None and len(self.bids) > 0:
            for key, value in reversed(self.bids.price_map.items()):
                tempfile.write("%s" % value)
        tempfile.write("\n***Asks***\n")
        if self.asks != None and len(self.asks) > 0:
            for key, value in self.asks.price_map.items():
                tempfile.write("%s" % value)
        tempfile.write("\n***Trades***\n")
        if self.tape != None and len(self.tape) > 0:
            num = 0
            for entry in self.tape:
                if num < 10:  # get last 5 entries
                    tempfile.write(
                        str(entry["quantity"])
                        + " @ "
                        + str(entry["price"])
                        + " ("
                        + str(entry["timestamp"])
                        + ") "
                        + str(entry["party1"][0])
                        + "/"
                        + str(entry["party2"][0])
                        + "\n"
                    )
                    num += 1
                else:
                    break
        tempfile.write("\n")
        return tempfile.getvalue()

    def get_orderbook(self, symbol):
        base_asset = symbol.split("_")[0]
        quote_asset = symbol.split("_")[1]
        print(base_asset, quote_asset)

        orderbook = {
            "baseAsset": base_asset,
            "quoteAsset": quote_asset,
            # "lastTradePrice": 95362.08,
            # "priceChangeIndicator": "up",
            "asks": [],
            "bids": [],
        }

        for price in self.asks.prices:
            price_list = self.asks.price_map[price]
            current_order = price_list.head_order

            # Traverse the linked list of orders
            while current_order != None:
                orderbook["asks"].append(
                    {
                        "price": float(price),
                        "amount": float(current_order.quantity),
                        "total": float(price * current_order.quantity),
                        "account": current_order.account,
                        "orderId": current_order.order_id,
                        "from_network": getattr(current_order, "from_network", None),
                        "to_network": getattr(current_order, "to_network", None),
                    }
                )
                current_order = current_order.next_order

        for price in self.bids.prices:
            price_list = self.bids.price_map[price]
            current_order = price_list.head_order

            # Traverse the linked list of orders
            while current_order != None:
                orderbook["bids"].append(
                    {
                        "price": float(price),
                        "amount": float(current_order.quantity),
                        "total": float(price * current_order.quantity),
                        "account": current_order.account,
                        "orderId": current_order.order_id,
                        "from_network": getattr(current_order, "from_network", None),
                        "to_network": getattr(current_order, "to_network", None),
                    }
                )
                current_order = current_order.next_order

        return orderbook
