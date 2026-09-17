"""The live order book.

Holds three kinds of orders, each with a simple lifecycle:

  * market orders        - execute immediately against the best supplier, then
                           sit ``in_transit`` until delivery (or default).
  * limit orders         - wait in the book until the best available score drops
                           to at or below the shop's maximum acceptable score.
  * intra-retail trades  - peer-to-peer product swaps between two shops.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


@dataclass
class MarketOrder:
    order_id: str
    shop_id: str
    product_id: str
    quantity: float
    supplier_id: str
    score: float
    unit_price: float
    total_cost: float
    order_time: int
    expected_delivery: int
    status: str = "in_transit"       # in_transit | delivered | defaulted
    delivery_days: float = 0.0
    quantity_mismatch: float = 0.0
    risk_score: float = 0.0
    is_split_leg: bool = False
    parent_id: Optional[str] = None


@dataclass
class LimitOrder:
    order_id: str
    shop_id: str
    product_id: str
    quantity: float
    max_score: float
    created_time: int
    status: str = "open"             # open | executed | cancelled
    current_best_score: Optional[float] = None
    executed_time: Optional[int] = None
    executed_supplier: Optional[str] = None


@dataclass
class IntraRetailTrade:
    trade_id: str
    seller_id: str                   # shop giving product_given
    buyer_id: str                    # shop receiving product_given
    product_given: str
    product_received: str
    qty_given: float
    qty_received: float
    exchange_ratio: float
    time: int
    status: str = "executed"


class OrderBook:
    def __init__(self):
        self.market_orders: Dict[str, MarketOrder] = {}
        self.limit_orders: Dict[str, LimitOrder] = {}
        self.intra_trades: Dict[str, IntraRetailTrade] = {}
        self._counter = 0

    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    # -- market ----------------------------------------------------------
    def add_market_order(self, order: MarketOrder) -> None:
        self.market_orders[order.order_id] = order

    def pending_deliveries(self, timestamp: int) -> List[MarketOrder]:
        return [
            o for o in self.market_orders.values()
            if o.status == "in_transit" and o.expected_delivery <= timestamp
        ]

    # -- limit -----------------------------------------------------------
    def add_limit_order(self, order: LimitOrder) -> None:
        self.limit_orders[order.order_id] = order

    def open_limit_orders(self) -> List[LimitOrder]:
        return [o for o in self.limit_orders.values() if o.status == "open"]

    # -- intra-retail ----------------------------------------------------
    def add_intra_trade(self, trade: IntraRetailTrade) -> None:
        self.intra_trades[trade.trade_id] = trade

    # -- snapshots -------------------------------------------------------
    def snapshot(self, recent: int = 40) -> Dict:
        def recent_sorted(values, key):
            return sorted(values, key=key, reverse=True)[:recent]

        markets = recent_sorted(self.market_orders.values(), lambda o: o.order_time)
        limits = sorted(
            self.limit_orders.values(),
            key=lambda o: (0 if o.status == "open" else 1, -o.created_time),
        )[:recent]
        trades = recent_sorted(self.intra_trades.values(), lambda t: t.time)
        return {
            "market_orders": [asdict(o) for o in markets],
            "limit_orders": [asdict(o) for o in limits],
            "intra_trades": [asdict(t) for t in trades],
            "counts": {
                "market_open": sum(1 for o in self.market_orders.values() if o.status == "in_transit"),
                "market_total": len(self.market_orders),
                "limit_open": len(self.open_limit_orders()),
                "limit_total": len(self.limit_orders),
                "intra_total": len(self.intra_trades),
            },
        }

    def clear(self) -> None:
        self.market_orders.clear()
        self.limit_orders.clear()
        self.intra_trades.clear()
        self._counter = 0
