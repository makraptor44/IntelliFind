# mcp_server/ — (future) tool surface for the AI layer

**Not implemented yet — placeholder for the planned AI optimisation layer.**

The intent (spec §22, §33) is to expose the simulation engine as a set of tools an AI agent can
call, e.g.:

- `run_simulation(config)` → long-term P&L and summary metrics
- `get_history()` / `get_events()` → structured training data
- `set_procurement_weights(...)` → adjust the strategy
- `evaluate_offers(...)` → inspect a procurement decision

The engine in `engine/` is already I/O-free and driven entirely by `SimulationConfig`, so wiring
these tools is additive and requires no engine changes. Until then, the same capabilities are
available through `sim/server.py` (REST) and `calibration/sweep.py` (headless sweeps).
