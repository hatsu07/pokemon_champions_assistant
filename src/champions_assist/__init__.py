"""Observer-only core for Pokémon Champions assistance.

This package deliberately contains no game-control or local tactical-decision code.
"""

from .store import BattleStore

__all__ = ["BattleStore"]
__version__ = "0.1.0"
PROTOCOL_VERSION = "2.3.0"
EFFECT_RULE_SCHEMA_VERSION = "0.2.0"
