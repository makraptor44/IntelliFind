"""The procurement scoring engine and order routing.

For every candidate supplier offer the engine computes a **cost score** from
four weighted, normalised components (§10-11)::

    Score = W_price   * PriceScore
          + W_risk    * RiskScore
          + W_delivery * DeliveryScore
          + W_quantity * QuantityMismatchScore      (weights sum to 1)

**Lower score == better offer.** The components are min-max normalised across
the current candidate set so that they are comparable and combine meaningfully.

The engine then routes orders:
  * market orders  - buy now from the best offer (optionally split across two
    suppliers, never below a 60:40 allocation, per §15);
  * limit orders   - execute only once the best available score is <= the
    shop's maximum acceptable score (§14).
"""

from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional

from .config import SimulationConfig
from .entities import MarketProduct, RepairShop, Supplier
from .events import EventLog
from .order_book import LimitOrder, MarketOrder, OrderBook
from .rng import RandomStreams


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _scale(value: float, lo: float, hi: float) -> float:
    """Map ``value`` from the range [lo, hi] onto [0, 1] (clamped)."""
    if hi - lo <= 1e-12:
        return 0.0
    return _clamp01((value - lo) / (hi - lo))


class ProcurementEngine:
    def __init__(self, config: SimulationConfig, rng: RandomStreams, log: EventLog):
        self.config = config
        self.rng = rng
        self.log = log
        # last decision per (shop, product) for the explainability view
        self.last_decisions: Dict[str, Dict] = {}
        # rolling feed of the most recent decisions (for the dashboard panel)
        self.recent_decisions: deque = deque(maxlen=30)

    # ------------------------------------------------------------------
    def pack_quantity(self, supplier: Supplier, required: float) -> float:
        """Round the required quantity up to the supplier's bulk pack increment."""
        pack = max(1, int(supplier.pack_size))
        return math.ceil(max(0.0, required) / pack) * pack

    # ------------------------------------------------------------------
    def evaluate_offers(
        self,
        product_id: str,
        required: float,
        suppliers: Dict[str, Supplier],
        products: Dict[str, MarketProduct],
        available_only: bool = True,
    ) -> List[Dict]:
        """Score every supplier for a product/quantity. Returns offers sorted
        best-first (lowest score first). Each offer is a fully populated dict for
        the dashboard's decision view.
        """
        cfg = self.config
        candidates: List[Supplier] = []
        for s in suppliers.values():
            if available_only and s.products[product_id].inventory <= 0:
                continue
            candidates.append(s)
        if not candidates:
            candidates = list(suppliers.values())

        base_cost = max(1e-6, products[product_id].base_cost)
        prices = [s.products[product_id].current_price for s in candidates]
        risks = [s.historical_default_rate for s in candidates]
        deliveries = [s.delivery_days for s in candidates]

        order_qtys = [self.pack_quantity(s, required) for s in candidates]
        mismatches = []
        for oq in order_qtys:
            excess = max(0.0, oq - required)
            mismatches.append(excess / required if required > 1e-9 else 0.0)

        # Absolute normalisation against stable reference scales, so a score is
        # comparable across suppliers, over time and between strategies.
        n_price = [_scale(p / base_cost, cfg.price_score_lo, cfg.price_score_hi) for p in prices]
        n_risk = [_scale(r, 0.0, cfg.risk_norm) for r in risks]
        n_delivery = [_scale(d, 0.0, cfg.delivery_norm) for d in deliveries]
        n_quantity = [_scale(m, 0.0, cfg.quantity_norm) for m in mismatches]

        offers: List[Dict] = []
        for i, s in enumerate(candidates):
            score = (
                cfg.w_price * n_price[i]
                + cfg.w_risk * n_risk[i]
                + cfg.w_delivery * n_delivery[i]
                + cfg.w_quantity * n_quantity[i]
            )
            offers.append({
                "supplier_id": s.supplier_id,
                "supplier_name": s.name,
                "product_id": product_id,
                "price": round(prices[i], 4),
                "risk": round(risks[i], 4),
                "default_probability": round(s.default_probability, 4),
                "delivery_days": s.delivery_days,
                "pack_size": s.pack_size,
                "order_quantity": order_qtys[i],
                "quantity_mismatch": round(mismatches[i], 4),
                "available_inventory": s.products[product_id].inventory,
                # normalised component scores
                "price_score": round(n_price[i], 4),
                "risk_score": round(n_risk[i], 4),
                "delivery_score": round(n_delivery[i], 4),
                "quantity_score": round(n_quantity[i], 4),
                "score": round(score, 4),
                "weights": {
                    "price": cfg.w_price, "risk": cfg.w_risk,
                    "delivery": cfg.w_delivery, "quantity": cfg.w_quantity,
                },
            })
        offers.sort(key=lambda o: o["score"])
        if offers:
            offers[0]["selected"] = True
        return offers

    # ------------------------------------------------------------------
    def _split_allocation(self, required: float, offers: List[Dict]) -> List[Dict]:
        """Decide how to allocate ``required`` units across the best offers.

        Favours the single best supplier, but will split across the top two when
        their scores are close (§15) — provided the smaller leg is at least
        ``min_split_ratio`` (40%) of the total. Never splits below 60:40.
        """
        cfg = self.config
        best = offers[0]
        if not cfg.enable_scattered_orders or len(offers) < 2:
            return [{"offer": best, "quantity": required}]

        second = offers[1]
        if (second["score"] - best["score"]) > cfg.scatter_score_delta:
            return [{"offer": best, "quantity": required}]

        # attractiveness-weighted split (inverse of score), then clamp to 60:40
        s1 = max(1e-6, 1.0 - best["score"])
        s2 = max(1e-6, 1.0 - second["score"])
        frac_best = s1 / (s1 + s2)
        frac_best = min(1.0 - cfg.min_split_ratio, max(cfg.min_split_ratio, frac_best))
        frac_second = 1.0 - frac_best

        if frac_second < cfg.min_split_ratio - 1e-9:
            return [{"offer": best, "quantity": required}]

        return [
            {"offer": best, "quantity": required * frac_best},
            {"offer": second, "quantity": required * frac_second},
        ]

    # ------------------------------------------------------------------
    def place_market_order(
        self,
        shop: RepairShop,
        product_id: str,
        required: float,
        suppliers: Dict[str, Supplier],
        products: Dict[str, MarketProduct],
        book: OrderBook,
        timestamp: int,
        supplier_engine,
        reason: str = "threshold",
    ) -> List[MarketOrder]:
        """Evaluate suppliers and execute a (possibly split) market order."""
        if required <= 0:
            return []
        offers = self.evaluate_offers(product_id, required, suppliers, products)
        if not offers:
            return []

        # remember the decision for the explainability panel
        decision = {
            "shop_id": shop.shop_id,
            "product_id": product_id,
            "required": round(required, 2),
            "timestamp": timestamp,
            "reason": reason,
            "offers": offers,
            "selected_supplier": offers[0]["supplier_id"],
        }
        self.last_decisions[f"{shop.shop_id}:{product_id}"] = decision
        self.recent_decisions.append(decision)

        allocation = self._split_allocation(required, offers)
        parent_id = book.next_id("mo") if len(allocation) > 1 else None
        created: List[MarketOrder] = []

        for leg in allocation:
            offer = leg["offer"]
            supplier = suppliers[offer["supplier_id"]]
            qty = self.pack_quantity(supplier, leg["quantity"])
            if qty <= 0:
                continue
            unit_price = supplier.products[product_id].current_price
            total_cost = qty * unit_price

            # realise supplier default risk at execution time
            defaulted = self.rng.chance(
                f"default:{supplier.supplier_id}:{timestamp}:{product_id}:{shop.shop_id}",
                supplier.default_probability,
            )
            supplier_engine.register_order_outcome(supplier, defaulted, timestamp)

            order = MarketOrder(
                order_id=book.next_id("mo"),
                shop_id=shop.shop_id,
                product_id=product_id,
                quantity=qty,
                supplier_id=supplier.supplier_id,
                score=offer["score"],
                unit_price=round(unit_price, 4),
                total_cost=round(total_cost, 2),
                order_time=timestamp,
                expected_delivery=timestamp + int(round(supplier.delivery_days * self.config.ticks_per_day)),
                delivery_days=supplier.delivery_days,
                quantity_mismatch=offer["quantity_mismatch"],
                risk_score=offer["risk"],
                is_split_leg=len(allocation) > 1,
                parent_id=parent_id,
            )

            if defaulted:
                order.status = "defaulted"
                book.add_market_order(order)
                self.log.emit(
                    timestamp, "supplier_default",
                    order_id=order.order_id, shop_id=shop.shop_id,
                    supplier_id=supplier.supplier_id, product_id=product_id,
                    quantity=qty, procurement_score=offer["score"],
                )
            else:
                # commit: charge shop, reserve supplier stock, schedule delivery
                shop.cash -= total_cost
                shop.procurement_cost += total_cost
                supplier.products[product_id].inventory = max(
                    0.0, supplier.products[product_id].inventory - qty
                )
                shop.products[product_id].incoming += qty
                supplier.outstanding_orders.append(order.order_id)
                shop.open_market_orders.append(order.order_id)
                book.add_market_order(order)
                self.log.emit(
                    timestamp, "market_order",
                    order_id=order.order_id, shop_id=shop.shop_id,
                    product_id=product_id, supplier_id=supplier.supplier_id,
                    quantity=qty, price=round(unit_price, 4),
                    risk_score=offer["risk"], delivery_days=supplier.delivery_days,
                    quantity_mismatch=offer["quantity_mismatch"],
                    procurement_score=offer["score"], reason=reason,
                    split=len(allocation) > 1,
                )
            created.append(order)
        return created

    # ------------------------------------------------------------------
    def create_limit_order(
        self, shop: RepairShop, product_id: str, quantity: float,
        max_score: float, book: OrderBook, timestamp: int,
    ) -> LimitOrder:
        order = LimitOrder(
            order_id=book.next_id("lo"),
            shop_id=shop.shop_id,
            product_id=product_id,
            quantity=quantity,
            max_score=max_score,
            created_time=timestamp,
        )
        book.add_limit_order(order)
        shop.open_limit_orders.append(order.order_id)
        self.log.emit(
            timestamp, "limit_order_created",
            order_id=order.order_id, shop_id=shop.shop_id,
            product_id=product_id, quantity=quantity, max_score=max_score,
        )
        return order

    # ------------------------------------------------------------------
    def evaluate_limit_orders(
        self,
        shops: Dict[str, RepairShop],
        suppliers: Dict[str, Supplier],
        products: Dict[str, MarketProduct],
        book: OrderBook,
        timestamp: int,
        supplier_engine,
    ) -> None:
        """Each tick: refresh best scores and execute any triggered limit orders."""
        for lo in book.open_limit_orders():
            shop = shops[lo.shop_id]
            offers = self.evaluate_offers(lo.product_id, lo.quantity, suppliers, products)
            if not offers:
                continue
            best = offers[0]
            lo.current_best_score = best["score"]
            if best["score"] <= lo.max_score:
                lo.status = "executed"
                lo.executed_time = timestamp
                lo.executed_supplier = best["supplier_id"]
                if lo.order_id in shop.open_limit_orders:
                    shop.open_limit_orders.remove(lo.order_id)
                self.log.emit(
                    timestamp, "limit_order_executed",
                    order_id=lo.order_id, shop_id=lo.shop_id,
                    product_id=lo.product_id, quantity=lo.quantity,
                    supplier_id=best["supplier_id"],
                    procurement_score=best["score"], max_score=lo.max_score,
                )
                self.place_market_order(
                    shop, lo.product_id, lo.quantity, suppliers, products,
                    book, timestamp, supplier_engine, reason="limit_order",
                )
