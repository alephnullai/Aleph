"""Symbol data model: IDs, raw symbols, and enriched symbols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from aleph.model.enums import SymbolKind, BodyLevel


@dataclass(frozen=True)
class SymbolID:
    """Content-addressed symbol identifier: prefix + hex hash."""
    prefix: str  # Single char from SymbolKind.value
    hex_hash: str  # 6-char hex default, auto-extends on collision

    def __str__(self) -> str:
        return f"{self.prefix}_{self.hex_hash}"

    def __repr__(self) -> str:
        return f"SymbolID('{self}')"

    @staticmethod
    def from_string(s: str) -> SymbolID:
        prefix, hex_hash = s.split("_", 1)
        return SymbolID(prefix=prefix, hex_hash=hex_hash)


@dataclass(frozen=True)
class Span:
    """Source location span."""
    start_line: int
    start_col: int
    end_line: int
    end_col: int


@dataclass
class RawSymbol:
    """A symbol extracted from source before ID assignment."""
    name: str
    qualified_name: str
    kind: SymbolKind
    scope: str  # Enclosing scope as qualified name, "" for top-level
    span: Span
    language: str
    source_file: str = ""
    body_text: str = ""
    signature_text: str = ""


@dataclass
class Symbol:
    """An enriched symbol with ID and metadata."""
    id: SymbolID
    raw: RawSymbol
    salience: float = 0.0
    body_level: BodyLevel = BodyLevel.OMIT
    calls: list[SymbolID] = field(default_factory=list)
    called_by: list[SymbolID] = field(default_factory=list)
    children: list[SymbolID] = field(default_factory=list)
    parent: Optional[SymbolID] = None
    # Phase 1+ fields (empty in Phase 0)
    stability: Optional[str] = None
    churn: Optional[float] = None
    last_modified_days: Optional[int] = None
    intents: list[str] = field(default_factory=list)
    coverage: Optional[str] = None
