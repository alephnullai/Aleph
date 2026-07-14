"""Property-based tests for roundtrip correctness.

For any valid function body, expand(compress_full(body, dict), dict) == body.
"""

from hypothesis import given, strategies as st, assume

from aleph.compress.body_compressor import BodyCompressor
from aleph.compress.policies import CompressionPolicy
from aleph.emit.loader import AlephLoader
from aleph.model.symbol import Symbol, RawSymbol, Span, SymbolID
from aleph.model.enums import SymbolKind, BodyLevel


# Strategy for simple function bodies (no string literals for simplicity)
simple_bodies = st.from_regex(r"[a-zA-Z0-9_ \t\n\+\-\*\/\=\;\(\)\{\}]{1,200}", fullmatch=True)


class TestRoundtripProperties:
    @given(body=simple_bodies)
    def test_original_body_always_preserved(self, body):
        """The original body is always stored, enabling roundtrip."""
        raw = RawSymbol(
            name="test_func", qualified_name="test_func",
            kind=SymbolKind.FUNCTION, scope="",
            span=Span(0, 0, 10, 0), language="cpp",
            body_text=body, signature_text="void test_func()",
        )
        sym = Symbol(id=SymbolID(prefix="f", hex_hash="abc123"), raw=raw)
        compressor = BodyCompressor()
        entry = compressor.compress(sym, {})
        assert entry.original_body == body

    @given(body=simple_bodies)
    def test_full_compression_with_empty_dict(self, body):
        """With no symbols to substitute, FULL compression preserves body exactly."""
        raw = RawSymbol(
            name="test_func", qualified_name="test_func",
            kind=SymbolKind.CONSTANT, scope="",
            span=Span(0, 0, 10, 0), language="cpp",
            body_text=body, signature_text="",
        )
        sym = Symbol(id=SymbolID(prefix="c", hex_hash="abc123"), raw=raw)
        # Force FULL level via constant kind
        compressor = BodyCompressor()
        entry = compressor.compress(sym, {})
        assert entry.level == BodyLevel.FULL
        assert entry.content == body

    @given(
        body=st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{2,20}\(\);", fullmatch=True),
        callee=st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{2,12}", fullmatch=True),
    )
    def test_full_compression_roundtrip_with_symbol_dict(self, body, callee):
        replaced_body = body.replace(body.split("(")[0], callee)
        raw = RawSymbol(
            name="caller", qualified_name="caller",
            kind=SymbolKind.CONSTANT, scope="",
            span=Span(0, 0, 10, 0), language="cpp",
            body_text=replaced_body, signature_text="",
        )
        sym = Symbol(id=SymbolID(prefix="c", hex_hash="abc123"), raw=raw)
        symbol_dict = {"f_deadbe": callee}
        entry = BodyCompressor().compress(sym, symbol_dict)
        expanded = AlephLoader().expand_entry(entry, symbol_dict)
        assert expanded == replaced_body
