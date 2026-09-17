"""Mutable runtime state objects for shops, suppliers and market products.

The static definitions live in :mod:`engine.config`; the objects here carry the
values that evolve as the simulation runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


# ---------------------------------------------------------------------------
@dataclass
class MarketProduct:
    """The shared, evolving market state for a single product."""

    product_id: str
    name: str
    retail_price: float
    base_cost: float
    baseline_demand: float
    market_index: float = 1.0        # shared multiplicative market-price factor
    last_market_price: float = 0.0   # base_cost * market_index (reference "spot")


# ---------------------------------------------------------------------------
@dataclass
class ShopProductState:
    """Per-product state held by a repair shop."""

    inventory: float = 0.0
    sales_rate: float = 0.0          # estimated units/day (EWMA of realised sales)
    base_daily_demand: float = 0.0   # this shop's own baseline for the product
    demand_multiplier: float = 1.0   # persistent per-shop preference around 1.0
    spike_residual: float = 0.0      # decaying carry-over of an active demand spike
    threshold: float = 0.0           # required inventory level
    incoming: float = 0.0            # units on order and not yet delivered
    cumulative_sales: float = 0.0
    cumulative_lost: float = 0.0
    history: List[float] = field(default_factory=list)  # recent realised daily demand


@dataclass
class RepairShop:
    shop_id: str
    name: str
    cash: float
    satisfaction: float
    products: Dict[str, ShopProductState] = field(default_factory=dict)

    # financial accumulators
    revenue: float = 0.0
    procurement_cost: float = 0.0
    stockout_cost: float = 0.0
    delay_cost: float = 0.0
    holding_cost: float = 0.0
    pnl: float = 0.0                 # cumulative long-term P&L
    instant_pnl: float = 0.0        # P&L booked this tick

    open_market_orders: List[str] = field(default_factory=list)   # order ids
    open_limit_orders: List[str] = field(default_factory=list)     # order ids


# ---------------------------------------------------------------------------
@dataclass
class SupplierProductState:
    """Per-product state held by a supplier."""

    inventory: float = 0.0
    initial_inventory: float = 0.0
    base_price: float = 0.0          # product base cost * premium
    current_price: float = 0.0
    price_noise_state: float = 0.0   # AR(1) idiosyncratic noise component


@dataclass
class Supplier:
    supplier_id: str
    name: str
    delivery_days: float
    pack_size: int
    base_default_probability: float  # long-run level the latent probability reverts to
    default_probability: float       # latent per-order chance of default (drifts)
    historical_default_rate: float   # realised EWMA default rate -> feeds risk
    risk_score: float                # derived attractiveness penalty
    products: Dict[str, SupplierProductState] = field(default_factory=dict)

    successful_orders: int = 0
    defaults: int = 0
    outstanding_orders: List[str] = field(default_factory=list)
