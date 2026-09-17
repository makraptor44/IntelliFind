"""Central configuration for the procurement automation simulation.

Everything that governs the simulation's behaviour lives here as plain data so
that (a) the dashboard can expose the major knobs as live controls and (b) a
future AI optimisation layer can propose new parameter sets without touching the
engine code.

Convention used throughout the codebase:
  * Time is measured in *ticks*. ``ticks_per_day`` ticks make one simulated day.
  * A procurement *score* is a COST: **lower is better**.
  * Money is expressed in the abstract currency unit shown as ``£`` on the
    dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List
import copy


# ---------------------------------------------------------------------------
# Per-entity definitions
# ---------------------------------------------------------------------------
@dataclass
class ProductConfig:
    """Static definition of a product line.

    ``baseline_demand`` is the *product-level* mean daily demand.  Each shop
    receives a small independent multiplier around this value (see
    :class:`SimulationConfig.shop_demand_variation`) so shops are similar but
    never identical.
    """

    product_id: str
    name: str
    baseline_demand: float      # mean units/day across all shops
    retail_price: float         # revenue booked per unit sold to end customer
    base_cost: float            # reference wholesale unit cost (before market moves)


@dataclass
class SupplierConfig:
    """Static definition of a supplier."""

    supplier_id: str
    name: str
    delivery_days: float                 # nominal lead time in days
    base_default_probability: float      # baseline per-order chance of defaulting
    pack_size: int                       # bulk increment (units) this supplier ships in
    price_premium: float                 # multiplicative premium vs product base cost
    initial_inventory: int               # starting stock (per product)


# ---------------------------------------------------------------------------
# Master configuration
# ---------------------------------------------------------------------------
@dataclass
class SimulationConfig:
    # --- reproducibility -------------------------------------------------
    seed: int = 42

    # --- clock -----------------------------------------------------------
    ticks_per_day: int = 24              # 1 tick == 1 hour
    num_days: int = 60                   # length of a full run (0 == open ended)

    # --- topology --------------------------------------------------------
    num_shops: int = 5
    products: List[ProductConfig] = field(default_factory=lambda: [
        ProductConfig("product_A", "Product A", 12.0, 45.0, 22.0),
        ProductConfig("product_B", "Product B", 8.0,  38.0, 18.0),
        ProductConfig("product_C", "Product C", 5.0,  30.0, 14.0),
        ProductConfig("product_D", "Product D", 2.5,  60.0, 30.0),
        ProductConfig("product_E", "Product E", 1.0,  90.0, 46.0),
    ])
    suppliers: List[SupplierConfig] = field(default_factory=lambda: [
        # id            name          lead  default  pack  premium  init_inv
        SupplierConfig("supplier_A", "Supplier A", 2.0, 0.05, 10, 1.02, 900),
        SupplierConfig("supplier_B", "Supplier B", 5.0, 0.15, 25, 0.94, 1400),
        SupplierConfig("supplier_C", "Supplier C", 1.0, 0.02, 50, 1.06, 600),
    ])

    # --- demand model ----------------------------------------------------
    shop_demand_variation: float = 0.10   # std-dev of per-shop demand multiplier
    demand_noise: float = 0.18            # std-dev of multiplicative per-tick noise
    spike_frequency: float = 0.015        # per-shop-product per-tick spike probability
    spike_magnitude: float = 3.0          # spike size in *days* of extra baseline demand
    spike_decay: float = 0.70             # fraction of remaining spike carried to next tick

    # --- inventory / thresholds -----------------------------------------
    coverage_days: float = 3.0            # buffer days the shop wants on hand
    reorder_to_days: float = 6.0          # order up to this many days of cover
    initial_inventory_days: float = 4.5   # starting stock expressed in days of cover
    initial_inventory_jitter: float = 0.40

    # --- supplier pricing ------------------------------------------------
    market_drift: float = 0.0             # deterministic drift of the market index
    market_volatility: float = 0.015      # std-dev of per-tick market index move
    market_reversion: float = 0.02        # pull of the market index back toward 1.0
    price_inventory_sensitivity: float = 0.10   # how much stock swings price (marginal)
    price_noise: float = 0.05             # per-supplier idiosyncratic price noise
    supplier_restock_days: float = 3.0    # supplier restock cadence
    supplier_restock_amount: float = 1.0  # fraction of initial stock restocked each cycle

    # --- supplier risk / defaults ---------------------------------------
    default_prob_drift: float = 0.0015    # random walk of latent default probability
    default_prob_reversion: float = 0.01  # pull of the latent probability back to base
    default_prob_max: float = 0.45
    default_history_smoothing: float = 0.02   # EWMA weight for realised default rate
    risk_curve: float = 1.0               # exponent applied to default rate -> risk

    # --- procurement scoring weights (must sum to 1.0) ------------------
    w_price: float = 0.25
    w_risk: float = 0.25
    w_delivery: float = 0.25
    w_quantity: float = 0.25

    # --- procurement score normalisation (absolute reference scales) -----
    # Each component is mapped onto [0, 1] against a *stable* reference so that
    # scores are comparable across suppliers, over time and between strategies
    # (unlike per-tick min-max, which only ranks within one instant).
    price_score_lo: float = 0.55          # price/base_cost mapped from here...
    price_score_hi: float = 1.35          # ...to here -> [0, 1]
    risk_norm: float = 0.40               # default-rate that maps to a risk score of 1
    delivery_norm: float = 7.0            # delivery days that maps to a delivery score of 1
    quantity_norm: float = 1.00           # excess fraction that maps to a mismatch score of 1

    # --- order routing ---------------------------------------------------
    min_split_ratio: float = 0.40         # smaller leg of a scattered order >= 40%
    scatter_score_delta: float = 0.08     # split only if 2nd-best score within this of best
    enable_scattered_orders: bool = True
    limit_order_score: float = 0.35       # default max score for auto limit orders
    auto_limit_order_frequency: float = 0.004  # chance/shop/product/tick to place one

    # --- financial model -------------------------------------------------
    stockout_penalty_per_unit: float = 0.60   # * retail_price, goodwill loss per lost unit
    delay_cost_per_unit: float = 0.20         # * retail_price, cost of a delayed repair
    holding_cost_per_unit_day: float = 0.02   # * base_cost, carrying cost
    satisfaction_recovery: float = 0.4        # points regained per healthy day
    satisfaction_stockout_hit: float = 6.0    # points lost per unit-day of stockout (scaled)
    churn_sensitivity: float = 0.006          # demand multiplier loss per satisfaction point below target
    satisfaction_target: float = 90.0
    initial_satisfaction: float = 95.0
    initial_cash: float = 50000.0

    # --- score vs P&L tracking ------------------------------------------
    outcome_horizon_days: float = 6.0     # window over which an order's P&L is attributed

    # --- intra-retail trading -------------------------------------------
    enable_intra_retail: bool = True
    excess_ratio: float = 1.15            # inventory > threshold * this == "excess"
    platform_fee: float = 0.0             # optional spread on swaps (fraction)
    max_swaps_per_tick: int = 2

    # ------------------------------------------------------------------
    def validate(self) -> None:
        wsum = self.w_price + self.w_risk + self.w_delivery + self.w_quantity
        if abs(wsum - 1.0) > 1e-6:
            raise ValueError(f"procurement weights must sum to 1.0, got {wsum:.4f}")
        if self.ticks_per_day <= 0:
            raise ValueError("ticks_per_day must be positive")
        if not self.products:
            raise ValueError("at least one product required")
        if not self.suppliers:
            raise ValueError("at least one supplier required")

    def normalize_weights(self) -> None:
        """Rescale the four weights so they sum to 1.0 (used after UI edits)."""
        wsum = self.w_price + self.w_risk + self.w_delivery + self.w_quantity
        if wsum <= 0:
            self.w_price = self.w_risk = self.w_delivery = self.w_quantity = 0.25
            return
        self.w_price /= wsum
        self.w_risk /= wsum
        self.w_delivery /= wsum
        self.w_quantity /= wsum

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict:
        return asdict(self)

    def clone(self) -> "SimulationConfig":
        return copy.deepcopy(self)

    # ------------------------------------------------------------------
    @classmethod
    def from_overrides(cls, overrides: Dict) -> "SimulationConfig":
        """Build a config from the default, applying a flat dict of overrides.

        Only scalar top-level fields are overridable this way (the fields the
        dashboard exposes).  Unknown keys are ignored so the UI and engine can
        evolve independently.
        """
        cfg = cls()
        cfg.apply_overrides(overrides)
        return cfg

    def apply_overrides(self, overrides: Dict) -> None:
        overrides = overrides or {}
        scalar_fields = {
            f for f in self.__dataclass_fields__
            if f not in ("products", "suppliers")
        }
        for key, value in overrides.items():
            if key in scalar_fields and value is not None:
                current = getattr(self, key)
                try:
                    if isinstance(current, bool):
                        setattr(self, key, bool(value))
                    elif isinstance(current, int) and not isinstance(current, bool):
                        setattr(self, key, int(value))
                    elif isinstance(current, float):
                        setattr(self, key, float(value))
                    else:
                        setattr(self, key, value)
                except (TypeError, ValueError):
                    continue

        # nested edits: per-product economics and per-supplier terms (§31)
        self._apply_entity_overrides(
            self.products, overrides.get("product_overrides"), "product_id",
            {"retail_price": float, "base_cost": float, "baseline_demand": float},
        )
        self._apply_entity_overrides(
            self.suppliers, overrides.get("supplier_overrides"), "supplier_id",
            {"delivery_days": float, "base_default_probability": float,
             "pack_size": int, "price_premium": float, "initial_inventory": int},
        )
        self.normalize_weights()

    @staticmethod
    def _apply_entity_overrides(entities, edits, id_field, fields) -> None:
        if not edits:
            return
        by_id = {getattr(e, id_field): e for e in entities}
        for ent_id, changes in edits.items():
            ent = by_id.get(ent_id)
            if ent is None or not isinstance(changes, dict):
                continue
            for field_name, caster in fields.items():
                if field_name in changes and changes[field_name] is not None:
                    try:
                        setattr(ent, field_name, caster(changes[field_name]))
                    except (TypeError, ValueError):
                        continue
