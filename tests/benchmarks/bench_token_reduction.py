"""Benchmark: corpus-wide token reduction."""

import os
import pytest
from aleph.cli import run_pipeline


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def get_fixture_files():
    files = []
    for root, dirs, filenames in os.walk(FIXTURES_DIR):
        for f in filenames:
            if f.endswith((".cpp", ".rs")):
                files.append(os.path.join(root, f))
    return files


class TestTokenReductionBenchmark:
    def test_corpus_reduction(self):
        """Average token reduction across all fixture files."""
        files = get_fixture_files()
        assert len(files) > 0

        total_original = 0
        total_compressed = 0
        results = []

        for path in files:
            result = run_pipeline(path)
            total_original += result["original_tokens"]
            total_compressed += result["compressed_tokens"]
            results.append({
                "file": os.path.basename(path),
                "original": result["original_tokens"],
                "compressed": result["compressed_tokens"],
                "reduction": result["token_reduction_percent"],
            })

        avg_reduction = (1 - total_compressed / total_original) * 100

        print("\n=== Token Reduction Benchmark ===")
        for r in results:
            print(f"  {r['file']:30s}  {r['original']:5d} -> {r['compressed']:5d}  ({r['reduction']:.1f}%)")
        print(f"  {'TOTAL':30s}  {total_original:5d} -> {total_compressed:5d}  ({avg_reduction:.1f}%)")

        # Log for CI regression detection
        assert avg_reduction > 0, "Average token reduction should be positive"
