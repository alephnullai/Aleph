"""Tests for symbol identification."""

import re
from aleph.symbols.identifier import SymbolIdentifier
from aleph.model.symbol import RawSymbol, Span
from aleph.model.enums import SymbolKind


def make_raw(
    name="test_func",
    scope="",
    kind=SymbolKind.FUNCTION,
    signature_text="int test_func()",
    source_file="a.cpp",
):
    return RawSymbol(
        name=name,
        qualified_name=f"{scope}::{name}" if scope else name,
        kind=kind,
        scope=scope,
        span=Span(0, 0, 10, 0),
        language="cpp",
        source_file=source_file,
        signature_text=signature_text,
    )


class TestSymbolIdentifier:
    def test_deterministic(self):
        ident = SymbolIdentifier()
        raw = make_raw()
        id1 = ident.assign_id(raw)
        id2 = ident.assign_id(raw)
        assert id1 == id2

    def test_prefix_matches_kind(self):
        ident = SymbolIdentifier()
        for kind in SymbolKind:
            raw = make_raw(kind=kind)
            sid = ident.assign_id(raw)
            assert sid.prefix == kind.value

    def test_format_matches_regex(self):
        ident = SymbolIdentifier()
        raw = make_raw()
        sid = ident.assign_id(raw)
        assert re.match(r"^[ftvcmds]_[0-9a-f]{6}$", str(sid))

    def test_rename_produces_new_id(self):
        ident = SymbolIdentifier()
        raw1 = make_raw(name="funcA")
        raw2 = make_raw(name="funcB")
        assert ident.assign_id(raw1) != ident.assign_id(raw2)

    def test_scope_affects_id(self):
        ident = SymbolIdentifier()
        raw1 = make_raw(name="method", scope="ClassA")
        raw2 = make_raw(name="method", scope="ClassB")
        assert ident.assign_id(raw1) != ident.assign_id(raw2)

    def test_signature_affects_id(self):
        ident = SymbolIdentifier()
        raw1 = make_raw(name="overload", signature_text="int overload(int x)")
        raw2 = make_raw(name="overload", signature_text="int overload(double x)")
        assert ident.assign_id(raw1) != ident.assign_id(raw2)

    def test_source_file_affects_id(self):
        ident = SymbolIdentifier()
        raw1 = make_raw(name="helper", source_file="a.cpp")
        raw2 = make_raw(name="helper", source_file="b.cpp")
        assert ident.assign_id(raw1) != ident.assign_id(raw2)

    def test_custom_length(self):
        ident = SymbolIdentifier()
        raw = make_raw()
        sid = ident.assign_id(raw, length=8)
        assert len(sid.hex_hash) == 8
