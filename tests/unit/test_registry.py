"""Tests for symbol registry."""

from aleph.symbols.registry import SymbolRegistry
from aleph.symbols.identifier import SymbolIdentifier
from aleph.model.symbol import RawSymbol, Span
from aleph.model.symbol import SymbolID
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


class TestSymbolRegistry:
    def test_add_lookup(self):
        reg = SymbolRegistry()
        raw = make_raw()
        sym = reg.register(raw)
        found = reg.lookup(sym.id)
        assert found is sym

    def test_dedup_same_name_scope(self):
        reg = SymbolRegistry()
        raw1 = make_raw(name="foo")
        raw2 = make_raw(name="foo")
        sym1 = reg.register(raw1)
        sym2 = reg.register(raw2)
        assert sym1 is sym2
        assert len(reg) == 1

    def test_different_names(self):
        reg = SymbolRegistry()
        sym1 = reg.register(make_raw(name="foo"))
        sym2 = reg.register(make_raw(name="bar"))
        assert sym1.id != sym2.id
        assert len(reg) == 2

    def test_lookup_by_name(self):
        reg = SymbolRegistry()
        reg.register(make_raw(name="my_func"))
        found = reg.lookup_by_name("my_func")
        assert found is not None
        assert found.raw.name == "my_func"

    def test_symbol_dict(self):
        reg = SymbolRegistry()
        reg.register(make_raw(name="foo"))
        reg.register(make_raw(name="bar"))
        d = reg.symbol_dict()
        assert len(d) == 2
        assert all(isinstance(v, str) for v in d.values())

    def test_all_symbols(self):
        reg = SymbolRegistry()
        reg.register(make_raw(name="a"))
        reg.register(make_raw(name="b"))
        assert len(reg.all_symbols()) == 2

    def test_overloads_are_not_deduped(self):
        reg = SymbolRegistry()
        sym1 = reg.register(make_raw(name="foo", signature_text="int foo(int x)"))
        sym2 = reg.register(make_raw(name="foo", signature_text="int foo(double x)"))
        assert sym1 is not sym2
        assert sym1.id != sym2.id

    def test_same_symbol_different_files_not_deduped(self):
        reg = SymbolRegistry()
        sym1 = reg.register(make_raw(name="helper", source_file="a.cpp"))
        sym2 = reg.register(make_raw(name="helper", source_file="b.cpp"))
        assert sym1 is not sym2
        assert sym1.id != sym2.id

    def test_collision_auto_extension(self, monkeypatch):
        reg = SymbolRegistry()

        def fake_assign_id(self, raw, length=None):
            if length is None or length == 6:
                return SymbolID(prefix=raw.kind.value, hex_hash="aaaaaa")
            if length == 8:
                suffix = "11" if raw.name.endswith("1") else "22"
                return SymbolID(prefix=raw.kind.value, hex_hash=f"aaaaaa{suffix}")
            return SymbolID(prefix=raw.kind.value, hex_hash="a" * (length or 12))

        monkeypatch.setattr(SymbolIdentifier, "assign_id", fake_assign_id)

        s1 = reg.register(make_raw(name="collision_1"))
        s2 = reg.register(make_raw(name="collision_2"))
        assert s1.id != s2.id
        assert len(s2.id.hex_hash) == 8
