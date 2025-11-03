from decimal import Decimal

class Order(object):
    '''
    Orders represent the core piece of the exchange. Every bid/ask is an Order.
    Orders are doubly linked and have helper functions (next_order, prev_order)
    to help the exchange fullfill orders with quantities larger than a single
    existing Order.
    '''
    def __init__(self, quote, order_list):
        self.timestamp = int(quote['timestamp']) # integer representing the timestamp of order creation
        self.quantity = Decimal(quote['quantity']) # decimal representing amount of thing - can be partial amounts
        self.price = Decimal(quote['price']) # decimal representing price (currency)
        self.order_id = int(quote['order_id'])
        self.trade_id = quote['trade_id']
        # self.private_key = quote['private_key']
        # doubly linked list to make it easier to re-order Orders for a particular price point
        self.next_order = None
        self.prev_order = None
        self.order_list = order_list

        self.account = quote['account']
        self.side = quote['side']
        self.baseAsset = quote['baseAsset']
        self.quoteAsset = quote['quoteAsset']
        # Network information (source/destination) used for on-chain settlement
        # Expecting strings like 'hedera', 'polygon', etc.
        self.from_network = quote.get('from_network') if isinstance(quote, dict) else None
        self.to_network = quote.get('to_network') if isinstance(quote, dict) else None
        # Optional receive wallet on the destination chain (where this party wants to receive tokens)
        self.receive_wallet = quote.get('receive_wallet') if isinstance(quote, dict) else None

    # helper functions to get Orders in linked list
    def next_order(self):
        return self.next_order

    def prev_order(self):
        return self.prev_order

    def update_quantity(self, new_quantity, new_timestamp):
        if new_quantity > self.quantity and self.order_list.tail_order != self:
            # check to see that the order is not the last order in list and the quantity is more
            self.order_list.move_to_tail(self) # move to the end
        self.order_list.volume -= (self.quantity - new_quantity) # update volume
        self.timestamp = new_timestamp
        self.quantity = new_quantity

    def __str__(self):
        return "{}@{}/{} - {}".format(self.quantity, self.price,
                                      self.trade_id, self.timestamp)
