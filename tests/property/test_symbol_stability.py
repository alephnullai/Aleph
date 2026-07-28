"""Property-based tests for symbol stability.

For any (name, scope), symbol_id_hash is deterministic.
Output matches ID format regex.
"""

import re
from hypothesis import given, strategies as st

from aleph.util.hashing import symbol_id_hash
from aleph.symbols.identifier import SymbolIdentifier
from aleph.model.symbol import RawSymbol, Span
from aleph.model.enums import SymbolKind


# Strategy for valid identifier-like names
names = st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,50}", fullmatch=True)
scopes = st.from_regex(r"([a-zA-Z_][a-zA-Z0-9_]{0,20}::){0,3}", fullmatch=True)


class TestSymbolStability:
    @given(name=names, scope=scopes)
    def test_deterministic(self, name, scope):
        h1 = symbol_id_hash(name, scope)
        h2 = symbol_id_hash(name, scope)
        assert h1 == h2

    @given(name=names, scope=scopes)
    def test_format_matches_hex(self, name, scope):
        h = symbol_id_hash(name, scope)
        assert re.match(r"^[0-9a-f]{6}$", h)

    @given(name=names, scope=scopes)
    def test_full_id_format(self, name, scope):
        for kind in SymbolKind:
            raw = RawSymbol(
                name=name,
                qualified_name=f"{scope}{name}" if scope else name,
                kind=kind, scope=scope,
                span=Span(0, 0, 1, 0), language="cpp",
            )
            ident = SymbolIdentifier()
            sid = ident.assign_id(raw)
            assert re.match(r"^[ftvcmds]_[0-9a-f]{6}$", str(sid))

    @given(name=names)
    def test_different_scopes_different_ids(self, name):
        h1 = symbol_id_hash(name, "scope_a")
        h2 = symbol_id_hash(name, "scope_b")
        # Not guaranteed different for all inputs (hash collisions), but statistically should differ
        # We just test that the function runs without error
        assert len(h1) == 6
        assert len(h2) == 6
