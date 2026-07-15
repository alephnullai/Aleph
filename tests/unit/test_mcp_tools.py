"""Unit tests for Aleph MCP tools and handlers.

Tests the handler layer directly without starting an MCP server.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from aleph.mcp.handlers import AlephHandlers, _cap_output, MAX_OUTPUT_BYTES
from aleph.mcp.tools import TOOL_DEFINITIONS
from aleph.mcp.server import TOOL_TIERS, get_tool_tier, create_server


# ── Tool definition tests ──


class TestToolDefinitions:
    """Verify tool definitions are well-formed and cover the protocol."""

    def test_all_protocol_commands_have_tools(self):
        """Every ALEPH: command from the protocol has a tool definition."""
        expected_names = {
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
            # Memory + Session
            "aleph_memory_resume", "aleph_session_summary",
            # Patching
            "aleph_patch", "aleph_patch_propose", "aleph_patch_list",
            "aleph_patch_apply", "aleph_patch_reject",
            # Workspace
            "aleph_workspace_search", "aleph_workspace_brief",
            "aleph_workspace_status",
        }
        actual_names = {t["name"] for t in TOOL_DEFINITIONS}
        assert expected_names == actual_names

    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) == 32

    def test_all_tools_have_descriptions(self):
        for tool in TOOL_DEFINITIONS:
            assert "description" in tool, f"{tool['name']} missing description"
            assert len(tool["description"]) > 10, f"{tool['name']} description too short"

    def test_required_params_are_defined(self):
        for tool in TOOL_DEFINITIONS:
            if "required" in tool:
                params = tool.get("parameters", {})
                for req in tool["required"]:
                    assert req in params, (
                        f"{tool['name']}: required param '{req}' not in parameters"
                    )

    def test_all_params_have_type_and_description(self):
        for tool in TOOL_DEFINITIONS:
            for pname, pdef in tool.get("parameters", {}).items():
                assert "type" in pdef, f"{tool['name']}.{pname} missing type"
                assert "description" in pdef, f"{tool['name']}.{pname} missing description"


# ── Handler tests (use a temp directory with mock .aleph files) ──


@pytest.fixture
def aleph_project(tmp_path):
    """Create a minimal .aleph project directory for testing."""
    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()

    # project.aleph.map
    (aleph_dir / "project.aleph.map").write_text(
        "[ALEPH:MAP:1.0]\n"
        "[ROOT:/test/project]\n"
        "[FILES]\n"
        "src/main.py hash=abc123 lang=python syms=5 calls=3 tokens=100->50 reduction=50.0%\n"
        "[/FILES]\n"
    )

    # project.aleph.dict
    (aleph_dir / "project.aleph.dict").write_text(
        "[ALEPH:DICT:1.0]\n"
        "[ROOT:/test/project]\n"
        "[SYMBOLS]\n"
        "f_abc123=main file=src/main.py kind=f scope=module sig=deadbeef\n"
        "f_def456=helper file=src/main.py kind=f scope=module sig=cafebabe\n"
        "t_ghi789=MyClass file=src/main.py kind=t scope=module\n"
        "[/SYMBOLS]\n"
    )

    # project.aleph.struct
    (aleph_dir / "project.aleph.struct").write_text(
        "[ALEPH:STRUCT:PROJECT:1.0]\n"
        "[ROOT:/test/project]\n"
        "[XREFS]\n"
        "f_abc123->f_def456 src=src/main.py dst=src/main.py\n"
        "[/XREFS]\n"
    )

    # project.aleph.salience
    (aleph_dir / "project.aleph.salience").write_text(
        "[ALEPH:SALIENCE:PROJECT:1.0]\n"
        "[ROOT:/test/project]\n"
        "[SCORES]\n"
        "f_abc123 main file=src/main.py score=0.85 local=3 xfile=0 total=3\n"
        "f_def456 helper file=src/main.py score=0.45 local=1 xfile=0 total=1\n"
        "[/SCORES]\n"
    )

    # project.aleph.attention
    (aleph_dir / "project.aleph.attention").write_text(
        "[ALEPH:ATTENTION:1.0]\n"
        "[ROOT:/test/project]\n"
        "[BUDGET]\n"
        "critical=1\nimportant=1\nperipheral=1\n"
        "[/BUDGET]\n"
        "[ENTRIES]\n"
        "f_abc123 critical main file=src/main.py score=0.85\n"
        "[/ENTRIES]\n"
    )

    # project.aleph.temporal
    (aleph_dir / "project.aleph.temporal").write_text(
        "[ALEPH:TEMPORAL:PROJECT:1.0]\n"
        "[PROJECT:/test/project]\n"
        "[COMPUTED:2026-03-17]\n"
        "[HISTORY:sufficient]\n"
        "[SYMBOLS]\n"
        "f_abc123  age=10d  last=2d  churn=low    stability=stable\n"
        "f_def456  age=3d   last=1d  churn=high   stability=volatile\n"
        "[/SYMBOLS]\n"
    )

    # project.aleph.coverage
    (aleph_dir / "project.aleph.coverage").write_text(
        "[ALEPH:COVERAGE:PROJECT:1.0]\n"
        "[ROOT:/test/project]\n"
        "[SUMMARY]\n"
        "symbols_total=3\ncovered=1\npartial=0\nnone=2\n"
        "[/SUMMARY]\n"
        "[UNCOVERED]\n"
        "f_def456 helper file=src/main.py\n"
        "[/UNCOVERED]\n"
    )

    # .aleph.index.json for callers/context
    (aleph_dir / ".aleph.index.json").write_text(json.dumps({
        "files": {
            "src/main.py": {
                "calls": [["f_abc123", "f_def456"]],
                "symbols": [
                    {"id": "f_abc123", "qualified_name": "main", "kind": "f"},
                    {"id": "f_def456", "qualified_name": "helper", "kind": "f"},
                ],
            }
        }
    }))

    return tmp_path


class TestToolTiers:
    """Tier manifest is canonical — drives deferred-tool clients."""

    def test_all_tiers_present(self):
        assert set(TOOL_TIERS.keys()) == {"core", "frequent", "occasional", "rare"}

    def test_tier_counts_per_plan(self):
        # Plan: 5 core + 6 frequent + 8 occasional + 14 rare = 33 total.
        # rare gained aleph_patch (#23) — it was a real served tool missing
        # from the manifest, so tiers now match the README/selftest count.
        assert len(TOOL_TIERS["core"]) == 5
        assert len(TOOL_TIERS["frequent"]) == 6
        assert len(TOOL_TIERS["occasional"]) == 8
        assert len(TOOL_TIERS["rare"]) == 14
        assert sum(len(v) for v in TOOL_TIERS.values()) == 33

    def test_no_tool_in_multiple_tiers(self):
        tiered = set()
        for tools in TOOL_TIERS.values():
            for t in tools:
                assert t not in tiered, f"{t} in multiple tiers"
                tiered.add(t)

    def test_core_tools_are_essentials(self):
        for t in ("aleph_map", "aleph_resolve", "aleph_expand", "aleph_search"):
            assert get_tool_tier(t) == "core"

    def test_get_tool_tier_unknown_returns_none(self):
        assert get_tool_tier("aleph_does_not_exist") is None


class TestHandlersNavigation:
    def test_map(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_map()
        assert "[ALEPH:MAP:1.0]" in result
        assert "src/main.py" in result

    def test_map_not_found(self, tmp_path):
        (tmp_path / ".aleph").mkdir()
        h = AlephHandlers(project_dir=str(tmp_path))
        # Remove the map file
        result = h.handle_map()
        assert "Error" in result

    def test_struct_project(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_struct()
        assert "[XREFS]" in result

    def test_struct_file_fallback(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_struct("src/main.py")
        assert "NOTE" in result or "[XREFS]" in result

    def test_coverage(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_coverage()
        assert "symbols_total=3" in result
        assert "[UNCOVERED]" in result

    def test_bodies_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_bodies("nonexistent.py")
        assert "Error" in result

    def test_errors_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_errors("nonexistent.py")
        assert "Error" in result

    def test_intents_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_intents("nonexistent.py")
        assert "Error" in result

    def test_tests_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_tests("nonexistent.py")
        assert "Error" in result


class TestHandlersResolution:
    def test_resolve(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_resolve("f_abc123")
        assert "main" in result
        assert "f_abc123" in result
        assert "src/main.py" in result

    def test_resolve_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_resolve("f_nonexistent")
        # Honest miss for an unknown id/name (distinct from a real symbol).
        assert "No symbol named" in result or "not found" in result

    def test_search(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_search("main")
        assert "f_abc123" in result
        assert "Matches" in result

    def test_search_no_results(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_search("zzz_nonexistent_xyz")
        # Never a bare empty: a true miss returns actionable guidance.
        assert "No match" in result
        assert "grep" in result or "Nearest" in result

    def test_callers(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_callers("f_def456")
        assert "f_abc123" in result

    def test_callers_empty(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_callers("f_abc123")
        # f_abc123 is a caller, not called by anyone in our fixture
        assert "No callers" in result or "0" in result or "Callers" in result

    def test_context(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_context("f_abc123")
        assert "f_abc123" in result
        assert "main" in result

    def test_context_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_context("f_nonexistent")
        assert "No symbol named" in result or "not found" in result

    def test_expand_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_expand("f_abc123")
        # f_abc123 is a real symbol but the fixture has no bodies file:
        # this is 'no body recorded', distinct from an unknown symbol.
        assert "Error" in result or "No body recorded" in result or result == ""


class TestHandlersPriority:
    def test_attention(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_attention()
        assert "critical=1" in result
        assert "f_abc123" in result

    def test_salience_all(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_salience()
        assert "f_abc123" in result
        assert "score=0.85" in result

    def test_salience_specific(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_salience("f_abc123")
        assert "0.85" in result

    def test_salience_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_salience("f_nonexistent")
        assert "No salience" in result or "not found" in result

    def test_temporal_all(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_temporal()
        assert "f_abc123" in result
        assert "stability=stable" in result

    def test_temporal_specific(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_temporal("f_abc123")
        assert "stable" in result

    def test_temporal_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_temporal("f_nonexistent")
        assert "No temporal" in result or "not found" in result


class TestHandlersEpistemic:
    def test_epistemic_empty(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_epistemic()
        assert "No epistemic state" in result

    def test_infer_and_read(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_infer("f_abc123", "this function is thread-safe", 0.85)
        result = h.handle_epistemic()
        assert "thread-safe" in result
        assert "0.85" in result

    def test_infer_specific_symbol(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_infer("f_abc123", "thread-safe", 0.85)
        h.handle_infer("f_def456", "has race condition", 0.6)
        result = h.handle_epistemic("f_abc123")
        assert "thread-safe" in result
        assert "race condition" not in result

    def test_flag_and_read(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_flag("f_abc123", "error boundary unclear")
        result = h.handle_epistemic()
        assert "error boundary unclear" in result
        assert "Flags" in result

    def test_verify_flag(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_flag("f_abc123", "needs checking")
        h.handle_verify("f_abc123")
        result = h.handle_epistemic()
        assert "VERIFIED" in result

    def test_verify_no_flags(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_verify("f_abc123")
        assert "No unverified" in result


class TestHandlersMemoryResume:
    def test_memory_resume_no_data(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_memory_resume()
        assert "No prior session" in result

    def test_memory_resume_with_inferences(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        # Seed some inferences
        h.handle_infer("f_abc123", "thread-safe under lock", 0.85)
        h.handle_infer("f_def456", "has race condition", 0.6)
        h.handle_flag("f_abc123", "needs lock audit")
        # Write briefing
        from aleph.memory.briefing import generate_briefing, write_briefing
        briefing = generate_briefing(str(aleph_project))
        write_briefing(str(aleph_project), briefing)
        result = h.handle_memory_resume()
        assert "Session Briefing" in result
        assert "f_abc123" in result
        assert "thread-safe" in result


class TestHandlersPatches:
    def test_patch_list_empty(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_patch_list()
        assert "No pending" in result

    def test_patch_create_and_list(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_patch("f_abc123", "change return type to Optional[int]")
        result = h.handle_patch_list()
        assert "f_abc123" in result
        assert "patch_1" in result

    def test_patch_propose_and_list(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_patch_propose("f_abc123", "add null check")
        assert "patch_1" in result
        result = h.handle_patch_list()
        assert "f_abc123" in result
        assert "add null check" in result

    def test_patch_apply_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_patch_apply("patch_999")
        assert "not found" in result.lower()

    def test_patch_reject(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        h.handle_patch_propose("f_abc123", "add null check")
        result = h.handle_patch_reject("patch_1")
        assert "rejected" in result.lower()

    def test_patch_reject_not_found(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_patch_reject("patch_999")
        assert "not found" in result.lower()


class TestOutputCaps:
    """P0: tool responses must never exceed the output cap (live incident:
    a >16MB aleph_attention response force-disconnected the MCP client)."""

    def test_cap_output_under_limit_unchanged(self):
        text = "short line\nanother line"
        assert _cap_output(text) == text

    def test_cap_output_truncates_at_line_boundary(self):
        lines = [f"line_{i:06d} " + "x" * 90 for i in range(2000)]
        text = "\n".join(lines)
        capped = _cap_output(text)
        assert len(capped.encode("utf-8")) < len(text.encode("utf-8"))
        assert "[OUTPUT TRUNCATED at 100KB" in capped
        # Last content line before the marker is complete (line boundary cut)
        content_lines = capped.split("\n")
        assert content_lines[-2] in lines

    def test_server_tool_output_capped(self, aleph_project):
        """Oversized fake artifact through a real registered tool stays under cap."""
        # 3MB fake bodies artifact (mirrors the aleph_map live incident size)
        huge = "[ALEPH:BODIES:1.0]\n" + "\n".join(
            f"[FULL:f_{i:06x}]\n" + "y" * 200 for i in range(15000)
        )
        (aleph_project / ".aleph" / "main.py.aleph.bodies").write_text(huge)
        server = create_server(str(aleph_project))
        tool_fn = server._tool_manager._tools["aleph_bodies"].fn
        # The capped_tool wrapper is async (per-call project resolution);
        # with ctx=None it resolves to the served root and runs the handler.
        result = asyncio.run(tool_fn(file="main.py", ctx=None))
        assert len(result.encode("utf-8")) <= MAX_OUTPUT_BYTES + 200
        assert "[OUTPUT TRUNCATED at 100KB" in result

    def test_search_capped_at_25_with_marker(self, aleph_project):
        # Add 40 matching symbols to the dictionary
        dict_path = aleph_project / ".aleph" / "project.aleph.dict"
        lines = [
            "[ALEPH:DICT:1.0]",
            "[ROOT:/test/project]",
            "[SYMBOLS]",
        ]
        for i in range(40):
            lines.append(
                f"f_aa{i:04x}=helper_{i} file=src/main.py kind=f scope=module"
            )
        lines.append("[/SYMBOLS]")
        dict_path.write_text("\n".join(lines) + "\n")

        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_search("helper")
        entry_lines = [l for l in result.splitlines() if l.strip().startswith("f_")]
        assert len(entry_lines) == 25
        assert "more matches — refine query" in result
        assert "Matches for 'helper': 40" in result

    def test_map_limit_truncates_with_marker(self, aleph_project):
        map_path = aleph_project / ".aleph" / "project.aleph.map"
        lines = ["[ALEPH:MAP:1.0]", "[ROOT:/test/project]", "[FILES]"]
        for i in range(10):
            lines.append(
                f"src/file{i}.py hash=h{i} lang=python syms=2 calls=1 "
                f"tokens=100->50 reduction=50.0%"
            )
        lines.append("[/FILES]")
        map_path.write_text("\n".join(lines) + "\n")

        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_map(limit=3)
        file_lines = [l for l in result.splitlines() if l.startswith("src/")]
        assert len(file_lines) == 3
        assert "[TRUNCATED: showing 3 of 10 files" in result

    def test_map_rollup_for_large_projects(self, aleph_project):
        map_path = aleph_project / ".aleph" / "project.aleph.map"
        lines = ["[ALEPH:MAP:1.0]", "[ROOT:/test/project]", "[FILES]"]
        for i in range(600):
            top = "src" if i % 2 == 0 else "tests"
            lines.append(
                f"{top}/file{i}.py hash=h{i} lang=python syms=3 calls=1 "
                f"tokens=100->40 reduction=60.0%"
            )
        lines.append("[/FILES]")
        map_path.write_text("\n".join(lines) + "\n")

        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_map()
        assert "[ROLLUP: 600 files" in result
        assert "src/ files=300 syms=900" in result
        assert "tests/ files=300" in result
        # path_prefix still drills into full per-file entries
        drill = h.handle_map(path_prefix="tests/", limit=5)
        assert "tests/file1.py" in drill
        assert "[TRUNCATED: showing 5 of 300 files" in drill

    def test_callers_capped_at_50(self, aleph_project):
        # Build an index with 60 callers of one target
        callers = [f"f_ca{i:04x}" for i in range(60)]
        symbols = [{"id": "f_target", "qualified_name": "target", "kind": "f"}]
        symbols += [
            {"id": cid, "qualified_name": f"caller_{i}", "kind": "f"}
            for i, cid in enumerate(callers)
        ]
        (aleph_project / ".aleph" / ".aleph.index.json").write_text(json.dumps({
            "files": {
                "src/main.py": {
                    "calls": [[cid, "f_target"] for cid in callers],
                    "symbols": symbols,
                }
            }
        }))

        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_callers("f_target")
        assert "Callers of f_target: 60" in result
        caller_lines = [l for l in result.splitlines() if l.strip().startswith("f_ca")]
        assert len(caller_lines) == 50
        assert "[TRUNCATED: showing 50 of 60 callers]" in result

    def test_attention_limit_truncates(self, aleph_project):
        attention_path = aleph_project / ".aleph" / "project.aleph.attention"
        lines = [
            "[ALEPH:ATTENTION:1.0]",
            "[ROOT:/test/project]",
            "[BUDGET]",
            "critical=150",
            "[/BUDGET]",
            "[ENTRIES]",
        ]
        for i in range(150):
            lines.append(f"f_e{i:05x} critical sym_{i} file=src/main.py score=0.9")
        lines.append("[/ENTRIES]")
        attention_path.write_text("\n".join(lines) + "\n")

        h = AlephHandlers(project_dir=str(aleph_project))
        result = h.handle_attention()
        assert "[TRUNCATED: showing 100 of 151 entries" in result
        result_big = h.handle_attention(limit=1000)
        assert "TRUNCATED" not in result_big


class TestCoverageIndex:
    """P0: coverage reader must be able to report 'covered' symbols."""

    def _write_coverage(self, aleph_project):
        (aleph_project / ".aleph" / "project.aleph.coverage").write_text(
            "[ALEPH:COVERAGE:PROJECT:1.0]\n"
            "[ROOT:/test/project]\n"
            "[SUMMARY]\n"
            "symbols_total=3\ncovered=1\npartial=0\nnone=2\n"
            "[/SUMMARY]\n"
            "[COVERED]\n"
            "f_abc123 main tests=3 file=src/main.py\n"
            "[/COVERED]\n"
            "[UNCOVERED]\n"
            "f_def456 helper file=src/main.py\n"
            "[/UNCOVERED]\n"
        )

    def test_index_parses_both_sections(self, aleph_project):
        self._write_coverage(aleph_project)
        h = AlephHandlers(project_dir=str(aleph_project))
        index = h._load_coverage_index()
        assert index["f_abc123"] == ("covered", 3)
        assert index["f_def456"] == ("none", 0)

    def test_index_uncovered_only_still_works(self, aleph_project):
        """Backward compat: artifacts without a [COVERED] section parse fine."""
        h = AlephHandlers(project_dir=str(aleph_project))
        index = h._load_coverage_index()
        assert index["f_def456"] == ("none", 0)
        assert "f_abc123" not in index

    def test_impact_renders_covered_bucket(self, aleph_project):
        self._write_coverage(aleph_project)
        h = AlephHandlers(project_dir=str(aleph_project))
        # f_abc123 (covered, tests=3) is the direct caller of f_def456
        result = h.handle_impact("f_def456")
        assert "COVERED (1 — tests will catch regressions)" in result
        assert "tests=3" in result


class TestHandlersArtifactDir:
    def test_resolves_aleph_subdir(self, aleph_project):
        h = AlephHandlers(project_dir=str(aleph_project))
        expected = os.path.join(str(aleph_project), ".aleph")
        assert h._artifact_dir == expected

    def test_falls_back_to_project_dir(self, tmp_path):
        # No .aleph subdir with dict file
        h = AlephHandlers(project_dir=str(tmp_path))
        assert h._artifact_dir == str(tmp_path)
