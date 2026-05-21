"""Tests for token counting utilities."""

from aleph.util.tokens import count_tokens, compare_tokens, TokenComparison


class TestCountTokens:
    def test_known_input(self):
        tokens = count_tokens("hello world")
        assert tokens > 0

    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_code_snippet(self):
        code = "int calculateDistanceBetweenTwoPoints(double x1, double y1) { return 0; }"
        tokens = count_tokens(code)
        assert tokens > 10


class TestCompareTokens:
    def test_comparison(self):
        # Longer identifiers produce more tokens than short symbol IDs
        original = "void calculateDistanceBetweenTwoPoints() { int longVariableNameForStorage = 0; processInputDataFromExternalSource(); }"
        compressed = "void f_a3c9() { int v_b1e2 = 0; f_c3d4(); }"
        result = compare_tokens(original, compressed)
        assert isinstance(result, TokenComparison)
        assert result.original_tokens > result.compressed_tokens

    def test_reduction_percent(self):
        comp = TokenComparison(original_tokens=100, compressed_tokens=60)
        assert comp.reduction_percent == 40.0

    def test_zero_original(self):
        comp = TokenComparison(original_tokens=0, compressed_tokens=0)
        assert comp.reduction == 0.0
