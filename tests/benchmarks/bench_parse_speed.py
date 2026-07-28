"""Benchmark: parse + symbolize + emit timing.

Establishes baseline for Phase 2's <100ms target.
"""

import os
import time
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


class TestParseSpeedBenchmark:
    def test_pipeline_timing(self):
        """Measure pipeline timing for baseline."""
        files = get_fixture_files()
        assert len(files) > 0

        print("\n=== Parse Speed Benchmark ===")
        for path in files:
            start = time.perf_counter()
            result = run_pipeline(path)
            elapsed = time.perf_counter() - start

            print(f"  {os.path.basename(path):30s}  {elapsed*1000:.1f}ms  "
                  f"({result['symbols_extracted']} symbols)")

            # No gate in Phase 0, just establish baseline
            assert elapsed < 30.0, f"Pipeline took {elapsed:.1f}s — something is very wrong"
