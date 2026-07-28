"""`aleph selftest` contract — the release gate must stay complete.

The responsiveness contract (docs/RESPONSIVENESS_CONTRACT.md) is only as
good as its coverage: a tool that the selftest does not exercise can hang
in production without ever failing the gate. These tests pin the
selftest's tool list to the LIVE registry in aleph.mcp.server, and pin
the budget/classification plumbing the gate relies on.
"""

from __future__ import annotations

import tempfile

import pytest

from aleph.cli import (
    _selftest_budget_multiplier,
    _selftest_tool_calls,
    _selftest_tool_status,
)
from aleph.mcp.server import create_server


def _registered_tool_names() -> set[str]:
    with tempfile.TemporaryDirectory() as d:
        # Degraded mode: registers every tool without needing artifacts.
        server = create_server(d, degraded_message="contract probe")
        return set(server._tool_manager._tools)


class TestSelftestToolCoverage:
    def test_selftest_exercises_every_registered_tool(self):
        """Adding an MCP tool without adding a selftest call fails here."""
        registered = _registered_tool_names()
        exercised = {name for name, _ in _selftest_tool_calls("S1")}
        missing = registered - exercised
        assert not missing, (
            f"MCP tool(s) registered but NOT exercised by `aleph selftest`: "
            f"{sorted(missing)} — add a call (with safe fixture arguments) "
            f"to _selftest_tool_calls in src/aleph/cli.py so the "
            f"responsiveness gate covers them"
        )

    def test_selftest_calls_no_phantom_tools(self):
        """A selftest entry for a removed/renamed tool fails here."""
        registered = _registered_tool_names()
        exercised = {name for name, _ in _selftest_tool_calls("S1")}
        phantom = exercised - registered
        assert not phantom, (
            f"`aleph selftest` calls tool(s) that no longer exist: "
            f"{sorted(phantom)} — update _selftest_tool_calls in "
            f"src/aleph/cli.py"
        )

    def test_selftest_calls_each_tool_exactly_once(self):
        names = [name for name, _ in _selftest_tool_calls("S1")]
        assert len(names) == len(set(names)), (
            "duplicate entries in _selftest_tool_calls skew the budget table"
        )

    def test_symbol_id_is_threaded_into_symbol_tools(self):
        """The harvested symbol id must reach the symbol-arg tools."""
        calls = dict(_selftest_tool_calls("SID-MARKER"))
        for tool in ("aleph_expand", "aleph_resolve", "aleph_callers",
                     "aleph_context", "aleph_impact"):
            assert calls[tool].get("symbol_id") == "SID-MARKER", (
                f"{tool} does not receive the harvested symbol id"
            )


class TestBudgetMultiplier:
    def test_default_is_one(self, monkeypatch):
        monkeypatch.delenv("ALEPH_SELFTEST_BUDGET_MULT", raising=False)
        assert _selftest_budget_multiplier() == 1.0

    def test_env_scales(self, monkeypatch):
        monkeypatch.setenv("ALEPH_SELFTEST_BUDGET_MULT", "2.5")
        assert _selftest_budget_multiplier() == 2.5

    @pytest.mark.parametrize("bad", ["", "abc", "0", "-3"])
    def test_garbage_and_nonpositive_fall_back_to_one(self, monkeypatch, bad):
        monkeypatch.setenv("ALEPH_SELFTEST_BUDGET_MULT", bad)
        assert _selftest_budget_multiplier() == 1.0


class TestToolStatusClassification:
    def test_no_response_is_timeout(self):
        """TIMEOUT (not FAIL): a hung tool is the gate's reason to exist."""
        assert _selftest_tool_status(None) == "TIMEOUT"

    def test_jsonrpc_error_is_fail(self):
        assert _selftest_tool_status({"error": {"message": "boom"}}) == "FAIL"

    def test_tool_level_error_is_fail(self):
        resp = {"result": {"isError": True, "content": []}}
        assert _selftest_tool_status(resp) == "FAIL"

    def test_degraded_text_is_degraded(self):
        resp = {"result": {"content": [
            {"type": "text", "text": "Aleph is running in degraded mode: ..."}
        ]}}
        assert _selftest_tool_status(resp) == "DEGRADED"

    def test_plain_result_is_ok(self):
        resp = {"result": {"content": [{"type": "text", "text": "S1 greet()"}]}}
        assert _selftest_tool_status(resp) == "OK"
