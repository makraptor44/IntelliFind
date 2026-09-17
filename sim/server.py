"""Dependency-free web server + live dashboard host for the simulation.

Uses only the Python standard library (``http.server``) so the demo runs with
nothing more than ``python3 -m sim.server``.  The simulation advances in a
background thread; the browser polls ``/api/state`` for the live snapshot and
POSTs to the control endpoints.

Endpoints
---------
GET  /                       -> the dashboard (static/index.html)
GET  /static/<file>          -> static assets (js/css)
GET  /api/state              -> full live snapshot (JSON)
GET  /api/export             -> full structured event log (JSON download)
POST /api/control            -> {"action": start|pause|reset|step, "overrides": {...}}
POST /api/weights            -> live procurement-weight change {price,risk,delivery,quantity}
POST /api/speed              -> {"speed": ticks_per_second}
POST /api/limit_order        -> {shop_id, product_id, quantity, max_score}
POST /api/sweep              -> {days?, random_configs?}  run a strategy sweep
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from engine.config import SimulationConfig
from engine.simulation import Simulation

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


# ---------------------------------------------------------------------------
class SimulationController:
    """Owns the simulation and the background stepping thread."""

    def __init__(self):
        self.lock = threading.RLock()
        self.config = SimulationConfig()
        self.sim = Simulation(self.config)
        self.running = False
        self.speed = 12.0            # ticks per second when running
        self._alive = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # -- background loop -------------------------------------------------
    def _loop(self) -> None:
        while self._alive:
            if not self.running:
                time.sleep(0.03)
                continue
            with self.lock:
                if self.sim.is_finished():
                    self.running = False
                    continue
                # batch steps for high speeds so we don't spin the CPU
                steps = max(1, int(self.speed * 0.05))
                for _ in range(steps):
                    if self.sim.is_finished():
                        self.running = False
                        break
                    self.sim.step()
            time.sleep(max(0.005, steps / max(0.001, self.speed)))

    # -- control ---------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if not self.sim.is_finished():
                self.running = True

    def pause(self) -> None:
        with self.lock:
            self.running = False

    def step_once(self, n: int = 1) -> None:
        with self.lock:
            for _ in range(max(1, n)):
                if self.sim.is_finished():
                    break
                self.sim.step()

    def reset(self, overrides: Optional[Dict] = None) -> None:
        with self.lock:
            self.running = False
            cfg = SimulationConfig()
            if overrides:
                cfg.apply_overrides(overrides)
            cfg.validate()
            self.config = cfg
            self.sim.reset(cfg)

    def set_weights(self, weights: Dict[str, float]) -> None:
        with self.lock:
            c = self.config
            c.w_price = float(weights.get("price", c.w_price))
            c.w_risk = float(weights.get("risk", c.w_risk))
            c.w_delivery = float(weights.get("delivery", c.w_delivery))
            c.w_quantity = float(weights.get("quantity", c.w_quantity))
            c.normalize_weights()

    def set_speed(self, speed: float) -> None:
        with self.lock:
            self.speed = max(0.25, min(400.0, float(speed)))

    def add_limit_order(self, shop_id: str, product_id: str,
                        quantity: float, max_score: float) -> Dict[str, Any]:
        with self.lock:
            sim = self.sim
            if shop_id not in sim.shops or product_id not in sim.products:
                return {"ok": False, "error": "unknown shop or product"}
            order = sim.procurement_engine.create_limit_order(
                sim.shops[shop_id], product_id, float(quantity),
                float(max_score), sim.book, sim.tick,
            )
            return {"ok": True, "order_id": order.order_id}

    # -- snapshots -------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        with self.lock:
            snap = self.sim.snapshot()
            snap["runtime"] = {
                "running": self.running,
                "speed": self.speed,
                "finished": self.sim.is_finished(),
            }
            return snap

    def export(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "config": self.config.to_dict(),
                "events": self.sim.log.export(),
                "outcome_samples": list(self.sim._outcome_samples),
            }

    def shutdown(self) -> None:
        self._alive = False


CONTROLLER = SimulationController()


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "IntelliFindSim/1.0"

    # keep the console quiet
    def log_message(self, fmt, *args):  # noqa: N802
        pass

    # -- helpers ---------------------------------------------------------
    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str, content_type: str) -> None:
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(STATIC_DIR, "index.html"), "text/html; charset=utf-8")
        if path == "/api/state":
            return self._send_json(CONTROLLER.state())
        if path == "/api/export":
            data = CONTROLLER.export()
            body = json.dumps(data, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", "attachment; filename=intellifind_events.json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/static/"):
            fname = os.path.basename(path)
            ext = os.path.splitext(fname)[1].lower()
            ctype = {".js": "application/javascript", ".css": "text/css",
                     ".html": "text/html"}.get(ext, "application/octet-stream")
            return self._send_file(os.path.join(STATIC_DIR, fname), ctype + "; charset=utf-8")
        self.send_error(404, "Not found")

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json()
        try:
            if path == "/api/control":
                action = payload.get("action")
                if action == "start":
                    CONTROLLER.start()
                elif action == "pause":
                    CONTROLLER.pause()
                elif action == "step":
                    CONTROLLER.step_once(int(payload.get("n", 1)))
                elif action == "reset":
                    CONTROLLER.reset(payload.get("overrides"))
                else:
                    return self._send_json({"ok": False, "error": "unknown action"}, 400)
                return self._send_json({"ok": True})

            if path == "/api/weights":
                CONTROLLER.set_weights(payload)
                return self._send_json({"ok": True})

            if path == "/api/speed":
                CONTROLLER.set_speed(payload.get("speed", 12))
                return self._send_json({"ok": True})

            if path == "/api/limit_order":
                res = CONTROLLER.add_limit_order(
                    payload.get("shop_id"), payload.get("product_id"),
                    payload.get("quantity", 0), payload.get("max_score", 0.35),
                )
                return self._send_json(res)

            if path == "/api/sweep":
                from calibration.sweep import run_sweep
                out = run_sweep(
                    base=CONTROLLER.config.clone(),
                    days=int(payload.get("days", 40)),
                    random_configs=int(payload.get("random_configs", 0)),
                    seed=int(payload.get("seed", CONTROLLER.config.seed)),
                )
                return self._send_json({"ok": True, "sweep": out})

            self.send_error(404, "Not found")
        except Exception as exc:  # pragma: no cover - defensive
            self._send_json({"ok": False, "error": str(exc)}, 500)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="IntelliFind procurement simulation server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--autostart", action="store_true", help="begin running immediately")
    args = parser.parse_args()

    if args.autostart:
        CONTROLLER.start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://localhost:{args.port}"
    print(f"IntelliFind simulation dashboard running at {url}")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        CONTROLLER.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
