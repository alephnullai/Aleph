"""Symbol identification: content-addressed hash assignment."""

from __future__ import annotations

import re

from aleph.model.symbol import SymbolID, RawSymbol
from aleph.util.hashing import symbol_id_hash


class SymbolIdentifier:
    """Assigns content-addressed SymbolIDs to RawSymbols."""

    DEFAULT_LENGTH = 6  # 6-char hex per Open Question 8 resolution

    def _normalized_signature(self, raw: RawSymbol) -> str:
        # Normalize whitespace and trim for stable overload differentiation.
        return re.sub(r"\s+", " ", raw.signature_text or "").strip()

    def dedup_key(self, raw: RawSymbol) -> str:
        return "|".join(
            [
                raw.qualified_name,
                raw.scope,
                raw.language,
                raw.source_file,
                self._normalized_signature(raw),
            ]
        )

    def assign_id(self, raw: RawSymbol, length: int | None = None) -> SymbolID:
        """Create a SymbolID from a RawSymbol identity fields."""
        length = length or self.DEFAULT_LENGTH
        hex_hash = symbol_id_hash(
            raw.qualified_name,
            raw.scope,
            language=raw.language,
            source_file=raw.source_file,
            signature=self._normalized_signature(raw),
            length=length,
        )
        return SymbolID(prefix=raw.kind.value, hex_hash=hex_hash)
