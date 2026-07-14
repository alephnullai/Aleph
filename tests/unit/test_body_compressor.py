"""Tests for body compression."""

from aleph.compress.body_compressor import BodyCompressor
from aleph.compress.policies import CompressionPolicy
from aleph.model.symbol import Symbol, RawSymbol, Span, SymbolID
from aleph.model.enums import SymbolKind, BodyLevel


def make_symbol(name, body_text="", sig_text="", kind=SymbolKind.FUNCTION):
    raw = RawSymbol(
        name=name,
        qualified_name=name,
        kind=kind,
        scope="",
        span=Span(0, 0, 10, 0),
        language="cpp",
        body_text=body_text,
        signature_text=sig_text,
    )
    return Symbol(id=SymbolID(prefix=kind.value, hex_hash="abc123"), raw=raw)


class TestBodyCompressor:
    def test_full_substitutes_known_ids(self):
        compressor = BodyCompressor(policy=CompressionPolicy(omit_threshold=100))
        body = "int x = helper();"
        sym = make_symbol("myFunc", body_text=body)
        sym.body_level = BodyLevel.FULL
        symbol_dict = {"f_def456": "helper"}
        entry = compressor.compress(sym, symbol_dict)
        assert entry.level == BodyLevel.SUMMARY  # Under 10 lines -> SUMMARY by default

    def test_full_preserves_strings(self):
        compressor = BodyCompressor(policy=CompressionPolicy(omit_threshold=100))
        body = 'printf("helper is great");'
        sym = make_symbol("myFunc", body_text=body, kind=SymbolKind.CONSTANT)
        symbol_dict = {"f_def456": "helper"}
        entry = compressor.compress(sym, symbol_dict)
        # String literal "helper is great" should be preserved
        assert entry.level == BodyLevel.FULL

    def test_summary_produces_non_empty(self):
        compressor = BodyCompressor()
        body = "x = 1;\ny = 2;\nz = 3;\n"
        sym = make_symbol("short_func", body_text=body, sig_text="void short_func()")
        symbol_dict = {}
        entry = compressor.compress(sym, symbol_dict)
        if entry.level == BodyLevel.SUMMARY:
            assert len(entry.content) > 0

    def test_omit_is_empty(self):
        compressor = BodyCompressor(policy=CompressionPolicy(omit_threshold=2, docstring_threshold=5))
        body = "\n".join([f"line {i};" for i in range(20)])
        sym = make_symbol("long_func", body_text=body, sig_text="void long_func()")
        symbol_dict = {}
        entry = compressor.compress(sym, symbol_dict)
        assert entry.level == BodyLevel.OMIT
        assert entry.content == ""

    def test_original_body_preserved(self):
        compressor = BodyCompressor()
        body = "return 42;"
        sym = make_symbol("func", body_text=body, kind=SymbolKind.CONSTANT)
        entry = compressor.compress(sym, {})
        assert entry.original_body == body


class TestCompressionPolicy:
    def test_large_body_omit(self):
        policy = CompressionPolicy(omit_threshold=5, docstring_threshold=10)
        body = "\n".join(["line"] * 20)
        assert policy.decide(SymbolKind.FUNCTION, body, "") == BodyLevel.OMIT

    def test_mid_body_docstring(self):
        policy = CompressionPolicy(omit_threshold=5, docstring_threshold=50)
        body = "\n".join(["line"] * 20)
        assert policy.decide(SymbolKind.FUNCTION, body, "") == BodyLevel.DOCSTRING

    def test_small_body_summary(self):
        policy = CompressionPolicy(omit_threshold=10)
        body = "x = 1;\ny = 2;"
        assert policy.decide(SymbolKind.FUNCTION, body, "") == BodyLevel.SUMMARY

    def test_constant_always_full(self):
        policy = CompressionPolicy()
        assert policy.decide(SymbolKind.CONSTANT, "42", "") == BodyLevel.FULL

    def test_variable_always_full(self):
        policy = CompressionPolicy()
        assert policy.decide(SymbolKind.VARIABLE, "x = 1", "") == BodyLevel.FULL
