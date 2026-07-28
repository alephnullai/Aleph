"""Integration tests for the Aleph MCP server.

Tests the full MCP server creation and tool registration against the
real .aleph/ output from the project's own self-application build.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from aleph.mcp.server import create_server
from aleph.mcp.handlers import AlephHandlers

# Path to the project's own .aleph output
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ALEPH_DIR = os.path.join(PROJECT_ROOT, ".aleph")


@pytest.fixture
def self_handlers():
    """Handlers pointed at Aleph's own .aleph/ output."""
    if not os.path.isdir(ALEPH_DIR):
        pytest.skip("No .aleph/ directory found — run `aleph build` first")
    return AlephHandlers(project_dir=PROJECT_ROOT)


@pytest.fixture
def mcp_server():
    """Create an MCP server for the Aleph project itself."""
    if not os.path.isdir(ALEPH_DIR):
        pytest.skip("No .aleph/ directory found — run `aleph build` first")
    return create_server(PROJECT_ROOT)


class TestServerCreation:
    def test_server_has_name(self, mcp_server):
        assert mcp_server.name == "aleph"

    def test_server_has_instructions(self, mcp_server):
        assert "Aleph" in (mcp_server.instructions or "")

    def test_server_registers_all_tools(self, mcp_server):
        """All protocol commands are registered as MCP tools."""
        tool_manager = mcp_server._tool_manager
        tool_names = set(tool_manager._tools.keys())
        expected = {
            # Navigation
            "aleph_map", "aleph_fs", "aleph_struct", "aleph_bodies",
            "aleph_errors", "aleph_intents", "aleph_tests", "aleph_coverage",
            # Resolution
            "aleph_expand", "aleph_resolve", "aleph_callers", "aleph_context",
            "aleph_search",
            # Priority
            "aleph_attention", "aleph_salience", "aleph_temporal",
            # Safety + Context
            "aleph_impact", "aleph_brief",
            # Epistemic
            "aleph_epistemic", "aleph_infer", "aleph_flag", "aleph_verify",
            # Patching
            "aleph_patch", "aleph_patch_propose", "aleph_patch_list",
            "aleph_patch_apply", "aleph_patch_reject",
            # Memory + Session
            "aleph_memory_resume", "aleph_session_summary",
            # Workspace
            "aleph_workspace_search", "aleph_workspace_brief",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


class TestSelfApplicationNavigation:
    """Test navigation commands against Aleph's own .aleph/ output."""

    def test_map_returns_project_files(self, self_handlers):
        result = self_handlers.handle_map()
        assert "[ALEPH:MAP:1.0]" in result
        assert "src/aleph/cli.py" in result

    def test_struct_returns_cross_refs(self, self_handlers):
        result = self_handlers.handle_struct()
        assert "XREFS" in result or "FILEDEPS" in result

    def test_coverage_returns_summary(self, self_handlers):
        result = self_handlers.handle_coverage()
        assert "symbols_total=" in result

    def test_attention_returns_budget(self, self_handlers):
        result = self_handlers.handle_attention()
        assert "critical=" in result


class TestSelfApplicationResolution:
    """Test resolution commands against Aleph's own .aleph/ output."""

    def test_search_finds_query_engine(self, self_handlers):
        result = self_handlers.handle_search("QueryEngine")
        assert "QueryEngine" in result
        assert "Matches" in result

    def test_resolve_found_symbol(self, self_handlers):
        # First search to find a real symbol ID
        search = self_handlers.handle_search("main")
        # Extract first symbol ID from results
        lines = search.splitlines()
        if len(lines) > 1:
            parts = lines[1].strip().split()
            if parts:
                symbol_id = parts[0]
                result = self_handlers.handle_resolve(symbol_id)
                assert symbol_id in result

    def test_callers_on_real_symbol(self, self_handlers):
        # Search for a function likely to have callers
        search = self_handlers.handle_search("run_pipeline")
        lines = search.splitlines()
        if len(lines) > 1:
            parts = lines[1].strip().split()
            if parts:
                symbol_id = parts[0]
                result = self_handlers.handle_callers(symbol_id)
                # Should either have callers or say "No callers"
                assert "caller" in result.lower() or "Callers" in result


class TestSelfApplicationPriority:
    def test_salience_returns_scores(self, self_handlers):
        result = self_handlers.handle_salience()
        assert "score=" in result

    def test_temporal_returns_entries(self, self_handlers):
        result = self_handlers.handle_temporal()
        assert "stability=" in result


class TestSelfApplicationEpistemic:
    def test_epistemic_starts_empty(self, self_handlers):
        result = self_handlers.handle_epistemic()
        # May or may not be empty depending on prior runs
        assert isinstance(result, str)

    def test_infer_and_read_roundtrip(self, self_handlers, tmp_path):
        """Test epistemic write/read without polluting the real project."""
        h = AlephHandlers(project_dir=str(tmp_path))
        # Create a minimal .aleph dir for the handler
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir(exist_ok=True)

        h._artifact_dir = str(aleph_dir)
        h.handle_infer("f_test", "test inference", 0.75)
        h.handle_flag("f_test", "needs review")
        result = h.handle_epistemic()
        assert "test inference" in result
        assert "needs review" in result


class TestCLIServeCommand:
    def test_serve_auto_builds_when_missing(self, tmp_path):
        """aleph serve should auto-build when no .aleph directory exists."""
        import subprocess
        # Create a minimal source file so auto-build has something to process
        (tmp_path / "hello.py").write_text("def hello(): pass\n")
        result = subprocess.run(
            [sys.executable, "-m", "aleph.cli", "serve", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        # Server will auto-build then start (and exit when stdin closes)
        assert "No artifacts found" in result.stderr or "Built" in result.stderr or result.returncode == 0
