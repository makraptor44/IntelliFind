"""Demand generation.

Demand is produced by a predefined stochastic process (no ML/forecasting yet):

    actual_demand = baseline_demand * (1 + noise) + occasional_spike

where
  * ``baseline_demand`` is the shop's own per-product baseline (a small random
    variation around the shared product-level mean),
  * ``noise`` is multiplicative Gaussian noise applied every tick,
  * ``occasional_spike`` fires rarely and then decays over a few ticks so that
    a spike is a short burst rather than a single isolated tick.

Customer satisfaction feeds back into demand: unhappy customers churn, which
lowers the effective demand multiplier (see :meth:`churn_multiplier`).
"""

from __future__ import annotations

from typing import Dict

from .config import SimulationConfig
from .entities import RepairShop
from .rng import RandomStreams


class DemandEngine:
    def __init__(self, config: SimulationConfig, rng: RandomStreams):
        self.config = config
        self.rng = rng

    # ------------------------------------------------------------------
    def churn_multiplier(self, shop: RepairShop) -> float:
        """Demand scaling from customer satisfaction (churn effect).

        At or above the satisfaction target the multiplier is 1.0.  Below it,
        demand erodes in proportion to ``churn_sensitivity`` — the long-run link
        between poor service and lost future revenue.
        """
        cfg = self.config
        deficit = max(0.0, cfg.satisfaction_target - shop.satisfaction)
        return max(0.30, 1.0 - deficit * cfg.churn_sensitivity)

    # ------------------------------------------------------------------
    def tick_demand(self, shop: RepairShop, product_id: str) -> Dict[str, float]:
        """Compute demand (in units for this tick) for one shop/product.

        Returns a breakdown so the dashboard can show actual-vs-baseline demand.
        """
        cfg = self.config
        st = shop.products[product_id]
        per_tick_baseline = st.base_daily_demand / cfg.ticks_per_day

        # multiplicative noise
        noise_key = f"demand_noise:{shop.shop_id}:{product_id}"
        noise = self.rng.gauss(noise_key, 0.0, cfg.demand_noise)

        # occasional spike -> seeds a residual that decays over subsequent ticks.
        # ``spike_magnitude`` is expressed in *days of extra demand*: a single
        # spike adds roughly ``magnitude`` days of baseline demand over its life
        # (front-loaded, then decaying), which is enough to occasionally outrun
        # the coverage buffer and cause a genuine stockout.
        spike_key = f"spike:{shop.shop_id}:{product_id}"
        spike_add = 0.0
        if self.rng.chance(spike_key, cfg.spike_frequency):
            mag_key = f"spike_mag:{shop.shop_id}:{product_id}"
            days = self.rng.uniform(mag_key, 0.4, 1.0) * cfg.spike_magnitude
            st.spike_residual += days * st.base_daily_demand * (1.0 - cfg.spike_decay)

        spike_add = st.spike_residual
        st.spike_residual *= cfg.spike_decay
        if st.spike_residual < 1e-4:
            st.spike_residual = 0.0

        churn = self.churn_multiplier(shop)
        baseline_component = per_tick_baseline * churn
        demand = baseline_component * (1.0 + noise) + spike_add
        demand = max(0.0, demand)

        return {
            "demand": demand,
            "baseline": baseline_component,
            "noise": noise,
            "spike": spike_add,
            "churn": churn,
            "per_tick_baseline": per_tick_baseline,
        }
