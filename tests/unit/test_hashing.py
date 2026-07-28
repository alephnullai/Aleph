"""Tests for hashing utilities."""

import re
from aleph.util.hashing import symbol_id_hash, semantic_hash, byte_hash


class TestSymbolIdHash:
    def test_deterministic(self):
        h1 = symbol_id_hash("foo::bar", "foo")
        h2 = symbol_id_hash("foo::bar", "foo")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        h1 = symbol_id_hash("foo::bar", "foo")
        h2 = symbol_id_hash("foo::baz", "foo")
        assert h1 != h2

    def test_default_length_6(self):
        h = symbol_id_hash("test_func", "")
        assert len(h) == 6

    def test_custom_length(self):
        h = symbol_id_hash("test_func", "", length=8)
        assert len(h) == 8

    def test_hex_format(self):
        h = symbol_id_hash("test_func", "")
        assert re.match(r"^[0-9a-f]{6}$", h)

    def test_scope_matters(self):
        h1 = symbol_id_hash("method", "ClassA")
        h2 = symbol_id_hash("method", "ClassB")
        assert h1 != h2


class TestSemanticHash:
    def test_deterministic(self):
        data = {"nodes": ["a", "b"], "edges": [("a", "b")]}
        h1 = semantic_hash(data)
        h2 = semantic_hash(data)
        assert h1 == h2

    def test_order_invariant(self):
        # JSON sort_keys makes this order-invariant
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        assert semantic_hash(d1) == semantic_hash(d2)

    def test_different_data_different_hash(self):
        h1 = semantic_hash({"nodes": ["a"]})
        h2 = semantic_hash({"nodes": ["b"]})
        assert h1 != h2

    def test_returns_12_char_hex(self):
        h = semantic_hash({"test": True})
        assert len(h) == 12
        assert re.match(r"^[0-9a-f]{12}$", h)


class TestByteHash:
    def test_string_input(self):
        h = byte_hash("hello")
        assert len(h) == 64  # full SHA256

    def test_bytes_input(self):
        h = byte_hash(b"hello")
        assert len(h) == 64

    def test_deterministic(self):
        assert byte_hash("test") == byte_hash("test")

    def test_different_input(self):
        assert byte_hash("a") != byte_hash("b")
