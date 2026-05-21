"""Integration test: self-application.

Run pipeline on Aleph's own Python source files.
Principle VII (Self-Application).
"""

import os
import pytest
from aleph.cli import run_pipeline


def get_aleph_source_files():
    """Find all .py files in src/aleph/."""
    src_dir = os.path.join(os.path.dirname(__file__), "..", "..", "src", "aleph")
    src_dir = os.path.abspath(src_dir)
    py_files = []
    for root, dirs, files in os.walk(src_dir):
        for f in files:
            if f.endswith(".py") and f != "__init__.py":
                py_files.append(os.path.join(root, f))
    return py_files


class TestSelfApplication:
    def test_pipeline_runs_on_own_source(self):
        """Pipeline must run on all Aleph .py source files without errors."""
        py_files = get_aleph_source_files()
        assert len(py_files) > 0, "No source files found"
        files_with_symbols = 0

        for path in py_files:
            result = run_pipeline(path)
            if result["symbols_extracted"] > 0:
                files_with_symbols += 1
            assert result["struct_text"], f"No struct output for {path}"
            assert result["bodies_text"], f"No bodies output for {path}"
        assert files_with_symbols > 0

    def test_self_application_produces_valid_aleph(self):
        """Output for own source should be parseable Aleph format."""
        py_files = get_aleph_source_files()
        for path in py_files[:3]:  # Test first 3 files
            result = run_pipeline(path)
            assert "[ALEPH:STRUCT:1.0]" in result["struct_text"]
            assert "[ALEPH:BODIES:1.0]" in result["bodies_text"]

    def test_self_application_token_reduction(self):
        """Measure token reduction on own source.

        Note: combined struct+bodies output may be larger than the original because
        structural metadata (headers, dict, hierarchy) adds overhead. The real value
        is in Dynamic Resolution — loading only what's needed. This test logs the
        numbers for tracking but doesn't assert positive reduction on the prototype's
        own small Python files.
        """
        py_files = get_aleph_source_files()
        total_original = 0
        total_compressed = 0
        for path in py_files:
            result = run_pipeline(path)
            total_original += result["original_tokens"]
            total_compressed += result["compressed_tokens"]

        if total_original > 0:
            overall_reduction = (1 - total_compressed / total_original) * 100
            print(f"Self-application: {total_original} -> {total_compressed} ({overall_reduction:.1f}%)")
            # Pipeline runs to completion — that's the self-application proof
