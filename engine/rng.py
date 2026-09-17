"""Deterministic, reproducible randomness for the simulation.

A single master seed drives every stochastic process.  To keep the individual
processes independent (so that, for example, changing the demand-noise stream
does not shift supplier prices) each named process gets its own
:class:`random.Random` instance seeded from the master seed plus a stable hash
of its name.  Re-running with the same seed reproduces the scenario exactly,
which satisfies the "Random Seed" reproducibility requirement.
"""

from __future__ import annotations

import random
from typing import Dict


def _stable_offset(name: str) -> int:
    """A deterministic integer derived from ``name`` (independent of PYTHONHASHSEED)."""
    h = 1469598103934665603  # FNV-1a 64-bit offset basis
    for byte in name.encode("utf-8"):
        h ^= byte
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


class RandomStreams:
    """Factory of named, independent, reproducible random streams."""

    def __init__(self, seed: int):
        self.seed = int(seed)
        self._streams: Dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        rng = self._streams.get(name)
        if rng is None:
            rng = random.Random((self.seed ^ _stable_offset(name)) & 0xFFFFFFFFFFFFFFFF)
            self._streams[name] = rng
        return rng

    # convenience helpers -------------------------------------------------
    def uniform(self, name: str, a: float, b: float) -> float:
        return self.stream(name).uniform(a, b)

    def gauss(self, name: str, mu: float, sigma: float) -> float:
        return self.stream(name).gauss(mu, sigma)

    def random(self, name: str) -> float:
        return self.stream(name).random()

    def chance(self, name: str, probability: float) -> bool:
        """True with the given probability, drawn from the named stream."""
        if probability <= 0.0:
            return False
        if probability >= 1.0:
            return True
        return self.stream(name).random() < probability
