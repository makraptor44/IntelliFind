"""Procurement-strategy sweep.

Runs the simulation headlessly under several procurement-weight configurations
and reports the long-term P&L of each.  This is the brute-force precursor to the
future AI optimisation layer (§22): it demonstrates that different weight sets
produce different long-term P&L, and surfaces which configuration performed best
for a given scenario/seed.

Nothing here is AI — it is an exhaustive/random search — but it produces exactly
the ``(parameter set -> long-term P&L)`` training signal the AI layer will learn
from.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

from engine.config import SimulationConfig
from engine.simulation import Simulation


def _run_one(base: SimulationConfig, weights: Tuple[float, float, float, float],
             days: int) -> Dict:
    cfg = base.clone()
    cfg.w_price, cfg.w_risk, cfg.w_delivery, cfg.w_quantity = weights
    cfg.normalize_weights()
    cfg.num_days = days
    sim = Simulation(cfg)
    sim.run(days * cfg.ticks_per_day)
    ov = sim.snapshot(include_history=False)["overview"]
    avg_score = (
        sum(s["score"] for s in sim._outcome_samples) / len(sim._outcome_samples)
        if sim._outcome_samples else None
    )
    return {
        "weights": {
            "price": round(cfg.w_price, 3), "risk": round(cfg.w_risk, 3),
            "delivery": round(cfg.w_delivery, 3), "quantity": round(cfg.w_quantity, 3),
        },
        "total_pnl": ov["total_pnl"],
        "avg_satisfaction": ov["avg_satisfaction"],
        "num_stockouts": ov["num_stockouts"],
        "num_defaults": ov["num_defaults"],
        "num_intra_trades": ov["num_intra_trades"],
        "avg_procurement_score": round(avg_score, 4) if avg_score is not None else None,
        "score_pnl_correlation": ov["score_pnl_correlation"],
    }


def default_weight_grid() -> List[Tuple[float, float, float, float]]:
    """A small, legible set of contrasting strategies for the demo."""
    return [
        (1.00, 0.00, 0.00, 0.00),   # naive: cheapest supplier wins
        (0.70, 0.10, 0.10, 0.10),   # price-led
        (0.25, 0.25, 0.25, 0.25),   # balanced (default)
        (0.10, 0.40, 0.40, 0.10),   # risk & delivery aware
        (0.20, 0.40, 0.20, 0.20),   # risk-led
        (0.20, 0.20, 0.40, 0.20),   # delivery-led
        (0.15, 0.30, 0.30, 0.25),   # quality-led
        (0.00, 0.45, 0.45, 0.10),   # ignore price entirely
    ]


def run_sweep(
    base: Optional[SimulationConfig] = None,
    weight_grid: Optional[List[Tuple[float, float, float, float]]] = None,
    days: int = 45,
    random_configs: int = 0,
    seed: int = 7,
) -> Dict:
    """Run a strategy sweep and return sorted results (best P&L first)."""
    base = base or SimulationConfig()
    base = base.clone()
    base.seed = seed
    grid = list(weight_grid or default_weight_grid())

    if random_configs > 0:
        rng = random.Random(seed)
        for _ in range(random_configs):
            w = [rng.random() for _ in range(4)]
            total = sum(w) or 1.0
            grid.append(tuple(x / total for x in w))

    results = [_run_one(base, w, days) for w in grid]
    results.sort(key=lambda r: r["total_pnl"], reverse=True)
    best = results[0] if results else None
    return {
        "days": days,
        "seed": seed,
        "results": results,
        "best": best,
    }


if __name__ == "__main__":  # pragma: no cover - manual utility
    import json
    out = run_sweep(days=45)
    print(json.dumps(out, indent=2))
