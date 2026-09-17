"""The top-level simulation orchestrator.

Wires the demand, inventory, supplier, procurement, intra-retail and financial
engines together and advances the world one tick at a time (§6).  It also:

  * maintains the entity state (shops, suppliers, market products),
  * records a downsampleable time-series history for the dashboard charts,
  * tracks the empirical relationship between an order's procurement score and
    the subsequent P&L outcome (§23),
  * produces a single JSON-serialisable :meth:`snapshot` for the dashboard.

The module is intentionally free of any I/O so it can be driven by the web
server, a test, or (later) an AI optimisation loop.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Deque, Dict, List, Optional

from .config import SimulationConfig
from .demand_engine import DemandEngine
from .entities import (
    MarketProduct, RepairShop, ShopProductState, Supplier, SupplierProductState,
)
from .events import EventLog
from .financial_engine import FinancialEngine
from .intra_retail_engine import IntraRetailEngine
from .inventory_engine import InventoryEngine
from .order_book import OrderBook
from .procurement_engine import ProcurementEngine
from .supplier_engine import SupplierEngine
from .rng import RandomStreams


HISTORY_MAX = 1600           # internal ring-buffer length
SNAPSHOT_HISTORY_POINTS = 220  # downsampled points sent to the dashboard


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


class Simulation:
    def __init__(self, config: Optional[SimulationConfig] = None):
        self.config = config or SimulationConfig()
        self.config.validate()
        self.tick: int = 0
        self.log = EventLog()
        self._build()

    # ==================================================================
    # construction / reset
    # ==================================================================
    def _build(self) -> None:
        cfg = self.config
        self.rng = RandomStreams(cfg.seed)

        # engines
        self.demand_engine = DemandEngine(cfg, self.rng)
        self.supplier_engine = SupplierEngine(cfg, self.rng, self.log)
        self.inventory_engine = InventoryEngine(cfg)
        self.procurement_engine = ProcurementEngine(cfg, self.rng, self.log)
        self.intra_engine = IntraRetailEngine(cfg, self.inventory_engine, self.log)
        self.financial_engine = FinancialEngine(cfg, self.log)
        self.book = OrderBook()

        # market products
        self.products: Dict[str, MarketProduct] = {}
        for p in cfg.products:
            self.products[p.product_id] = MarketProduct(
                product_id=p.product_id, name=p.name,
                retail_price=p.retail_price, base_cost=p.base_cost,
                baseline_demand=p.baseline_demand,
                market_index=1.0, last_market_price=p.base_cost,
            )

        self.expected_lead_days = sum(s.delivery_days for s in cfg.suppliers) / len(cfg.suppliers)

        # shops
        self.shops: Dict[str, RepairShop] = {}
        for i in range(cfg.num_shops):
            sid = f"shop_{i + 1}"
            shop = RepairShop(
                shop_id=sid, name=f"Repair Shop {i + 1}",
                cash=cfg.initial_cash, satisfaction=cfg.initial_satisfaction,
            )
            for p in cfg.products:
                mult = 1.0 + self.rng.gauss(f"shopmult:{sid}:{p.product_id}", 0.0, cfg.shop_demand_variation)
                mult = max(0.5, mult)
                base_daily = p.baseline_demand * mult
                # initial inventory expressed in days of cover, with jitter
                jitter = 1.0 + self.rng.uniform(
                    f"initinv:{sid}:{p.product_id}",
                    -cfg.initial_inventory_jitter, cfg.initial_inventory_jitter,
                )
                init_inv = base_daily * cfg.initial_inventory_days * max(0.2, jitter)
                st = ShopProductState(
                    inventory=round(init_inv, 1),
                    sales_rate=base_daily,
                    base_daily_demand=base_daily,
                    demand_multiplier=mult,
                )
                st.threshold = self.inventory_engine.compute_threshold(base_daily, self.expected_lead_days)
                shop.products[p.product_id] = st
            self.shops[sid] = shop

        # suppliers
        self.suppliers: Dict[str, Supplier] = {}
        for sc in cfg.suppliers:
            sup = Supplier(
                supplier_id=sc.supplier_id, name=sc.name,
                delivery_days=sc.delivery_days, pack_size=sc.pack_size,
                base_default_probability=sc.base_default_probability,
                default_probability=sc.base_default_probability,
                historical_default_rate=sc.base_default_probability,
                risk_score=sc.base_default_probability,
            )
            for p in cfg.products:
                base_price = p.base_cost * sc.price_premium
                inv_jit = 1.0 + self.rng.uniform(f"supinv:{sc.supplier_id}:{p.product_id}", -0.2, 0.2)
                init_inv = sc.initial_inventory * max(0.3, inv_jit)
                sup.products[p.product_id] = SupplierProductState(
                    inventory=round(init_inv, 0),
                    initial_inventory=round(init_inv, 0),
                    base_price=base_price,
                    current_price=base_price,
                )
            self.suppliers[sc.supplier_id] = sup

        # prime prices/risk once so the first snapshot is populated
        self.supplier_engine.update_market(self.products)
        self.supplier_engine.update_prices(self.suppliers, self.products, 0)
        self.supplier_engine.update_risk(self.suppliers, 0)

        # global accumulators
        self.total_sales_units = 0.0
        self.total_revenue = 0.0
        self.total_procurement = 0.0

        # score -> P&L outcome tracking
        self._pending_outcomes: Deque[Dict] = deque()
        self._outcome_samples: Deque[Dict] = deque(maxlen=1200)

        # time-series history (parallel lists, trimmed to HISTORY_MAX)
        self.history = self._empty_history()

        self.log.emit(0, "simulation_reset", seed=cfg.seed)

    def _empty_history(self) -> Dict:
        return {
            "t": [],
            "pnl_total": [],
            "pnl_by_shop": {sid: [] for sid in self._shop_ids()},
            "satisfaction": [],
            "inv_by_product": {pid: [] for pid in self._product_ids()},
            "demand_actual": {pid: [] for pid in self._product_ids()},
            "demand_baseline": {pid: [] for pid in self._product_ids()},
            "price": {s.supplier_id: {p.product_id: [] for p in self.config.products}
                      for s in self.config.suppliers},
            "risk": {s.supplier_id: [] for s in self.config.suppliers},
            "score": {s.supplier_id: [] for s in self.config.suppliers},
        }

    def _shop_ids(self) -> List[str]:
        return [f"shop_{i + 1}" for i in range(self.config.num_shops)]

    def _product_ids(self) -> List[str]:
        return [p.product_id for p in self.config.products]

    def reset(self, config: Optional[SimulationConfig] = None) -> None:
        if config is not None:
            self.config = config
            self.config.validate()
        self.tick = 0
        self.log.clear()
        self._build()

    # ==================================================================
    # one tick
    # ==================================================================
    def step(self) -> None:
        cfg = self.config
        t = self.tick

        # 6/7: market, prices, risk, restock ------------------------------
        self.supplier_engine.update_market(self.products)
        self.supplier_engine.update_prices(self.suppliers, self.products, t)
        self.supplier_engine.update_risk(self.suppliers, t)
        self.supplier_engine.restock(self.suppliers, t)

        # 1-4: demand, sales, stockouts -----------------------------------
        tick_demand_by_product = {pid: 0.0 for pid in self.products}
        tick_baseline_by_product = {pid: 0.0 for pid in self.products}
        shop_tick_costs: Dict[str, Dict[str, float]] = {}

        for shop in self.shops.values():
            costs = {"revenue": 0.0, "procurement_cost": 0.0,
                     "stockout_cost": 0.0, "delay_cost": 0.0, "holding_cost": 0.0}
            any_unmet = False
            for pid, st in shop.products.items():
                product = self.products[pid]
                d = self.demand_engine.tick_demand(shop, pid)
                demand = d["demand"]
                tick_demand_by_product[pid] += demand
                tick_baseline_by_product[pid] += d["baseline"]

                # update the shop's estimate of its own sales velocity (EWMA)
                daily_equiv = demand * cfg.ticks_per_day
                st.sales_rate += 0.02 * (daily_equiv - st.sales_rate)
                st.sales_rate = max(0.0, st.sales_rate)

                sold, unmet = self.inventory_engine.consume(shop, pid, demand)
                if sold > 0:
                    costs["revenue"] += self.financial_engine.book_sale(shop, product, sold)
                    self.total_sales_units += sold
                if unmet > 1e-9:
                    any_unmet = True
                    res = self.financial_engine.book_stockout(shop, product, unmet, t)
                    costs["stockout_cost"] += res["stockout_cost"]
                    costs["delay_cost"] += res["delay_cost"]

                # refresh threshold from the latest velocity estimate
                st.threshold = self.inventory_engine.compute_threshold(
                    st.sales_rate, self.expected_lead_days
                )

            # satisfaction slowly recovers on a fully-served tick
            self.financial_engine.recover_satisfaction(shop, t, healthy=not any_unmet)
            shop_tick_costs[shop.shop_id] = costs

        # 4: process deliveries -------------------------------------------
        for order in self.book.pending_deliveries(t):
            shop = self.shops[order.shop_id]
            st = shop.products[order.product_id]
            st.inventory += order.quantity
            st.incoming = max(0.0, st.incoming - order.quantity)
            order.status = "delivered"
            if order.order_id in shop.open_market_orders:
                shop.open_market_orders.remove(order.order_id)
            supplier = self.suppliers[order.supplier_id]
            if order.order_id in supplier.outstanding_orders:
                supplier.outstanding_orders.remove(order.order_id)
            self.log.emit(
                t, "supplier_delivery",
                order_id=order.order_id, shop_id=order.shop_id,
                supplier_id=order.supplier_id, product_id=order.product_id,
                quantity=order.quantity,
            )

        # 8: procurement decisions (market orders) ------------------------
        procurement_before = {sid: shop.procurement_cost for sid, shop in self.shops.items()}
        for shop in self.shops.values():
            for pid, st in shop.products.items():
                available = st.inventory + st.incoming
                if available < st.threshold:
                    target = self.inventory_engine.reorder_target(st.sales_rate, self.expected_lead_days)
                    required = target - available
                    if required >= 1.0:
                        self.procurement_engine.place_market_order(
                            shop, pid, required, self.suppliers, self.products,
                            self.book, t, self.supplier_engine, reason="threshold",
                        )
                # occasionally place a forward-looking limit order
                if self.rng.chance(f"autolimit:{shop.shop_id}:{pid}:{t}", cfg.auto_limit_order_frequency):
                    qty = self.procurement_engine.pack_quantity(
                        list(self.suppliers.values())[0], st.sales_rate * cfg.coverage_days
                    )
                    if qty >= 1:
                        self.procurement_engine.create_limit_order(
                            shop, pid, qty, cfg.limit_order_score, self.book, t
                        )

        # 8: limit orders --------------------------------------------------
        self.procurement_engine.evaluate_limit_orders(
            self.shops, self.suppliers, self.products, self.book, t, self.supplier_engine
        )

        # 9: intra-retail swaps -------------------------------------------
        self.intra_engine.find_and_execute(
            self.shops, self.products, self.book, t, self.expected_lead_days
        )

        # 10: holding cost + P&L ------------------------------------------
        for shop in self.shops.values():
            costs = shop_tick_costs[shop.shop_id]
            costs["holding_cost"] = self.financial_engine.apply_holding_cost(shop, self.products)
            # procurement cost booked this tick (orders placed above)
            costs["procurement_cost"] = shop.procurement_cost - procurement_before[shop.shop_id]
            self.financial_engine.update_pnl(shop, t, costs)

        self.total_revenue = sum(s.revenue for s in self.shops.values())
        self.total_procurement = sum(s.procurement_cost for s in self.shops.values())

        # score -> P&L outcome maturation ---------------------------------
        self._register_new_outcomes(t)
        self._mature_outcomes(t)

        # history ----------------------------------------------------------
        self._record_history(t, tick_demand_by_product, tick_baseline_by_product)

        self.tick += 1

    # ------------------------------------------------------------------
    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    def is_finished(self) -> bool:
        if self.config.num_days <= 0:
            return False
        return self.tick >= self.config.num_days * self.config.ticks_per_day

    # ==================================================================
    # score -> P&L tracking (§23)
    # ==================================================================
    def _register_new_outcomes(self, t: int) -> None:
        """Snapshot every market order placed this tick with the shop's P&L now."""
        for order in self.book.market_orders.values():
            if order.order_time == t and order.status != "defaulted" and not order.is_split_leg:
                shop = self.shops[order.shop_id]
                self._pending_outcomes.append({
                    "order_time": t,
                    "shop_id": order.shop_id,
                    "score": order.score,
                    "pnl_at_order": shop.pnl,
                })

    def _mature_outcomes(self, t: int) -> None:
        horizon = int(self.config.outcome_horizon_days * self.config.ticks_per_day)
        while self._pending_outcomes and (t - self._pending_outcomes[0]["order_time"]) >= horizon:
            item = self._pending_outcomes.popleft()
            shop = self.shops[item["shop_id"]]
            pnl_delta = shop.pnl - item["pnl_at_order"]
            self._outcome_samples.append({"score": item["score"], "pnl_delta": pnl_delta})

    def score_pnl_correlation(self) -> Optional[float]:
        if len(self._outcome_samples) < 3:
            return None
        xs = [s["score"] for s in self._outcome_samples]
        ys = [s["pnl_delta"] for s in self._outcome_samples]
        return _pearson(xs, ys)

    # ==================================================================
    # history
    # ==================================================================
    def _nominal_score_snapshot(self) -> Dict[str, float]:
        """Average score per supplier across products for a nominal order size."""
        result = {sid: 0.0 for sid in self.suppliers}
        counts = {sid: 0 for sid in self.suppliers}
        for pid in self.products:
            offers = self.procurement_engine.evaluate_offers(
                pid, 50.0, self.suppliers, self.products, available_only=False
            )
            for o in offers:
                result[o["supplier_id"]] += o["score"]
                counts[o["supplier_id"]] += 1
        for sid in result:
            if counts[sid]:
                result[sid] /= counts[sid]
        return result

    def _record_history(self, t: int, demand_actual: Dict, demand_baseline: Dict) -> None:
        h = self.history
        day = t / self.config.ticks_per_day
        h["t"].append(round(day, 3))
        h["pnl_total"].append(round(sum(s.pnl for s in self.shops.values()), 2))
        for sid, shop in self.shops.items():
            h["pnl_by_shop"][sid].append(round(shop.pnl, 2))
        h["satisfaction"].append(
            round(sum(s.satisfaction for s in self.shops.values()) / len(self.shops), 3)
        )
        for pid in self.products:
            inv = sum(shop.products[pid].inventory for shop in self.shops.values())
            h["inv_by_product"][pid].append(round(inv, 1))
            h["demand_actual"][pid].append(round(demand_actual[pid], 3))
            h["demand_baseline"][pid].append(round(demand_baseline[pid], 3))
        for sid, sup in self.suppliers.items():
            for pid, ps in sup.products.items():
                h["price"][sid][pid].append(round(ps.current_price, 3))
            h["risk"][sid].append(round(sup.historical_default_rate, 4))
        scores = self._nominal_score_snapshot()
        for sid in self.suppliers:
            h["score"][sid].append(round(scores[sid], 4))

        # trim
        if len(h["t"]) > HISTORY_MAX:
            self._trim_history(h)

    def _trim_history(self, h: Dict) -> None:
        cut = len(h["t"]) - HISTORY_MAX
        def trim_list(lst):
            del lst[:cut]
        trim_list(h["t"])
        trim_list(h["pnl_total"])
        trim_list(h["satisfaction"])
        for lst in h["pnl_by_shop"].values():
            trim_list(lst)
        for group in ("inv_by_product", "demand_actual", "demand_baseline"):
            for lst in h[group].values():
                trim_list(lst)
        for sid in h["price"]:
            for lst in h["price"][sid].values():
                trim_list(lst)
        for lst in h["risk"].values():
            trim_list(lst)
        for lst in h["score"].values():
            trim_list(lst)

    def _downsample_history(self, points: int = SNAPSHOT_HISTORY_POINTS) -> Dict:
        h = self.history
        n = len(h["t"])
        if n <= points:
            idx = list(range(n))
        else:
            stride = n / points
            idx = sorted(set(int(i * stride) for i in range(points)))
            if idx and idx[-1] != n - 1:
                idx.append(n - 1)

        def pick(lst):
            return [lst[i] for i in idx]

        return {
            "t": pick(h["t"]),
            "pnl_total": pick(h["pnl_total"]),
            "pnl_by_shop": {k: pick(v) for k, v in h["pnl_by_shop"].items()},
            "satisfaction": pick(h["satisfaction"]),
            "inv_by_product": {k: pick(v) for k, v in h["inv_by_product"].items()},
            "demand_actual": {k: pick(v) for k, v in h["demand_actual"].items()},
            "demand_baseline": {k: pick(v) for k, v in h["demand_baseline"].items()},
            "price": {sid: {pid: pick(v) for pid, v in prod.items()}
                      for sid, prod in h["price"].items()},
            "risk": {k: pick(v) for k, v in h["risk"].items()},
            "score": {k: pick(v) for k, v in h["score"].items()},
        }

    # ==================================================================
    # snapshot for the dashboard
    # ==================================================================
    def snapshot(self, include_history: bool = True) -> Dict:
        cfg = self.config
        t = self.tick

        # --- shops ----------------------------------------------------
        shops_out = []
        for shop in self.shops.values():
            rows = []
            for pid, st in shop.products.items():
                doi = self.inventory_engine.days_of_inventory(st.inventory, st.sales_rate)
                status = self.inventory_engine.status(
                    st.inventory, st.incoming, st.threshold, st.sales_rate, self.expected_lead_days
                )
                rows.append({
                    "product_id": pid,
                    "product_name": self.products[pid].name,
                    "inventory": round(st.inventory, 1),
                    "incoming": round(st.incoming, 1),
                    "sales_rate": round(st.sales_rate, 2),
                    "threshold": round(st.threshold, 1),
                    "days_of_inventory": None if math.isinf(doi) else round(doi, 2),
                    "status": status,
                })
            shops_out.append({
                "shop_id": shop.shop_id, "name": shop.name,
                "cash": round(shop.cash, 2),
                "satisfaction": round(shop.satisfaction, 2),
                "revenue": round(shop.revenue, 2),
                "procurement_cost": round(shop.procurement_cost, 2),
                "stockout_cost": round(shop.stockout_cost, 2),
                "delay_cost": round(shop.delay_cost, 2),
                "holding_cost": round(shop.holding_cost, 2),
                "pnl": round(shop.pnl, 2),
                "instant_pnl": round(shop.instant_pnl, 2),
                "open_market_orders": len(shop.open_market_orders),
                "open_limit_orders": len(shop.open_limit_orders),
                "products": rows,
            })

        # --- suppliers ------------------------------------------------
        suppliers_out = []
        for sup in self.suppliers.values():
            suppliers_out.append({
                "supplier_id": sup.supplier_id, "name": sup.name,
                "delivery_days": sup.delivery_days,
                "pack_size": sup.pack_size,
                "default_probability": round(sup.default_probability, 4),
                "historical_default_rate": round(sup.historical_default_rate, 4),
                "risk_score": round(sup.risk_score, 4),
                "successful_orders": sup.successful_orders,
                "defaults": sup.defaults,
                "outstanding_orders": len(sup.outstanding_orders),
                "products": [{
                    "product_id": pid,
                    "product_name": self.products[pid].name,
                    "inventory": round(ps.inventory, 0),
                    "price": round(ps.current_price, 2),
                } for pid, ps in sup.products.items()],
            })

        # --- global overview -----------------------------------------
        avg_sat = sum(s.satisfaction for s in self.shops.values()) / len(self.shops)
        total_pnl = sum(s.pnl for s in self.shops.values())
        overview = {
            "tick": t,
            "day": round(t / cfg.ticks_per_day, 2),
            "ticks_per_day": cfg.ticks_per_day,
            "num_days": cfg.num_days,
            "total_sales_units": round(self.total_sales_units, 1),
            "total_revenue": round(self.total_revenue, 2),
            "total_procurement": round(self.total_procurement, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_satisfaction": round(avg_sat, 2),
            "num_stockouts": self.log.counts.get("stockout", 0),
            "num_defaults": self.log.counts.get("supplier_default", 0),
            "num_market_orders": self.log.counts.get("market_order", 0),
            "num_limit_orders_created": self.log.counts.get("limit_order_created", 0),
            "num_limit_orders_executed": self.log.counts.get("limit_order_executed", 0),
            "num_intra_trades": self.log.counts.get("intra_retail_trade", 0),
            "score_pnl_correlation": self.score_pnl_correlation(),
            "outcome_samples": len(self._outcome_samples),
        }

        snap = {
            "overview": overview,
            "products": [{
                "product_id": p.product_id, "name": p.name,
                "retail_price": p.retail_price, "base_cost": p.base_cost,
                "market_index": round(p.market_index, 4),
                "market_price": round(p.last_market_price, 3),
            } for p in self.products.values()],
            "shops": shops_out,
            "suppliers": suppliers_out,
            "order_book": self.book.snapshot(),
            "decisions": list(self.procurement_engine.recent_decisions)[-8:][::-1],
            "events": self.log.tail_types(40, (
                "market_order", "supplier_default", "supplier_delivery", "stockout",
                "limit_order_created", "limit_order_executed", "intra_retail_trade",
            )),
            "config": self.config.to_dict(),
            "scatter_samples": [
                {"score": round(s["score"], 4), "pnl": round(s["pnl_delta"], 2)}
                for s in list(self._outcome_samples)[-400:]
            ],
        }
        if include_history:
            snap["history"] = self._downsample_history()
        return snap
