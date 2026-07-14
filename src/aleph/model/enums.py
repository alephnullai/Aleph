"""Core enumerations for the Aleph data model."""

from enum import Enum


class SymbolKind(Enum):
    """Symbol type prefixes per PLAN.md symbol naming convention."""
    FUNCTION = "f"
    TYPE = "t"
    VARIABLE = "v"
    MODULE = "m"
    DEPENDENCY = "d"
    CONSTANT = "c"
    SYMBOL = "s"


class BodyLevel(Enum):
    """Body compression levels."""
    FULL = "FULL"
    DOCSTRING = "DOCSTRING"
    SUMMARY = "SUMMARY"
    OMIT = "OMIT"


class StabilityClass(Enum):
    """Temporal stability classification."""
    STABLE = "stable"
    ACTIVE = "active"
    VOLATILE = "volatile"


class AttentionLevel(Enum):
    """Attention budget classification for symbols."""
    CRITICAL = "critical"
    IMPORTANT = "important"
    PERIPHERAL = "peripheral"
    SKIP = "skip"
