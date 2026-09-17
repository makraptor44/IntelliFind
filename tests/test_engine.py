"""Unit tests for the IntelliFind simulation engine (stdlib unittest).

Run with:  python -m unittest discover -s tests   (or: python -m pytest tests)
"""

from __future__ import annotations

import unittest

from engine.config import SimulationConfig
from engine.rng import RandomStreams
from engine.simulation import Simulation
from engine.procurement_engine import ProcurementEngine, _scale
from engine.events import EventLog


class TestRNG(unittest.TestCase):
    def test_reproducible(self):
        a = RandomStreams(123)
        b = RandomStreams(123)
        seq_a = [a.random("demand") for _ in range(50)]
        seq_b = [b.random("demand") for _ in range(50)]
        self.assertEqual(seq_a, seq_b)

    def test_streams_independent(self):
        r = RandomStreams(7)
        s1 = [r.random("alpha") for _ in range(20)]
        s2 = [r.random("beta") for _ in range(20)]
        self.assertNotEqual(s1, s2)

    def test_seed_changes_sequence(self):
        a = [RandomStreams(1).random("x") for _ in range(10)]
        b = [RandomStreams(2).random("x") for _ in range(10)]
        self.assertNotEqual(a, b)


class TestConfig(unittest.TestCase):
    def test_weights_validate(self):
        cfg = SimulationConfig()
        cfg.validate()  # default weights sum to 1
        cfg.w_price = 0.5
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_normalize_weights(self):
        cfg = SimulationConfig()
        cfg.w_price = cfg.w_risk = cfg.w_delivery = cfg.w_quantity = 2.0
        cfg.normalize_weights()
        self.assertAlmostEqual(
            cfg.w_price + cfg.w_risk + cfg.w_delivery + cfg.w_quantity, 1.0
        )

    def test_scalar_overrides(self):
        cfg = SimulationConfig.from_overrides({"seed": 99, "num_days": 12, "coverage_days": 4.0})
        self.assertEqual(cfg.seed, 99)
        self.assertEqual(cfg.num_days, 12)
        self.assertEqual(cfg.coverage_days, 4.0)

    def test_nested_overrides(self):
        cfg = SimulationConfig.from_overrides({
            "supplier_overrides": {"supplier_B": {"delivery_days": 9}},
            "product_overrides": {"product_A": {"retail_price": 55}},
        })
        b = next(s for s in cfg.suppliers if s.supplier_id == "supplier_B")
        a = next(p for p in cfg.products if p.product_id == "product_A")
        self.assertEqual(b.delivery_days, 9.0)
        self.assertEqual(a.retail_price, 55.0)


class TestScale(unittest.TestCase):
    def test_scale_clamps(self):
        self.assertEqual(_scale(0.4, 0.5, 1.5), 0.0)   # below range -> 0
        self.assertEqual(_scale(2.0, 0.5, 1.5), 1.0)   # above range -> 1
        self.assertAlmostEqual(_scale(1.0, 0.5, 1.5), 0.5)


class TestProcurementScoring(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation(SimulationConfig(seed=5))
        self.eng = self.sim.procurement_engine

    def test_offers_sorted_lower_is_better(self):
        offers = self.eng.evaluate_offers(
            "product_A", 100, self.sim.suppliers, self.sim.products, available_only=False
        )
        scores = [o["score"] for o in offers]
        self.assertEqual(scores, sorted(scores))
        self.assertTrue(offers[0]["selected"])

    def test_components_in_unit_interval(self):
        offers = self.eng.evaluate_offers(
            "product_A", 100, self.sim.suppliers, self.sim.products, available_only=False
        )
        for o in offers:
            for k in ("price_score", "risk_score", "delivery_score", "quantity_score", "score"):
                self.assertGreaterEqual(o[k], 0.0)
                self.assertLessEqual(o[k], 1.0)

    def test_risk_penalises_score(self):
        # crank a supplier's realised default rate; its risk component must worsen
        sup = self.sim.suppliers["supplier_C"]
        before = self.eng.evaluate_offers("product_A", 100, self.sim.suppliers, self.sim.products, False)
        c_before = next(o for o in before if o["supplier_id"] == "supplier_C")["risk_score"]
        sup.historical_default_rate = 0.5
        after = self.eng.evaluate_offers("product_A", 100, self.sim.suppliers, self.sim.products, False)
        c_after = next(o for o in after if o["supplier_id"] == "supplier_C")["risk_score"]
        self.assertGreater(c_after, c_before)

    def test_pack_rounding(self):
        sup = self.sim.suppliers["supplier_B"]  # pack 25
        self.assertEqual(self.eng.pack_quantity(sup, 73), 75)
        self.assertEqual(self.eng.pack_quantity(sup, 75), 75)
        self.assertEqual(self.eng.pack_quantity(sup, 76), 100)

    def test_scatter_respects_60_40(self):
        # construct two near-identical offers -> allocation legs must be >= 40%
        offers = [
            {"supplier_id": "s1", "score": 0.30, "quantity_mismatch": 0},
            {"supplier_id": "s2", "score": 0.31, "quantity_mismatch": 0},
        ]
        alloc = self.eng._split_allocation(100.0, offers)
        if len(alloc) == 2:
            fracs = sorted(a["quantity"] / 100.0 for a in alloc)
            self.assertGreaterEqual(fracs[0], self.sim.config.min_split_ratio - 1e-9)

    def test_no_scatter_when_scores_far_apart(self):
        offers = [
            {"supplier_id": "s1", "score": 0.20, "quantity_mismatch": 0},
            {"supplier_id": "s2", "score": 0.60, "quantity_mismatch": 0},
        ]
        alloc = self.eng._split_allocation(100.0, offers)
        self.assertEqual(len(alloc), 1)


class TestSupplierRisk(unittest.TestCase):
    def test_default_updates_history(self):
        sim = Simulation(SimulationConfig(seed=3))
        sup = sim.suppliers["supplier_A"]
        before = sup.historical_default_rate
        sim.supplier_engine.register_order_outcome(sup, True, 0)
        self.assertGreater(sup.historical_default_rate, before)
        self.assertEqual(sup.defaults, 1)

    def test_success_lowers_history(self):
        sim = Simulation(SimulationConfig(seed=3))
        sup = sim.suppliers["supplier_B"]
        sup.historical_default_rate = 0.3
        for _ in range(50):
            sim.supplier_engine.register_order_outcome(sup, False, 0)
        self.assertLess(sup.historical_default_rate, 0.3)
        self.assertEqual(sup.successful_orders, 50)

    def test_prices_move(self):
        sim = Simulation(SimulationConfig(seed=3))
        p0 = sim.suppliers["supplier_A"].products["product_A"].current_price
        sim.run(48)
        p1 = sim.suppliers["supplier_A"].products["product_A"].current_price
        self.assertNotEqual(round(p0, 4), round(p1, 4))


class TestFinancial(unittest.TestCase):
    def test_stockout_hits_satisfaction(self):
        sim = Simulation(SimulationConfig(seed=3))
        shop = sim.shops["shop_1"]
        product = sim.products["product_A"]
        before = shop.satisfaction
        shop.products["product_A"].sales_rate = 24.0
        sim.financial_engine.book_stockout(shop, product, 20.0, 0)
        self.assertLess(shop.satisfaction, before)
        self.assertGreater(shop.stockout_cost, 0.0)

    def test_sale_books_revenue_and_cash(self):
        sim = Simulation(SimulationConfig(seed=3))
        shop = sim.shops["shop_1"]
        cash0, rev0 = shop.cash, shop.revenue
        got = sim.financial_engine.book_sale(shop, sim.products["product_A"], 10.0)
        self.assertAlmostEqual(got, 10.0 * sim.products["product_A"].retail_price)
        self.assertAlmostEqual(shop.cash - cash0, got)
        self.assertAlmostEqual(shop.revenue - rev0, got)


class TestSimulation(unittest.TestCase):
    def test_smoke_run(self):
        sim = Simulation(SimulationConfig(seed=1))
        sim.run(24 * 10)
        snap = sim.snapshot()
        for key in ("overview", "shops", "suppliers", "order_book", "history", "products"):
            self.assertIn(key, snap)
        self.assertEqual(len(snap["shops"]), 5)
        self.assertEqual(len(snap["suppliers"]), 3)
        self.assertEqual(len(snap["products"]), 5)

    def test_deterministic(self):
        a = Simulation(SimulationConfig(seed=42)); a.run(24 * 15)
        b = Simulation(SimulationConfig(seed=42)); b.run(24 * 15)
        self.assertAlmostEqual(
            a.snapshot(False)["overview"]["total_pnl"],
            b.snapshot(False)["overview"]["total_pnl"],
        )

    def test_seed_changes_outcome(self):
        a = Simulation(SimulationConfig(seed=1)); a.run(24 * 15)
        b = Simulation(SimulationConfig(seed=2)); b.run(24 * 15)
        self.assertNotEqual(
            round(a.snapshot(False)["overview"]["total_pnl"], 2),
            round(b.snapshot(False)["overview"]["total_pnl"], 2),
        )

    def test_reset_reproduces(self):
        sim = Simulation(SimulationConfig(seed=7)); sim.run(24 * 12)
        pnl1 = sim.snapshot(False)["overview"]["total_pnl"]
        sim.reset(SimulationConfig(seed=7)); sim.run(24 * 12)
        pnl2 = sim.snapshot(False)["overview"]["total_pnl"]
        self.assertAlmostEqual(pnl1, pnl2)

    def test_is_finished(self):
        cfg = SimulationConfig(seed=1, num_days=2)
        sim = Simulation(cfg)
        self.assertFalse(sim.is_finished())
        sim.run(cfg.num_days * cfg.ticks_per_day)
        self.assertTrue(sim.is_finished())

    def test_open_ended_never_finishes(self):
        sim = Simulation(SimulationConfig(seed=1, num_days=0))
        sim.run(24 * 5)
        self.assertFalse(sim.is_finished())

    def test_activity_occurs(self):
        # a moderate run should exercise the major mechanics
        sim = Simulation(SimulationConfig(seed=42)); sim.run(24 * 40)
        ov = sim.snapshot(False)["overview"]
        self.assertGreater(ov["num_market_orders"], 0)
        self.assertGreaterEqual(ov["num_defaults"], 0)
        self.assertGreater(ov["total_revenue"], 0)

    def test_correlation_is_numeric_or_none(self):
        sim = Simulation(SimulationConfig(seed=42)); sim.run(24 * 40)
        corr = sim.score_pnl_correlation()
        self.assertTrue(corr is None or (-1.0 <= corr <= 1.0))


class TestEventLog(unittest.TestCase):
    def test_emit_and_counts(self):
        log = EventLog()
        log.emit(0, "market_order", shop_id="shop_1")
        log.emit(1, "stockout", shop_id="shop_1")
        log.emit(2, "market_order", shop_id="shop_2")
        self.assertEqual(log.counts["market_order"], 2)
        self.assertEqual(len(log.tail_types(10, {"market_order"})), 2)
        self.assertEqual(len(log.export()), 3)


if __name__ == "__main__":
    unittest.main()
