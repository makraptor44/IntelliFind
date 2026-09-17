"""Revenue, costs, customer satisfaction and P&L (§20-21).

P&L is deliberately more than ``revenue - purchase price``.  Each tick a shop
books:

  * revenue          from units actually sold,
  * procurement cost paid to suppliers (booked when orders execute elsewhere),
  * stockout cost    a goodwill/lost-margin penalty on unmet demand,
  * delay cost       cost of repairs delayed by missing parts,
  * holding cost     carrying cost on inventory on hand.

Unmet demand also drives customer satisfaction down; low satisfaction feeds back
into future demand (churn) in :mod:`engine.demand_engine`, so procurement
quality shows up in long-term P&L rather than just the immediate transaction.
"""

from __future__ import annotations

from typing import Dict

from .config import SimulationConfig
from .entities import MarketProduct, RepairShop
from .events import EventLog


class FinancialEngine:
    def __init__(self, config: SimulationConfig, log: EventLog):
        self.config = config
        self.log = log

    # ------------------------------------------------------------------
    def book_sale(self, shop: RepairShop, product: MarketProduct, units: float) -> float:
        if units <= 0:
            return 0.0
        revenue = units * product.retail_price
        shop.revenue += revenue
        shop.cash += revenue
        return revenue

    # ------------------------------------------------------------------
    def book_stockout(
        self, shop: RepairShop, product: MarketProduct, unmet_units: float, timestamp: int
    ) -> Dict[str, float]:
        """Apply stockout + delay penalties and reduce satisfaction."""
        cfg = self.config
        if unmet_units <= 1e-9:
            return {"stockout_cost": 0.0, "delay_cost": 0.0, "satisfaction_delta": 0.0}

        stockout_cost = unmet_units * product.retail_price * cfg.stockout_penalty_per_unit
        delay_cost = unmet_units * product.retail_price * cfg.delay_cost_per_unit
        shop.stockout_cost += stockout_cost
        shop.delay_cost += delay_cost

        # satisfaction hit scaled by the size of the shortfall relative to demand
        rate_per_tick = max(1e-6, shop.products[product.product_id].sales_rate / cfg.ticks_per_day)
        severity = min(3.0, unmet_units / rate_per_tick)
        sat_delta = -cfg.satisfaction_stockout_hit * severity / cfg.ticks_per_day
        self._adjust_satisfaction(shop, sat_delta, timestamp, "stockout")

        self.log.emit(
            timestamp, "stockout",
            shop_id=shop.shop_id, product_id=product.product_id,
            unmet_units=round(unmet_units, 3),
            stockout_cost=round(stockout_cost, 2), delay_cost=round(delay_cost, 2),
        )
        return {
            "stockout_cost": stockout_cost,
            "delay_cost": delay_cost,
            "satisfaction_delta": sat_delta,
        }

    # ------------------------------------------------------------------
    def apply_holding_cost(
        self, shop: RepairShop, products: Dict[str, MarketProduct]
    ) -> float:
        cfg = self.config
        total = 0.0
        per_tick = cfg.holding_cost_per_unit_day / cfg.ticks_per_day
        for pid, st in shop.products.items():
            total += st.inventory * products[pid].base_cost * per_tick
        shop.holding_cost += total
        shop.cash -= total
        return total

    # ------------------------------------------------------------------
    def recover_satisfaction(self, shop: RepairShop, timestamp: int, healthy: bool) -> None:
        cfg = self.config
        if healthy and shop.satisfaction < 100.0:
            delta = cfg.satisfaction_recovery / cfg.ticks_per_day
            self._adjust_satisfaction(shop, delta, timestamp, "recovery")

    def _adjust_satisfaction(self, shop: RepairShop, delta: float, timestamp: int, cause: str) -> None:
        before = shop.satisfaction
        shop.satisfaction = max(0.0, min(100.0, shop.satisfaction + delta))
        if abs(shop.satisfaction - before) > 1e-6:
            self.log.emit(
                timestamp, "customer_satisfaction_change",
                shop_id=shop.shop_id, cause=cause,
                satisfaction=round(shop.satisfaction, 3), delta=round(delta, 4),
            )

    # ------------------------------------------------------------------
    def update_pnl(self, shop: RepairShop, timestamp: int, tick_costs: Dict[str, float]) -> None:
        """Roll the tick's economic result into instantaneous and long-term P&L."""
        instant = (
            tick_costs.get("revenue", 0.0)
            - tick_costs.get("procurement_cost", 0.0)
            - tick_costs.get("stockout_cost", 0.0)
            - tick_costs.get("delay_cost", 0.0)
            - tick_costs.get("holding_cost", 0.0)
        )
        shop.instant_pnl = instant
        shop.pnl += instant
        self.log.emit(
            timestamp, "pnl_update",
            shop_id=shop.shop_id, instant_pnl=round(instant, 2),
            cumulative_pnl=round(shop.pnl, 2),
            revenue=round(tick_costs.get("revenue", 0.0), 2),
            procurement_cost=round(tick_costs.get("procurement_cost", 0.0), 2),
            stockout_cost=round(tick_costs.get("stockout_cost", 0.0), 2),
        )
