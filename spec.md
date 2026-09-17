# IntelliFind — Design & Implementation Notes

This document records how the procurement-automation simulation is built and where each
requirement of the product specification is implemented. It is the map between the spec and the
code. (The narrative product spec itself is summarised in the [README](README.md).)

## Design principles

1. **Modular engine, no I/O.** All simulation logic lives in `engine/` and never touches the
   network or filesystem, so the same engine drives the web dashboard, the tests, the headless
   strategy sweep, and (later) an AI optimisation loop.
2. **Everything configurable.** `engine/config.py::SimulationConfig` holds every parameter as
   plain data. The dashboard exposes the major knobs; a future AI can propose whole parameter
   sets.
3. **Reproducible randomness.** One master seed → many named, independent random streams
   (`engine/rng.py`). Same seed ⇒ identical scenario.
4. **Score is a cost; lower = better.** Applied consistently across the engine, the API and the UI.
5. **Zero third-party dependencies.** Pure Python standard library on the backend; vanilla
   HTML/CSS/JS with custom canvas charts on the frontend.

## Spec-section → implementation map

| Spec | Where |
|------|-------|
| §2 Terminology ("repair shop"), 5 shops / 3 suppliers / 5 products | `config.py`, `simulation.py::_build` |
| §3 Repair-shop entity (cash, inventory, sales, satisfaction, P&L, open orders) | `entities.py::RepairShop`, `ShopProductState` |
| §3 Similar-but-not-identical demand per shop | `simulation._build` (per-shop demand multiplier ~ N(1, σ)) |
| §4 Products, baseline velocity, noise, occasional spikes | `config.ProductConfig`, `demand_engine.py` |
| §5 Required inventory = rate × (coverage + lead time) | `inventory_engine.compute_threshold` |
| §6 Tick order of operations | `simulation.step` |
| §7 Supplier entity | `entities.py::Supplier`, `SupplierProductState` |
| §8 Pricing = base × inv-adj × market + noise; correlated via shared market index | `supplier_engine.update_market/update_prices` |
| §9 Risk from historical default rate; real defaults raise it | `supplier_engine.update_risk/register_order_outcome`, defaults realised in `procurement_engine` |
| §10-11 Four-component weighted score, normalised, weights sum to 1 | `procurement_engine.evaluate_offers` |
| §11.4 Bulk-quantity mismatch via pack sizes | `procurement_engine.pack_quantity` + quantity component |
| §12 Decision view data (per-component scores, weights, final) | offer dicts in `evaluate_offers`; UI Procurement tab |
| §13 Market orders | `procurement_engine.place_market_order` |
| §14 Limit orders (execute when score ≤ max) | `create_limit_order` / `evaluate_limit_orders` |
| §15 Scattered orders, never below 60:40 | `procurement_engine._split_allocation` (`min_split_ratio`) |
| §16-17 "In need" / "high inventory" / two-product exchange | `intra_retail_engine.py` |
| §18 Exchange rate = price(A)/price(B), value-balanced | `intra_retail_engine._execute_swap` |
| §19 Centralised platform brokers & records swaps | `intra_retail_engine.find_and_execute`, `order_book` |
| §20 P&L = revenue − procurement − stockout − delay − holding | `financial_engine` |
| §20 Satisfaction ↓ on shortage → churn → lower future demand | `financial_engine`, `demand_engine.churn_multiplier` |
| §21 Instantaneous vs long-term (cumulative) P&L | `financial_engine.update_pnl`, `RepairShop.instant_pnl/pnl` |
| §22 Future AI: configurable weights, parameter sets → P&L | `calibration/sweep.py`, Strategy Lab tab |
| §23 Empirical score↔P&L correlation (not hard-coded) | `simulation._register/_mature_outcomes`, `score_pnl_correlation` |
| §24 Initial parameters | `config` defaults |
| §25 Reproducible seed; randomness on all stochastic parts | `rng.py`, `config.seed`, UI seed control |
| §26-32 Dashboard, order book, charts, controls, explainability | `sim/static/*` |
| §33 Architecture modules | `engine/` package layout |
| §34 Structured event logging | `events.py`, `/api/export` |

## The score↔P&L relationship (§23)

Two complementary, honest demonstrations are provided instead of a hard-coded correlation:

* **Within-run** (Analytics → *Score vs subsequent P&L*): every executed market order records
  its procurement score and the placing shop's P&L change over the following
  `outcome_horizon_days`. The empirical Pearson correlation is computed from those samples and
  shown live. Under the default scenario it is around **−0.3** (lower score → better outcome),
  but the code reports whatever the data gives — it is scenario-dependent, exactly as §23 warns.
* **Cross-strategy** (Strategy Lab): running many weight configurations shows the robust result
  — risk/delivery-aware strategies produce far fewer stockouts and higher long-term P&L than
  naive "cheapest wins", which is the actual product thesis (§36).

## Acceptance criteria (§35)

All 26 acceptance behaviours are observable in the running dashboard:

1-6 start / watch inventory, sales, supplier inventory, prices, demand noise & spikes → Overview
& Analytics charts, Shops/Suppliers tabs. 7-14 threshold approach → engine compares all suppliers
→ weighted scores → lowest chosen → market order → order book → delivery after lead time →
Procurement Engine & Order Book tabs. 15-16 create & trigger limit orders → Order Book creator +
Limit Orders table. 17-19 defaults occur → risk rises → supplier becomes less used despite low
price → Suppliers tab + event feed. 20 complementary swaps → Order Book intra-retail table.
21 satisfaction reacts to shortages → Shops tab + satisfaction chart. 22-24 P&L changes, live P&L
chart, score-vs-P&L relationship → Analytics. 25 change parameters → different outcomes; 26 reset
+ same seed reproduces the scenario → Controls tab (verified by `test_reset_reproduces`).

## Testing

`tests/test_engine.py` and `tests/test_intra_retail.py` (31 tests, stdlib `unittest`) cover RNG
determinism, config overrides, score normalisation & ordering, the 60:40 scatter rule, pack
rounding, risk penalisation, default bookkeeping, price movement, financial accounting,
intra-retail value balance, and simulation determinism / reset reproducibility.
