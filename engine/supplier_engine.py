"""Supplier behaviour: pricing, inventory, delivery and risk/defaults.

Pricing model (per §8 of the spec)::

    price = base_price * inventory_adjustment * market_factor + random_variation

  * ``base_price``  - product base cost * the supplier's premium (static)
  * ``market_factor`` - a per-product market index shared by all suppliers,
    so their prices are correlated but not identical.
  * ``inventory_adjustment`` - lower stock -> marginally higher price.
  * ``random_variation`` - a per-supplier AR(1) idiosyncratic component large
    enough that there is usually a clear cheapest supplier.

Risk model (per §9)::

    risk_score = f(historical_default_rate)

Defaults are realised at execution time by :mod:`engine.procurement_engine`;
this module maintains the latent default probability, the realised
(EWMA-smoothed) historical default rate and the derived risk score.
"""

from __future__ import annotations

from typing import Dict, List

from .config import SimulationConfig
from .entities import MarketProduct, Supplier
from .events import EventLog
from .rng import RandomStreams


class SupplierEngine:
    def __init__(self, config: SimulationConfig, rng: RandomStreams, log: EventLog):
        self.config = config
        self.rng = rng
        self.log = log

    # ------------------------------------------------------------------
    def update_market(self, products: Dict[str, MarketProduct]) -> None:
        """Advance the shared per-product market index (mean-reverting walk)."""
        cfg = self.config
        for p in products.values():
            shock = self.rng.gauss(f"market:{p.product_id}", 0.0, cfg.market_volatility)
            reversion = cfg.market_reversion * (1.0 - p.market_index)
            p.market_index += cfg.market_drift + reversion + shock
            # keep the index in a sane band
            p.market_index = max(0.5, min(2.0, p.market_index))
            p.last_market_price = p.base_cost * p.market_index

    # ------------------------------------------------------------------
    def update_prices(
        self,
        suppliers: Dict[str, Supplier],
        products: Dict[str, MarketProduct],
        timestamp: int,
    ) -> None:
        cfg = self.config
        for s in suppliers.values():
            for pid, ps in s.products.items():
                mp = products[pid]
                # inventory adjustment: marginal, centred on the supplier's own
                # initial inventory as the neutral reference point.
                ref = max(1.0, ps.initial_inventory)
                stock_ratio = ps.inventory / ref
                inv_adj = 1.0 + cfg.price_inventory_sensitivity * (1.0 - stock_ratio)
                inv_adj = max(0.90, min(1.12, inv_adj))

                # AR(1) idiosyncratic noise
                nkey = f"price_noise:{s.supplier_id}:{pid}"
                ps.price_noise_state = (
                    0.7 * ps.price_noise_state
                    + self.rng.gauss(nkey, 0.0, cfg.price_noise)
                )
                random_variation = ps.base_price * ps.price_noise_state

                new_price = ps.base_price * inv_adj * mp.market_index + random_variation
                new_price = max(0.1, new_price)

                if abs(new_price - ps.current_price) > 1e-9:
                    ps.current_price = new_price

    # ------------------------------------------------------------------
    def update_risk(self, suppliers: Dict[str, Supplier], timestamp: int) -> None:
        """Drift the latent default probability and recompute the risk score."""
        cfg = self.config
        for s in suppliers.values():
            drift = self.rng.gauss(f"defprob:{s.supplier_id}", 0.0, cfg.default_prob_drift)
            # mild mean-reversion toward the supplier's base level keeps long,
            # open-ended runs stationary instead of drifting to all-risky.
            reversion = cfg.default_prob_reversion * (s.base_default_probability - s.default_probability)
            s.default_probability = max(
                0.0, min(cfg.default_prob_max, s.default_probability + drift + reversion)
            )
            # risk score derives from the *realised* historical default rate
            s.risk_score = min(1.0, s.historical_default_rate ** cfg.risk_curve)

    # ------------------------------------------------------------------
    def register_order_outcome(self, supplier: Supplier, defaulted: bool, timestamp: int) -> None:
        """Update a supplier's realised default history after an order attempt."""
        cfg = self.config
        target = 1.0 if defaulted else 0.0
        supplier.historical_default_rate += cfg.default_history_smoothing * (
            target - supplier.historical_default_rate
        )
        supplier.historical_default_rate = max(0.0, min(1.0, supplier.historical_default_rate))
        if defaulted:
            supplier.defaults += 1
            # a real default also nudges the latent probability up
            supplier.default_probability = min(
                cfg.default_prob_max, supplier.default_probability + 0.03
            )
        else:
            supplier.successful_orders += 1
        supplier.risk_score = min(1.0, supplier.historical_default_rate ** cfg.risk_curve)

    # ------------------------------------------------------------------
    def restock(self, suppliers: Dict[str, Supplier], timestamp: int) -> None:
        """Periodically replenish supplier inventory so runs are open-ended."""
        cfg = self.config
        ticks = max(1, int(cfg.supplier_restock_days * cfg.ticks_per_day))
        if timestamp % ticks != 0 or timestamp == 0:
            return
        for s in suppliers.values():
            for pid, ps in s.products.items():
                add = ps.initial_inventory * cfg.supplier_restock_amount
                ps.inventory += add
            self.log.emit(
                timestamp, "supplier_restock",
                supplier_id=s.supplier_id,
            )
