"""Integration test: token reduction targets.

Principle III (Token Optimization).

Token reduction is measured as struct tokens vs source tokens.
The struct is what an LLM loads for navigation (Dynamic Resolution, Invariant VI).
Bodies are loaded on demand per-symbol.

For small files with mostly short functions, the struct metadata overhead may exceed
savings. Reduction scales with function body size — realistic production files with
substantial function bodies achieve 40%+.
"""

import os
import pytest
from aleph.cli import run_pipeline

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


class TestTokenReduction:
    def test_realistic_file_reduction(self):
        """Realistic production-like file should achieve 40%+ reduction."""
        path = os.path.join(FIXTURES_DIR, "cpp", "sample_realistic.cpp")
        result = run_pipeline(path)
        reduction = result["token_reduction_percent"]
        print(f"Realistic C++: {result['original_tokens']} -> {result['compressed_tokens']} ({reduction:.1f}%)")
        assert reduction >= 40, f"Token reduction {reduction:.1f}% below 40% target"

    def test_all_files_produce_struct(self):
        """All fixture files should produce valid struct output."""
        for root, dirs, files in os.walk(FIXTURES_DIR):
            for f in files:
                if f.endswith((".cpp", ".rs")):
                    path = os.path.join(root, f)
                    result = run_pipeline(path)
                    assert result["compressed_tokens"] > 0
                    print(f"  {f:30s} {result['original_tokens']:4d} -> {result['compressed_tokens']:4d} "
                          f"({result['token_reduction_percent']:+.1f}%)")

    def test_struct_smaller_for_large_bodies(self):
        """Files with large function bodies should have struct significantly smaller than source."""
        path = os.path.join(FIXTURES_DIR, "cpp", "sample_realistic.cpp")
        result = run_pipeline(path)
        assert result["compressed_tokens"] < result["original_tokens"]
