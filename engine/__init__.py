"""IntelliFind procurement-automation simulation engine.

Modular simulation of an automated procurement layer between repair shops and
suppliers.  See :mod:`engine.simulation` for the orchestrator and
:mod:`engine.config` for the tunable parameters.
"""

from .config import SimulationConfig, ProductConfig, SupplierConfig
from .simulation import Simulation

__all__ = ["Simulation", "SimulationConfig", "ProductConfig", "SupplierConfig"]
