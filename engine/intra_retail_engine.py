"""Peer-to-peer inventory trading between repair shops (§16-19).

The centralised platform looks for *complementary* inventory positions: shop X
is short of product P but long product Q, while shop Y is short Q but long P.
It then proposes a swap priced by the two products' current market prices::

    Exchange Ratio = Price(P) / Price(Q)

so the economic value exchanged is (approximately) balanced.  All swaps flow
through the platform, demonstrating the network value of shared procurement.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .config import SimulationConfig
from .entities import MarketProduct, RepairShop
from .events import EventLog
from .inventory_engine import InventoryEngine
from .order_book import IntraRetailTrade, OrderBook


class IntraRetailEngine:
    def __init__(self, config: SimulationConfig, inventory: InventoryEngine, log: EventLog):
        self.config = config
        self.inventory = inventory
        self.log = log

    # ------------------------------------------------------------------
    def _needs(self, shop: RepairShop, product_id: str, expected_lead_days: float) -> bool:
        """A shop needs a product if its runway is shorter than the lead time."""
        st = shop.products[product_id]
        if st.sales_rate <= 1e-9:
            return False
        runway = self.inventory.days_of_inventory(st.inventory, st.sales_rate)
        return runway < expected_lead_days and st.inventory < st.threshold

    def _excess(self, shop: RepairShop, product_id: str) -> float:
        """Units available to give away (above the excess threshold), else 0."""
        st = shop.products[product_id]
        limit = st.threshold * self.config.excess_ratio
        return max(0.0, st.inventory - limit)

    # ------------------------------------------------------------------
    def find_and_execute(
        self,
        shops: Dict[str, RepairShop],
        products: Dict[str, MarketProduct],
        book: OrderBook,
        timestamp: int,
        expected_lead_days: float,
    ) -> List[IntraRetailTrade]:
        if not self.config.enable_intra_retail:
            return []

        executed: List[IntraRetailTrade] = []
        shop_list = list(shops.values())
        product_ids = list(products.keys())

        for i in range(len(shop_list)):
            for j in range(len(shop_list)):
                if i == j:
                    continue
                if len(executed) >= self.config.max_swaps_per_tick:
                    return executed
                shop_a, shop_b = shop_list[i], shop_list[j]

                # look for complementary product pair (P, Q)
                for pid in product_ids:
                    if not self._needs(shop_a, pid, expected_lead_days):
                        continue
                    if self._excess(shop_b, pid) <= 0:
                        continue
                    for qid in product_ids:
                        if qid == pid:
                            continue
                        # shop_a must be long Q, shop_b short Q
                        if self._excess(shop_a, qid) <= 0:
                            continue
                        if not self._needs(shop_b, qid, expected_lead_days):
                            continue
                        trade = self._execute_swap(
                            shop_a, shop_b, pid, qid, products, book, timestamp
                        )
                        if trade is not None:
                            executed.append(trade)
                            break
                    if len(executed) >= self.config.max_swaps_per_tick:
                        return executed
        return executed

    # ------------------------------------------------------------------
    def _execute_swap(
        self,
        shop_a: RepairShop,   # needs P, has excess Q
        shop_b: RepairShop,   # has excess P, needs Q
        pid: str,             # product P (a receives, b gives)
        qid: str,             # product Q (a gives, b receives)
        products: Dict[str, MarketProduct],
        book: OrderBook,
        timestamp: int,
    ) -> Optional[IntraRetailTrade]:
        price_p = max(1e-6, products[pid].last_market_price)
        price_q = max(1e-6, products[qid].last_market_price)

        # how much P shop_a wants: enough to reach its threshold, capped by
        # what shop_b can spare.
        need_p = shop_a.products[pid].threshold - shop_a.products[pid].inventory
        avail_p = self._excess(shop_b, pid)
        qty_p = max(0.0, min(need_p, avail_p))
        if qty_p < 1.0:
            return None

        # value-balanced counter-quantity of Q (P is worth price_p/price_q of Q)
        ratio = price_p / price_q  # 1 unit P == `ratio` units Q
        qty_q = qty_p * ratio * (1.0 + self.config.platform_fee)

        # constrain by what shop_a can spare and shop_b needs
        avail_q = self._excess(shop_a, qid)
        need_q = shop_b.products[qid].threshold - shop_b.products[qid].inventory
        max_q = min(avail_q, max(0.0, need_q))
        if qty_q > max_q:
            if max_q < 1.0:
                return None
            qty_q = max_q
            qty_p = (qty_q / (1.0 + self.config.platform_fee)) / ratio
            if qty_p < 1.0:
                return None

        # execute: move inventory both ways
        shop_b.products[pid].inventory -= qty_p
        shop_a.products[pid].inventory += qty_p
        shop_a.products[qid].inventory -= qty_q
        shop_b.products[qid].inventory += qty_q

        trade = IntraRetailTrade(
            trade_id=book.next_id("ir"),
            seller_id=shop_b.shop_id,   # gave P to A
            buyer_id=shop_a.shop_id,
            product_given=pid,
            product_received=qid,
            qty_given=round(qty_p, 2),
            qty_received=round(qty_q, 2),
            exchange_ratio=round(ratio, 4),
            time=timestamp,
        )
        book.add_intra_trade(trade)
        self.log.emit(
            timestamp, "intra_retail_trade",
            trade_id=trade.trade_id,
            shop_a=shop_a.shop_id, shop_b=shop_b.shop_id,
            product_given=pid, product_received=qid,
            qty_p=trade.qty_given, qty_q=trade.qty_received,
            exchange_ratio=trade.exchange_ratio,
        )
        return trade
