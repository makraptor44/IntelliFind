"""Tests for peer-to-peer intra-retailer trading (§16-19)."""

from __future__ import annotations

import unittest

from engine.config import SimulationConfig
from engine.simulation import Simulation


class TestIntraRetail(unittest.TestCase):
    def _make(self):
        return Simulation(SimulationConfig(seed=11))

    def test_complementary_swap_executes(self):
        sim = self._make()
        a = sim.shops["shop_1"]
        b = sim.shops["shop_2"]

        # Shop A: needs Product A (near-empty, high sales), long Product B.
        a.products["product_A"].inventory = 1.0
        a.products["product_A"].sales_rate = 48.0
        a.products["product_A"].threshold = 200.0
        a.products["product_B"].inventory = 900.0
        a.products["product_B"].threshold = 100.0

        # Shop B: mirror — long Product A, needs Product B.
        b.products["product_A"].inventory = 900.0
        b.products["product_A"].threshold = 100.0
        b.products["product_B"].inventory = 1.0
        b.products["product_B"].sales_rate = 48.0
        b.products["product_B"].threshold = 200.0

        trades = sim.intra_engine.find_and_execute(
            sim.shops, sim.products, sim.book, 0, sim.expected_lead_days
        )
        self.assertTrue(len(trades) >= 1)
        t = trades[0]
        # both shops should have improved their short position
        self.assertGreater(a.products["product_A"].inventory, 1.0)
        self.assertGreater(b.products["product_B"].inventory, 1.0)
        self.assertGreater(t.exchange_ratio, 0)

    def test_value_balanced(self):
        sim = self._make()
        a = sim.shops["shop_1"]
        b = sim.shops["shop_2"]
        pa = sim.products["product_A"].last_market_price
        pb = sim.products["product_B"].last_market_price

        a.products["product_A"].inventory = 1.0
        a.products["product_A"].sales_rate = 48.0
        a.products["product_A"].threshold = 200.0
        a.products["product_B"].inventory = 1500.0
        a.products["product_B"].threshold = 100.0
        b.products["product_A"].inventory = 1500.0
        b.products["product_A"].threshold = 100.0
        b.products["product_B"].inventory = 1.0
        b.products["product_B"].sales_rate = 48.0
        b.products["product_B"].threshold = 200.0

        trades = sim.intra_engine.find_and_execute(
            sim.shops, sim.products, sim.book, 0, sim.expected_lead_days
        )
        self.assertTrue(trades)
        t = trades[0]
        # value of product A given ~= value of product B given (within tolerance)
        value_p = t.qty_given * pa
        value_q = t.qty_received * pb
        self.assertLess(abs(value_p - value_q) / max(value_p, value_q), 0.25)

    def test_no_swap_without_complementarity(self):
        sim = self._make()
        # everyone healthy and balanced: no needs -> no swaps
        for shop in sim.shops.values():
            for st in shop.products.values():
                st.inventory = st.threshold * 1.05
        trades = sim.intra_engine.find_and_execute(
            sim.shops, sim.products, sim.book, 0, sim.expected_lead_days
        )
        self.assertEqual(trades, [])


if __name__ == "__main__":
    unittest.main()
