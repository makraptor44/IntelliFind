"""Inventory accounting and threshold logic.

Required inventory (per §5)::

    Required = Expected Daily Sales Rate * (Coverage Days + Supplier Lead Time)

The engine also classifies each shop/product into a human-readable status used
by the dashboard's colour indicators, and converts demand into sales while
tracking unmet demand (stockouts).
"""

from __future__ import annotations

from typing import Dict, Tuple

from .config import SimulationConfig
from .entities import RepairShop


# status labels
HEALTHY = "Healthy"
APPROACHING = "Approaching threshold"
PROCURE = "Procurement required"
STOCKOUT_RISK = "Stockout risk"
STOCKOUT = "Stockout"
EXCESS = "Excess inventory"


class InventoryEngine:
    def __init__(self, config: SimulationConfig):
        self.config = config

    # ------------------------------------------------------------------
    def compute_threshold(self, sales_rate: float, expected_lead_days: float) -> float:
        cfg = self.config
        return max(0.0, sales_rate * (cfg.coverage_days + expected_lead_days))

    def reorder_target(self, sales_rate: float, expected_lead_days: float) -> float:
        cfg = self.config
        return max(0.0, sales_rate * (cfg.reorder_to_days + expected_lead_days))

    # ------------------------------------------------------------------
    def days_of_inventory(self, inventory: float, sales_rate: float) -> float:
        if sales_rate <= 1e-9:
            return float("inf")
        return inventory / sales_rate

    def runway(self, inventory: float, incoming: float, sales_rate: float) -> float:
        """Days of cover including inventory currently on hand only."""
        return self.days_of_inventory(inventory, sales_rate)

    # ------------------------------------------------------------------
    def status(
        self,
        inventory: float,
        incoming: float,
        threshold: float,
        sales_rate: float,
        expected_lead_days: float,
    ) -> str:
        cfg = self.config
        if inventory <= 1e-6:
            return STOCKOUT
        runway = self.days_of_inventory(inventory, sales_rate)
        if inventory > threshold * cfg.excess_ratio:
            return EXCESS
        if runway < expected_lead_days and incoming <= 1e-6:
            return STOCKOUT_RISK
        if inventory < threshold:
            # if replenishment is already inbound we're merely approaching
            return APPROACHING if incoming > 1e-6 else PROCURE
        if inventory < threshold * 1.15:
            return APPROACHING
        return HEALTHY

    # ------------------------------------------------------------------
    def consume(self, shop: RepairShop, product_id: str, demand: float) -> Tuple[float, float]:
        """Deduct demand from inventory. Returns (units_sold, units_unmet)."""
        st = shop.products[product_id]
        sold = min(st.inventory, demand)
        st.inventory -= sold
        if st.inventory < 0:
            st.inventory = 0.0
        unmet = max(0.0, demand - sold)
        st.cumulative_sales += sold
        st.cumulative_lost += unmet
        return sold, unmet
