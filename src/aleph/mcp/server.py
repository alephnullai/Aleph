"""Aleph MCP server — exposes the full ALEPH: interaction protocol over MCP.

Usage:
    aleph serve [project_dir]

Starts an MCP server (stdio transport) that lets LLMs navigate a built
Aleph project using the protocol commands from CONSUMER_GUIDE.md.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

from aleph.mcp.handlers import AlephHandlers

# Module-level state set by create_server() or serve().
_handlers: AlephHandlers | None = None

SYSTEM_INSTRUCTIONS = """\
You are working with Aleph-encoded information. Aleph is a semantic \
compression format designed for LLMs. Use the ALEPH: tools to navigate \
the codebase — start with aleph_map, then aleph_attention, then drill \
into specific symbols with aleph_resolve / aleph_expand. Load \
aleph_epistemic first when resuming a prior session.\
"""


# Tool tier manifest — for deferred-tool clients (Claude Code, etc.)
# CORE: always loaded (essential navigation: map, attention, resolve, expand, search)
# FREQUENT: most code-reading sessions
# OCCASIONAL: analysis / inspection
# RARE: patches, workspace, agent-side annotations
#
# Schema-stable: canonical reference for tier-aware MCP clients.
# Expose via `aleph mcp tiers`. See Aleph Null launch plan: 2026-07-29.
TOOL_TIERS: dict[str, tuple[str, ...]] = {
    "core": (
        "aleph_map",
        "aleph_attention",
        "aleph_resolve",
        "aleph_expand",
        "aleph_search",
    ),
    "frequent": (
        "aleph_brief",
        "aleph_struct",
        "aleph_bodies",
        "aleph_callers",
        "aleph_context",
        "aleph_salience",
    ),
    "occasional": (
        "aleph_coverage",
        "aleph_errors",
        "aleph_tests",
        "aleph_temporal",
        "aleph_impact",
        "aleph_fs",
        "aleph_intents",
        "aleph_epistemic",
    ),
    "rare": (
        "aleph_patch_propose",
        "aleph_patch_apply",
        "aleph_patch_list",
        "aleph_patch_reject",
        "aleph_infer",
        "aleph_flag",
        "aleph_verify",
        "aleph_memory_resume",
        "aleph_session_summary",
        "aleph_workspace_search",
        "aleph_workspace_brief",
        "aleph_rebuild",
    ),
}


def get_tool_tier(tool_name: str) -> str | None:
    """Return the tier for a given Aleph tool name, or None if unknown."""
    for tier, tools in TOOL_TIERS.items():
        if tool_name in tools:
            return tier
    return None


def create_server(project_dir: str) -> FastMCP:
    """Create and configure an MCP server for a built Aleph project."""
    global _handlers

    project_dir = os.path.abspath(project_dir)
    agent_id = os.environ.get("ALEPH_AGENT_ID", "default")
    _handlers = AlephHandlers(project_dir=project_dir, agent_id=agent_id)

    mcp = FastMCP(
        name="aleph",
        instructions=SYSTEM_INSTRUCTIONS,
    )

    # ── Navigation tools ──

    @mcp.tool(
        name="aleph_map",
        description=(
            "ALEPH:MAP — Project manifest and component list. "
            "Start here always. Returns all files, semantic hashes, "
            "and token statistics."
        ),
    )
    def aleph_map(path_prefix: str = "") -> str:
        return _handlers.handle_map(path_prefix or None)

    @mcp.tool(
        name="aleph_fs",
        description=(
            "ALEPH:FS — Filesystem layout and module boundaries. "
            "Shows all source files with language, symbol count, and directory structure."
        ),
    )
    def aleph_fs() -> str:
        return _handlers.handle_fs()

    @mcp.tool(
        name="aleph_struct",
        description=(
            "ALEPH:STRUCT — Call graph, signatures, hierarchy. "
            "Project-level or file-level structure."
        ),
    )
    def aleph_struct(file: str = "") -> str:
        return _handlers.handle_struct(file or None)

    @mcp.tool(
        name="aleph_bodies",
        description=(
            "ALEPH:BODIES — Compressed function bodies for a file."
        ),
    )
    def aleph_bodies(file: str) -> str:
        return _handlers.handle_bodies(file)

    @mcp.tool(
        name="aleph_errors",
        description=(
            "ALEPH:ERRORS — Error flow layer for a file."
        ),
    )
    def aleph_errors(file: str) -> str:
        return _handlers.handle_errors(file)

    @mcp.tool(
        name="aleph_intents",
        description=(
            "ALEPH:INTENTS — Intent and invariant annotations for a file."
        ),
    )
    def aleph_intents(file: str) -> str:
        return _handlers.handle_intents(file)

    @mcp.tool(
        name="aleph_tests",
        description=(
            "ALEPH:TESTS — Test coverage map for a file."
        ),
    )
    def aleph_tests(file: str) -> str:
        return _handlers.handle_tests(file)

    @mcp.tool(
        name="aleph_coverage",
        description=(
            "ALEPH:COVERAGE — Project-wide test coverage and high-risk gaps."
        ),
    )
    def aleph_coverage() -> str:
        return _handlers.handle_coverage()

    # ── Resolution tools ──

    @mcp.tool(
        name="aleph_expand",
        description=(
            "ALEPH:EXPAND — Full body of a symbol by ID."
        ),
    )
    def aleph_expand(symbol_id: str) -> str:
        return _handlers.handle_expand(symbol_id)

    @mcp.tool(
        name="aleph_resolve",
        description=(
            "ALEPH:RESOLVE — Dictionary entry: name, kind, file, signature."
        ),
    )
    def aleph_resolve(symbol_id: str) -> str:
        return _handlers.handle_resolve(symbol_id)

    @mcp.tool(
        name="aleph_callers",
        description=(
            "ALEPH:CALLERS — All symbols that call or reference this one."
        ),
    )
    def aleph_callers(symbol_id: str) -> str:
        return _handlers.handle_callers(symbol_id)

    @mcp.tool(
        name="aleph_context",
        description=(
            "ALEPH:CONTEXT — Symbol plus immediate call neighborhood."
        ),
    )
    def aleph_context(symbol_id: str) -> str:
        return _handlers.handle_context(symbol_id)

    @mcp.tool(
        name="aleph_search",
        description=(
            "ALEPH:SEARCH — Symbols matching a name or intent."
        ),
    )
    def aleph_search(term: str) -> str:
        return _handlers.handle_search(term)

    # ── Priority tools ──

    @mcp.tool(
        name="aleph_attention",
        description=(
            "ALEPH:ATTENTION — Recommended load order and attention budget."
        ),
    )
    def aleph_attention() -> str:
        return _handlers.handle_attention()

    @mcp.tool(
        name="aleph_salience",
        description=(
            "ALEPH:SALIENCE — How load-bearing a symbol is (0-1)."
        ),
    )
    def aleph_salience(symbol_id: str = "") -> str:
        return _handlers.handle_salience(symbol_id or None)

    @mcp.tool(
        name="aleph_temporal",
        description=(
            "ALEPH:TEMPORAL — Age, churn rate, stability class."
        ),
    )
    def aleph_temporal(symbol_id: str = "") -> str:
        return _handlers.handle_temporal(symbol_id or None)

    # ── Epistemic tools ──

    @mcp.tool(
        name="aleph_epistemic",
        description=(
            "ALEPH:EPISTEMIC — Cached inferences and flags (your prior state)."
        ),
    )
    def aleph_epistemic(symbol_id: str = "") -> str:
        return _handlers.handle_epistemic(symbol_id or None)

    @mcp.tool(
        name="aleph_infer",
        description=(
            "ALEPH:INFER — Record a conclusion about a symbol with confidence."
        ),
    )
    def aleph_infer(symbol_id: str, conclusion: str, confidence: float) -> str:
        return _handlers.handle_infer(symbol_id, conclusion, confidence)

    @mcp.tool(
        name="aleph_flag",
        description=(
            "ALEPH:FLAG — Flag a symbol as uncertain or needing verification."
        ),
    )
    def aleph_flag(symbol_id: str, reason: str) -> str:
        return _handlers.handle_flag(symbol_id, reason)

    @mcp.tool(
        name="aleph_verify",
        description=(
            "ALEPH:VERIFY — Mark a flagged symbol as verified."
        ),
    )
    def aleph_verify(symbol_id: str) -> str:
        return _handlers.handle_verify(symbol_id)

    # ── Memory resume ──

    @mcp.tool(
        name="aleph_memory_resume",
        description=(
            "ALEPH:MEMORY:RESUME — Session briefing with top inferences, "
            "flags, pending patches, and key decisions/learnings. "
            "Use this to resume a prior session."
        ),
    )
    def aleph_memory_resume() -> str:
        return _handlers.handle_memory_resume()

    # ── Patch tools ──

    @mcp.tool(
        name="aleph_patch_propose",
        description=(
            "ALEPH:PATCH — Propose a semantic change to a symbol."
        ),
    )
    def aleph_patch_propose(symbol_id: str, intent: str, file: str = "") -> str:
        return _handlers.handle_patch_propose(symbol_id, intent, file=file or None)

    @mcp.tool(
        name="aleph_patch",
        description=(
            "ALEPH:PATCH — Propose a semantic change (alias for patch_propose)."
        ),
    )
    def aleph_patch(symbol_id: str, patch_body: str) -> str:
        return _handlers.handle_patch(symbol_id, patch_body)

    @mcp.tool(
        name="aleph_patch_list",
        description=(
            "ALEPH:PATCH:LIST — List pending semantic patches."
        ),
    )
    def aleph_patch_list() -> str:
        return _handlers.handle_patch_list()

    @mcp.tool(
        name="aleph_patch_apply",
        description=(
            "ALEPH:PATCH:APPLY — Apply a semantic patch to the source file."
        ),
    )
    def aleph_patch_apply(patch_id: str, force: bool = False) -> str:
        return _handlers.handle_patch_apply(patch_id, force=force)

    @mcp.tool(
        name="aleph_patch_reject",
        description=(
            "ALEPH:PATCH:REJECT — Mark a pending patch as rejected."
        ),
    )
    def aleph_patch_reject(patch_id: str) -> str:
        return _handlers.handle_patch_reject(patch_id)

    @mcp.tool(
        name="aleph_impact",
        description=(
            "ALEPH:IMPACT — Pre-modification change impact analysis. "
            "Shows direct callers, transitive impact (2 hops), risk "
            "assessment (untested high-salience callers = DANGER), "
            "and suggested test targets. Use BEFORE modifying any symbol."
        ),
    )
    def aleph_impact(symbol_id: str) -> str:
        return _handlers.handle_impact(symbol_id)

    @mcp.tool(
        name="aleph_session_summary",
        description=(
            "ALEPH:SESSION — Summarize this session's tool queries and save "
            "a review trail to the epistemic layer. Records which symbols "
            "were examined without requiring explicit ALEPH:INFER calls. "
            "Call at session end."
        ),
    )
    def aleph_session_summary() -> str:
        return _handlers.handle_session_summary()

    @mcp.tool(
        name="aleph_brief",
        description=(
            "ALEPH:BRIEF — Task-aware context optimizer. Describe your task "
            "in natural language and get a curated briefing: relevant symbols "
            "ranked by salience, call graph, impact risk, temporal warnings, "
            "prior knowledge, and recommended next steps. "
            "Use this FIRST when starting any task."
        ),
    )
    def aleph_brief(task: str) -> str:
        return _handlers.handle_brief(task)

    @mcp.tool(
        name="aleph_workspace_search",
        description=(
            "ALEPH:WORKSPACE:SEARCH — Search across all projects in the workspace. "
            "Requires .aleph-workspace.json in the project root. "
            "Returns results tagged by project name."
        ),
    )
    def aleph_workspace_search(term: str) -> str:
        return _handlers.handle_workspace_search(term)

    @mcp.tool(
        name="aleph_workspace_brief",
        description=(
            "ALEPH:WORKSPACE:BRIEF — Task-aware briefing across all workspace projects. "
            "Shows relevant symbols from each project, cross-project connections "
            "(shared symbol names), and recommended next steps."
        ),
    )
    def aleph_workspace_brief(task: str) -> str:
        return _handlers.handle_workspace_brief(task)

    @mcp.tool(
        name="aleph_rebuild",
        description=(
            "ALEPH:REBUILD — Force a full rebuild of all artifacts. "
            "Use when artifacts seem stale or after major code changes."
        ),
    )
    def aleph_rebuild() -> str:
        return _handlers.handle_rebuild()

    return mcp


def _start_auto_rebuild(project_dir: str) -> None:
    """Start a background thread that watches for file changes and rebuilds.

    Polls source files every 3 seconds. When changes are detected, runs an
    incremental rebuild and invalidates the handler cache so the next MCP
    tool call serves fresh data.

    Disabled by setting ALEPH_AUTO_REBUILD=false.
    """
    if os.environ.get("ALEPH_AUTO_REBUILD", "").lower() in ("false", "0", "no"):
        return

    import threading
    import time
    import sys

    def watch_loop():
        from aleph.project.indexer import discover_source_files
        from aleph.project.cache import FileStamp

        # Build initial stamps
        stamps: dict[str, FileStamp] = {}
        try:
            for f in discover_source_files(project_dir):
                try:
                    stamps[f] = FileStamp.from_file(f)
                except OSError:
                    pass
        except Exception:
            return  # Can't discover files — skip watching

        while True:
            time.sleep(3)
            try:
                current_files = set(discover_source_files(project_dir))
                prev_files = set(stamps.keys())

                changed = False
                for f in current_files & prev_files:
                    try:
                        current = FileStamp.from_file(f)
                        if not stamps[f].matches(current):
                            changed = True
                            stamps[f] = current
                    except OSError:
                        pass

                if current_files != prev_files:
                    changed = True
                    for f in current_files - prev_files:
                        try:
                            stamps[f] = FileStamp.from_file(f)
                        except OSError:
                            pass
                    for f in prev_files - current_files:
                        stamps.pop(f, None)

                if changed:
                    print("[aleph] Changes detected — rebuilding...", file=sys.stderr)
                    try:
                        # Import here to avoid circular imports
                        from aleph.cli import _auto_build
                        _auto_build(project_dir)
                        # Invalidate handler cache
                        global _handlers
                        if _handlers is not None:
                            _handlers._engine = None
                        print("[aleph] Rebuild complete.", file=sys.stderr)
                    except Exception as e:
                        print(f"[aleph] Auto-rebuild failed: {e}", file=sys.stderr)
            except Exception:
                pass  # Never crash the watch thread

    thread = threading.Thread(target=watch_loop, daemon=True)
    thread.start()


def serve(project_dir: str = ".") -> None:
    """Entry point for `aleph serve`. Creates server and runs on stdio.

    Automatically watches for file changes and rebuilds in the background.
    Disable with ALEPH_AUTO_REBUILD=false.
    """
    _start_auto_rebuild(project_dir)
    mcp_server = create_server(project_dir)
    mcp_server.run(transport="stdio")
