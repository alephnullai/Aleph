"""Semantic fingerprint: content-addressed hash of a symbol graph."""

from __future__ import annotations

from aleph.model.symbol import Symbol
from aleph.util.hashing import semantic_hash, byte_hash


class SemanticFingerprint:
    """Computes a reformat-invariant fingerprint over a set of symbols."""

    def compute(self, symbols: list[Symbol]) -> str:
        """Hash over sorted set of symbol identity + local relationships.

        Reformat-invariant by construction: based on semantic structure,
        not source text layout.
        """
        entries = []
        for sym in sorted(symbols, key=lambda s: str(s.id)):
            sig_hash = byte_hash(sym.raw.signature_text) if sym.raw.signature_text else ""
            call_ids = sorted(str(c) for c in sym.calls)
            entries.append({
                "id": str(sym.id),
                "kind": sym.raw.kind.value,
                "language": sym.raw.language,
                "scope": sym.raw.scope,
                "sig_hash": sig_hash,
                "calls": call_ids,
                "called_by": sorted(str(c) for c in sym.called_by),
                "parent": str(sym.parent) if sym.parent else "",
            })

        return semantic_hash({"symbols": entries})
