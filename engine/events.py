"""Structured event logging.

Every economically meaningful action produces a structured record.  These
records are the raw material a future AI/ML layer will train on, so they are
kept as flat JSON-serialisable dicts with a stable ``event_type`` field.

The log keeps two views:
  * ``recent`` - a bounded deque for the live dashboard feed.
  * ``all``    - the full history, exportable via the API for offline analysis.
"""

from __future__ import annotations

from collections import deque, Counter
from typing import Any, Deque, Dict, List, Optional


# Canonical event types (documented for downstream consumers).
EVENT_TYPES = (
    "demand_event",
    "inventory_change",
    "sale",
    "stockout",
    "supplier_price_change",
    "supplier_default",
    "supplier_delivery",
    "supplier_restock",
    "market_order",
    "limit_order_created",
    "limit_order_executed",
    "intra_retail_trade",
    "customer_satisfaction_change",
    "pnl_update",
    "parameter_change",
    "simulation_reset",
)


class EventLog:
    def __init__(self, recent_size: int = 400, keep_all: bool = True):
        self.recent: Deque[Dict[str, Any]] = deque(maxlen=recent_size)
        self.all: List[Dict[str, Any]] = []
        self.keep_all = keep_all
        self.counts: Counter = Counter()
        self._seq = 0

    def emit(self, timestamp: int, event_type: str, **fields: Any) -> Dict[str, Any]:
        self._seq += 1
        record: Dict[str, Any] = {
            "seq": self._seq,
            "timestamp": timestamp,
            "event_type": event_type,
        }
        record.update(fields)
        self.recent.append(record)
        if self.keep_all:
            self.all.append(record)
        self.counts[event_type] += 1
        return record

    def tail(self, n: int = 60, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        items = list(self.recent)
        if event_type:
            items = [e for e in items if e["event_type"] == event_type]
        return items[-n:]

    def tail_types(self, n: int, types) -> List[Dict[str, Any]]:
        """Most recent ``n`` events restricted to the given set of types."""
        allowed = set(types)
        items = [e for e in self.recent if e["event_type"] in allowed]
        return items[-n:]

    def export(self) -> List[Dict[str, Any]]:
        return list(self.all)

    def clear(self) -> None:
        self.recent.clear()
        self.all.clear()
        self.counts.clear()
        self._seq = 0
