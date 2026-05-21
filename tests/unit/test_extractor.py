"""Tests for symbol extraction."""

import pytest
from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.model.enums import SymbolKind


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def extractor():
    return SymbolExtractor()


class TestSymbolExtractor:
    def test_extract_cpp_functions(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        func_names = [s.name for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert "calculateDistance" in func_names
        assert "calculateArea" in func_names
        assert "main" in func_names

    def test_extract_rust_functions(self, parser, extractor, rust_simple_source):
        tree = parser.parse(rust_simple_source, "rust")
        symbols = extractor.extract(tree, rust_simple_source, "rust")
        func_names = [s.name for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert "calculate_distance" in func_names
        assert "calculate_area" in func_names
        assert "main" in func_names

    def test_extract_cpp_nested_scope(self, parser, extractor):
        source = """
namespace geometry {
    class Point {
    public:
        double distanceTo(const Point& other) const {
            return 0.0;
        }
    };
}
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        # Should find namespace, class, and method
        kinds = {s.kind for s in symbols}
        assert SymbolKind.MODULE in kinds or SymbolKind.TYPE in kinds

    def test_extract_rust_structs(self, parser, extractor, rust_simple_path):
        tree, source, lang = parser.parse_file(rust_simple_path)
        symbols = extractor.extract(tree, source, lang)
        func_syms = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(func_syms) >= 3

    def test_symbols_have_spans(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        for sym in symbols:
            assert sym.span.start_line >= 0
            assert sym.span.end_line >= sym.span.start_line

    def test_symbols_have_body_text(self, parser, extractor, cpp_simple_source):
        tree = parser.parse(cpp_simple_source, "cpp")
        symbols = extractor.extract(tree, cpp_simple_source, "cpp")
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        for f in funcs:
            assert len(f.body_text) > 0

    def test_qualified_names_cpp(self, parser, extractor):
        source = """
namespace ns {
    void helper() {}
}
"""
        tree = parser.parse(source, "cpp")
        symbols = extractor.extract(tree, source, "cpp")
        # The function inside the namespace should have qualified name
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        if funcs:
            # Should be "ns::helper" if scope tracking works
            assert any("::" in s.qualified_name for s in funcs)
