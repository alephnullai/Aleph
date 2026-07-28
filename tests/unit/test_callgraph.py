"""Tests for call graph builder."""

import pytest
from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry
from aleph.structure.callgraph import CallGraphBuilder
from aleph.model.enums import SymbolKind


@pytest.fixture
def parser():
    return TreeSitterParser()


def build_symbols(parser, source, language):
    tree = parser.parse(source, language)
    extractor = SymbolExtractor()
    raws = extractor.extract(tree, source, language)
    registry = SymbolRegistry()
    return [registry.register(r) for r in raws], tree


class TestCallGraphBuilder:
    def test_direct_calls_identified(self, parser, cpp_simple_source):
        symbols, tree = build_symbols(parser, cpp_simple_source, "cpp")
        builder = CallGraphBuilder()
        source_bytes = cpp_simple_source.encode("utf-8")
        edges = builder.build(tree, source_bytes, "cpp", symbols)
        # main calls calculateDistance, calculateArea, printResult
        assert len(edges) > 0

    def test_rust_calls(self, parser, rust_simple_source):
        symbols, tree = build_symbols(parser, rust_simple_source, "rust")
        builder = CallGraphBuilder()
        source_bytes = rust_simple_source.encode("utf-8")
        edges = builder.build(tree, source_bytes, "rust", symbols)
        assert len(edges) > 0

    def test_recursive_calls_handled(self, parser):
        source = """
int factorial(int n) {
    if (n <= 1) return 1;
    return n * factorial(n - 1);
}
"""
        symbols, tree = build_symbols(parser, source, "cpp")
        builder = CallGraphBuilder()
        edges = builder.build(tree, source.encode("utf-8"), "cpp", symbols)
        # Recursive call should NOT create self-edge (caller != callee check)
        for caller, callee in edges:
            assert caller != callee

    def test_apply_to_symbols(self, parser, cpp_simple_source):
        symbols, tree = build_symbols(parser, cpp_simple_source, "cpp")
        builder = CallGraphBuilder()
        source_bytes = cpp_simple_source.encode("utf-8")
        edges = builder.build(tree, source_bytes, "cpp", symbols)
        builder.apply_to_symbols(edges, symbols)
        # At least one symbol should have calls or called_by
        has_calls = any(len(s.calls) > 0 for s in symbols)
        has_called_by = any(len(s.called_by) > 0 for s in symbols)
        # Both should be true if edges were found
        if edges:
            assert has_calls or has_called_by
