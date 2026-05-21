"""Hierarchy builder: containment tree from symbols."""

from __future__ import annotations

from aleph.model.symbol import Symbol, SymbolID
from aleph.model.enums import SymbolKind
from aleph.model.components import HierarchyNode


class HierarchyBuilder:
    """Builds a containment tree (namespace > class > method) from symbols."""

    def build(self, symbols: list[Symbol]) -> list[HierarchyNode]:
        """Build hierarchy nodes. Returns top-level roots."""
        # Index by qualified name for parent lookup
        by_qname: dict[str, Symbol] = {}
        for sym in symbols:
            by_qname[sym.raw.qualified_name] = sym

        # Build parent-child relationships
        children_of: dict[str, list[Symbol]] = {}  # parent qname -> children
        roots: list[Symbol] = []

        for sym in symbols:
            if sym.raw.scope and sym.raw.scope in [s.raw.qualified_name for s in symbols]:
                parent_qname = sym.raw.scope
                if parent_qname not in children_of:
                    children_of[parent_qname] = []
                children_of[parent_qname].append(sym)
            else:
                roots.append(sym)

        # Build tree nodes recursively
        def build_node(sym: Symbol) -> HierarchyNode:
            child_syms = children_of.get(sym.raw.qualified_name, [])
            child_nodes = [build_node(c) for c in child_syms]
            return HierarchyNode(symbol_id=sym.id, children=child_nodes)

        return [build_node(r) for r in roots]

    def assign_parents(self, symbols: list[Symbol]) -> None:
        """Set parent/children fields on Symbol objects in-place."""
        by_qname: dict[str, Symbol] = {}
        for sym in symbols:
            by_qname[sym.raw.qualified_name] = sym

        for sym in symbols:
            if sym.raw.scope:
                parent = by_qname.get(sym.raw.scope)
                if parent:
                    sym.parent = parent.id
                    if sym.id not in parent.children:
                        parent.children.append(sym.id)
