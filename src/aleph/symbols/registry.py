"""Symbol registry: in-memory store with dedup and collision detection."""

from __future__ import annotations

from aleph.model.symbol import Symbol, SymbolID, RawSymbol
from aleph.symbols.identifier import SymbolIdentifier


class SymbolRegistry:
    """In-memory symbol store with dedup and collision auto-extension."""

    def __init__(self) -> None:
        self._symbols: dict[str, Symbol] = {}  # id_str -> Symbol
        self._by_qualified_name: dict[str, str] = {}  # qualified_name -> id_str
        self._identifier = SymbolIdentifier()

    def register(self, raw: RawSymbol, **kwargs) -> Symbol:
        """Register a RawSymbol, handling dedup and collision detection.

        Returns the Symbol (existing if dedup, new if fresh).
        """
        # Check dedup using full identity key (supports overloads/provenance).
        dedup_key = self._identifier.dedup_key(raw)
        if dedup_key in self._by_qualified_name:
            existing_id = self._by_qualified_name[dedup_key]
            return self._symbols[existing_id]

        # Assign ID
        sym_id = self._identifier.assign_id(raw)
        id_str = str(sym_id)

        # Collision detection: same ID but different qualified_name
        if id_str in self._symbols:
            existing = self._symbols[id_str]
            if existing.raw.qualified_name != raw.qualified_name:
                # Auto-extend hash length
                sym_id = self._identifier.assign_id(raw, length=8)
                id_str = str(sym_id)
                # If still collides (extremely unlikely), extend further
                if id_str in self._symbols and self._symbols[id_str].raw.qualified_name != raw.qualified_name:
                    sym_id = self._identifier.assign_id(raw, length=12)
                    id_str = str(sym_id)

        symbol = Symbol(id=sym_id, raw=raw, **kwargs)
        self._symbols[id_str] = symbol
        self._by_qualified_name[dedup_key] = id_str
        return symbol

    def lookup(self, symbol_id: SymbolID | str) -> Symbol | None:
        id_str = str(symbol_id)
        return self._symbols.get(id_str)

    def lookup_by_name(self, qualified_name: str) -> Symbol | None:
        for sym in self._symbols.values():
            if sym.raw.qualified_name == qualified_name:
                return sym
        return None

    def all_symbols(self) -> list[Symbol]:
        return list(self._symbols.values())

    def symbol_dict(self) -> dict[str, str]:
        """Return id_str -> qualified_name mapping for the bodies component."""
        return {id_str: sym.raw.qualified_name for id_str, sym in self._symbols.items()}

    def __len__(self) -> int:
        return len(self._symbols)
