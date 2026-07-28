"""Tests for hierarchy builder."""

from aleph.structure.hierarchy import HierarchyBuilder
from aleph.model.symbol import Symbol, RawSymbol, Span, SymbolID
from aleph.model.enums import SymbolKind


def make_symbol(name, scope="", kind=SymbolKind.FUNCTION, prefix="f", hex_hash="abc123"):
    qname = f"{scope}::{name}" if scope else name
    raw = RawSymbol(
        name=name, qualified_name=qname, kind=kind, scope=scope,
        span=Span(0, 0, 10, 0), language="cpp",
    )
    return Symbol(id=SymbolID(prefix=prefix, hex_hash=hex_hash), raw=raw)


class TestHierarchyBuilder:
    def test_flat_hierarchy(self):
        builder = HierarchyBuilder()
        syms = [
            make_symbol("funcA", hex_hash="aaa111"),
            make_symbol("funcB", hex_hash="bbb222"),
        ]
        roots = builder.build(syms)
        assert len(roots) == 2

    def test_parent_child(self):
        builder = HierarchyBuilder()
        parent = make_symbol("MyClass", kind=SymbolKind.TYPE, prefix="t", hex_hash="ppp111")
        child = make_symbol("method", scope="MyClass", hex_hash="ccc111")
        syms = [parent, child]
        roots = builder.build(syms)
        assert len(roots) == 1
        assert len(roots[0].children) == 1

    def test_assign_parents(self):
        builder = HierarchyBuilder()
        parent = make_symbol("MyClass", kind=SymbolKind.TYPE, prefix="t", hex_hash="ppp222")
        child = make_symbol("method", scope="MyClass", hex_hash="ccc222")
        syms = [parent, child]
        builder.assign_parents(syms)
        assert child.parent == parent.id
        assert child.id in parent.children
