"""Tests for signature extraction."""

import pytest
from aleph.ingest.parser import TreeSitterParser
from aleph.symbols.extractor import SymbolExtractor
from aleph.symbols.registry import SymbolRegistry
from aleph.structure.signatures import SignatureExtractor
from aleph.model.enums import SymbolKind


@pytest.fixture
def parser():
    return TreeSitterParser()


@pytest.fixture
def sig_extractor():
    return SignatureExtractor()


def extract_symbols(parser, source, language):
    tree = parser.parse(source, language)
    extractor = SymbolExtractor()
    raws = extractor.extract(tree, source, language)
    registry = SymbolRegistry()
    return [registry.register(r) for r in raws]


class TestSignatureExtractor:
    def test_cpp_function_sig(self, parser, sig_extractor, cpp_simple_source):
        symbols = extract_symbols(parser, cpp_simple_source, "cpp")
        funcs = [s for s in symbols if s.raw.kind == SymbolKind.FUNCTION]
        for func in funcs:
            entry = sig_extractor.extract(func)
            assert entry.name == func.raw.name
            assert entry.kind == "f"

    def test_rust_function_sig(self, parser, sig_extractor, rust_simple_source):
        symbols = extract_symbols(parser, rust_simple_source, "rust")
        funcs = [s for s in symbols if s.raw.kind == SymbolKind.FUNCTION]
        for func in funcs:
            entry = sig_extractor.extract(func)
            assert entry.name == func.raw.name

    def test_cpp_params_extracted(self, parser, sig_extractor):
        source = "double calculateDistance(double x1, double y1, double x2, double y2) { return 0; }"
        symbols = extract_symbols(parser, source, "cpp")
        funcs = [s for s in symbols if s.raw.kind == SymbolKind.FUNCTION]
        assert len(funcs) >= 1
        entry = sig_extractor.extract(funcs[0])
        assert len(entry.params) == 4

    def test_rust_return_type(self, parser, sig_extractor):
        source = "fn calculate(x: f64) -> f64 { x * 2.0 }"
        symbols = extract_symbols(parser, source, "rust")
        funcs = [s for s in symbols if s.raw.kind == SymbolKind.FUNCTION]
        assert len(funcs) >= 1
        entry = sig_extractor.extract(funcs[0])
        assert "f64" in entry.return_type

    def test_visibility_rust_pub(self, parser, sig_extractor):
        source = "pub fn public_func() {}\nfn private_func() {}"
        symbols = extract_symbols(parser, source, "rust")
        for sym in symbols:
            if sym.raw.kind == SymbolKind.FUNCTION:
                entry = sig_extractor.extract(sym)
                if sym.raw.name == "public_func":
                    assert entry.visibility == "public"
                elif sym.raw.name == "private_func":
                    assert entry.visibility == "private"
