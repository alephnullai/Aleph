"""Tests for Aleph serializer."""

from aleph.emit.serializer import AlephSerializer
from aleph.emit.format import STRUCT_HEADER, BODIES_HEADER
from aleph.model.components import (
    StructComponent, BodiesComponent, SignatureEntry, HierarchyNode, BodyEntry,
)
from aleph.model.symbol import Symbol, RawSymbol, Span, SymbolID
from aleph.model.enums import SymbolKind, BodyLevel


def make_symbol(name, hex_hash="abc123", kind=SymbolKind.FUNCTION):
    raw = RawSymbol(
        name=name, qualified_name=name, kind=kind, scope="",
        span=Span(0, 0, 10, 0), language="cpp", body_text="return 0;",
        signature_text=f"int {name}()",
    )
    sid = SymbolID(prefix=kind.value, hex_hash=hex_hash)
    return Symbol(id=sid, raw=raw)


class TestAlephSerializer:
    def test_struct_has_correct_header(self):
        serializer = AlephSerializer()
        sym = make_symbol("test_func")
        comp = StructComponent(
            source_file="test.cpp",
            signatures=[SignatureEntry(
                symbol_id=sym.id, name="test_func", qualified_name="test_func",
                kind="f", signature="int test_func()",
            )],
            hierarchy=[HierarchyNode(symbol_id=sym.id)],
            call_edges=[],
            symbols={str(sym.id): sym},
        )
        output = serializer.serialize_struct(comp)
        assert output.startswith(STRUCT_HEADER)
        assert "[SOURCE:test.cpp]" in output

    def test_struct_has_symbols_section(self):
        serializer = AlephSerializer()
        sym = make_symbol("func1")
        comp = StructComponent(
            source_file="test.cpp", signatures=[], hierarchy=[],
            call_edges=[], symbols={str(sym.id): sym},
        )
        output = serializer.serialize_struct(comp)
        assert "[SYMBOLS]" in output
        assert "[/SYMBOLS]" in output

    def test_struct_sections(self):
        serializer = AlephSerializer()
        sym = make_symbol("func1")
        comp = StructComponent(
            source_file="test.cpp", signatures=[], hierarchy=[],
            call_edges=[("f_abc123", "f_def456")],
            symbols={str(sym.id): sym},
        )
        output = serializer.serialize_struct(comp)
        assert "[SYMBOLS]" in output
        assert "[CALLS]" in output

    def test_bodies_has_correct_header(self):
        serializer = AlephSerializer()
        comp = BodiesComponent(
            source_file="test.cpp", entries=[], symbol_dict={},
        )
        output = serializer.serialize_bodies(comp)
        assert output.startswith(BODIES_HEADER)

    def test_bodies_contains_entries(self):
        serializer = AlephSerializer()
        sid = SymbolID(prefix="f", hex_hash="abc123")
        comp = BodiesComponent(
            source_file="test.cpp",
            entries=[BodyEntry(
                symbol_id=sid, level=BodyLevel.FULL,
                content="return 42;", original_body="return 42;",
            )],
            symbol_dict={"f_abc123": "test_func"},
        )
        output = serializer.serialize_bodies(comp)
        assert "[FULL:f_abc123]" in output
        assert "return 42;" in output

    def test_valid_format_structure(self):
        serializer = AlephSerializer()
        comp = StructComponent(
            source_file="test.cpp", signatures=[], hierarchy=[],
            call_edges=[], symbols={},
        )
        output = serializer.serialize_struct(comp)
        lines = output.strip().split("\n")
        # First line is header
        assert lines[0] == STRUCT_HEADER
