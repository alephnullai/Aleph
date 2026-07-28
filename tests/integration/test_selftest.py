"""Integration test for the `aleph selftest` CLI subcommand.

Runs selftest end-to-end (it builds a tiny temp project, serves it over
stdio, and times the core MCP tools) and asserts a clean exit plus that the
key tool calls completed under budget.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run_selftest(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "aleph.cli", "selftest", *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


class TestSelftest:
    def test_selftest_exits_zero_and_all_ok(self):
        result = _run_selftest("--budget", "10")
        assert result.returncode == 0, (
            f"selftest failed (exit {result.returncode}).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        out = result.stdout
        # The four core tools the selftest drives should each report OK.
        for tool in ("aleph_map", "aleph_search", "aleph_struct", "aleph_brief"):
            assert tool in out, f"{tool} missing from selftest output:\n{out}"
        # No tool row should be FAIL/TIMEOUT/DEGRADED (check the status
        # column, not the summary line which always names every state).
        # Table rows are: tool  elapsed  budget  status
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].startswith("aleph_"):
                assert parts[3] == "OK", f"{parts[0]} status was {parts[3]}:\n{out}"
        assert "0 FAIL, 0 TIMEOUT, 0 DEGRADED" in out, f"summary not clean:\n{out}"

    def test_selftest_degraded_on_multi_repo_parent(self, tmp_path):
        """A multi-repo parent serves degraded — selftest must say DEGRADED.

        Degraded-mode tool calls return *successful* results carrying setup
        instructions; the selftest must not report them as OK. The fixture
        is two tiny fake git repos (a bare .git dir is enough for the serve
        guard), which deterministically triggers the degraded serve path.
        """
        for name in ("repo-a", "repo-b"):
            repo = tmp_path / name
            (repo / ".git").mkdir(parents=True)
            (repo / "main.py").write_text("def f():\n    return 1\n")

        result = _run_selftest("--project", str(tmp_path), "--budget", "10")
        assert result.returncode == 2, (
            f"expected degraded exit 2, got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        out = result.stdout
        rows = [
            line.split() for line in out.splitlines()
            if len(line.split()) == 4 and line.split()[0].startswith("aleph_")
        ]
        assert rows, f"no tool rows in selftest output:\n{out}"
        for parts in rows:
            assert parts[3] == "DEGRADED", (
                f"{parts[0]} status was {parts[3]}, expected DEGRADED:\n{out}"
            )
        assert "0 FAIL" in out and f"{len(rows)} DEGRADED" in out, (
            f"summary not degraded-clean:\n{out}"
        )

    def test_selftest_tools_under_budget(self):
        """Every timed tool call completes well under a generous budget."""
        budget = 10.0
        result = _run_selftest("--budget", str(budget))
        assert result.returncode == 0, result.stderr

        # Parse the "tool  elapsed  budget  status" table rows.
        timed = 0
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 4 and parts[0].startswith("aleph_") and parts[3] == "OK":
                seconds = float(parts[1])
                assert seconds < budget, f"{parts[0]} took {seconds}s (budget {budget}s)"
                timed += 1
        assert timed >= 4, f"expected at least 4 timed tool calls, saw {timed}"
