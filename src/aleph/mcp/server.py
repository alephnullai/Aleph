"""Aleph MCP server — exposes the full ALEPH: interaction protocol over MCP.

Usage:
    aleph serve [project_dir]

Starts an MCP server (stdio transport) that lets LLMs navigate a built
Aleph project using the protocol commands from CONSUMER_GUIDE.md.
"""

from __future__ import annotations

import contextvars
import functools
import os
import sys
import threading

from mcp.server.fastmcp import Context, FastMCP

from aleph.mcp.handlers import AlephHandlers, _cap_output
from aleph.mcp.project_router import (
    HandlerCache,
    fetch_client_roots,
    has_index,
    no_index_message,
    resolve_project_dir,
)

# Module-level state set by create_server() or serve().
# ``_handlers`` is the SERVED-ROOT handler set — the historical single
# project binding and the fallback when per-call resolution finds nothing
# better. Per-call resolution (P0 adoption fix #1) may override it for the
# duration of one tool call via the contextvar below; the deferred-startup
# thread still swaps this global so the served-root fallback stays fresh.
_handlers: AlephHandlers | None = None

# The directory `aleph serve` was launched against. The resolution fallback
# and the actionable no-index error reference it.
_served_root: str = ""

# Bounded LRU of per-project handlers, keyed by resolved project dir, so a
# session that hops among indexed repos loads each index once and never
# rebuilds on switch. Created in create_server().
_handler_cache: HandlerCache | None = None

# Per-tool-call handler override. The capped_tool wrapper resolves the
# active project from the client's workspace roots and binds the matching
# handlers here for the duration of one call; the tool closures read it via
# _active_handlers(). A contextvar (not the bare global) so concurrent
# async tool calls can't clobber each other's resolved project.
_active_handlers_var: contextvars.ContextVar[AlephHandlers | None] = (
    contextvars.ContextVar("aleph_active_handlers", default=None)
)


def _active_handlers() -> AlephHandlers:
    """Return the handlers a tool call should use.

    Prefers the per-call override set by the resolving wrapper (the repo the
    client is actually working in); falls back to the served-root handlers
    (unchanged single-project behaviour). Raising here only happens if a
    tool runs entirely outside both, which the wrapper prevents.
    """
    override = _active_handlers_var.get()
    if override is not None:
        return override
    return _handlers


def _served_handlers() -> AlephHandlers:
    """The served-root handler set, ignoring any per-call override.

    Workspace tools (search/status/brief) are scoped to the served root's
    ``.aleph-workspace.json`` and span every project in it, so they must
    NOT be redirected to a single per-call resolved sub-project.
    """
    return _handlers


def _expose_context_param(wrapper, fn) -> None:
    """Make FastMCP inject a Context into ``wrapper`` without schema leakage.

    The capped_tool wrapper is wrapped with ``functools.wraps(fn)``, which
    copies ``fn``'s ``__annotations__`` and signature — hiding the wrapper's
    own ``ctx`` parameter from FastMCP's Context detection (it inspects
    ``typing.get_type_hints``/the signature). We therefore re-attach a
    keyword-only ``ctx: Context`` to the wrapper's annotations and signature.
    FastMCP then injects the per-call client session as ``ctx`` AND excludes
    that resolved context kwarg from the public tool schema, so the tool's
    advertised parameters are exactly ``fn``'s.
    """
    import inspect

    ann = dict(getattr(fn, "__annotations__", {}))
    ann["ctx"] = Context
    wrapper.__annotations__ = ann

    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    params.append(
        inspect.Parameter(
            "ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context, default=None
        )
    )
    wrapper.__signature__ = sig.replace(parameters=params)


# The deferred-startup worker started by serve() (None until then).
# Exposed so tests and diagnostics can join it deterministically.
_startup_thread: threading.Thread | None = None

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
        "aleph_patch",
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
        "aleph_workspace_status",
        "aleph_rebuild",
    ),
}


def get_tool_tier(tool_name: str) -> str | None:
    """Return the tier for a given Aleph tool name, or None if unknown."""
    for tier, tools in TOOL_TIERS.items():
        if tool_name in tools:
            return tier
    return None


def create_server(project_dir: str, degraded_message: str | None = None) -> FastMCP:
    """Create and configure an MCP server for a built Aleph project.

    When ``degraded_message`` is set, the server still completes the MCP
    handshake and registers every tool, but each tool call returns that
    message instead of running its handler. Used when the target directory
    cannot be served as a single project (e.g. a multi-repo parent), so the
    client gets actionable instructions rather than a transport crash.
    """
    global _handlers, _served_root, _handler_cache

    project_dir = os.path.abspath(project_dir)
    agent_id = os.environ.get("ALEPH_AGENT_ID", "default")
    _handlers = AlephHandlers(project_dir=project_dir, agent_id=agent_id)
    _served_root = project_dir
    _handler_cache = HandlerCache(agent_id=agent_id)

    mcp = FastMCP(
        name="aleph",
        instructions=SYSTEM_INSTRUCTIONS,
    )
    # Report Aleph's own version in the MCP handshake. FastMCP takes no
    # version arg and leaves the lowlevel server's version None, so it falls
    # back to importlib.metadata.version("mcp") — which surfaced as the SDK's
    # 1.28.1 instead of ours (issue #27). Set it on the lowlevel server.
    from aleph.__version__ import __version__ as _aleph_version
    mcp._mcp_server.version = _aleph_version

    def capped_tool(name: str, description: str):
        """Register an MCP tool whose response passes the shared output cap.

        Single guard point: every tool response is truncated at 100KB
        (line-boundary) so an oversized artifact can never flood the
        client transport. In degraded mode every tool short-circuits to
        the degraded message.

        PER-CALL PROJECT RESOLUTION (P0 adoption fix #1): the wrapper is
        async and requests a Context. On each call it asks the MCP client
        for its workspace roots and binds the handlers for the repo the
        client is actually working in (cached, lazy) for the duration of
        that call — so a single globally-configured `aleph serve .` follows
        the agent across indexed repos instead of being pinned to its
        launch dir. All of this runs AFTER the handshake (tool handlers are
        nested fns the purity guard skips), so the responsiveness contract
        is untouched. Roots resolution is capability-guarded, timeout-bound,
        and fail-soft: with no roots / an unindexed target it falls back to
        the served root, preserving single-project and workspace behaviour.
        """
        def decorator(fn):
            @functools.wraps(fn)
            async def wrapper(*args, ctx: Context | None = None, **kwargs):
                if degraded_message is not None:
                    return _cap_output(degraded_message)

                target = kwargs.get("file") or kwargs.get("path_prefix") or None
                resolved = await _resolve_call_handlers(ctx, target)
                if isinstance(resolved, str):  # actionable no-index error
                    return _cap_output(resolved)
                token = _active_handlers_var.set(resolved)
                try:
                    return _cap_output(fn(*args, **kwargs))
                finally:
                    _active_handlers_var.reset(token)

            # functools.wraps copied fn's signature/annotations onto the
            # wrapper, which hides our injected `ctx` from FastMCP's Context
            # detection (it reads __annotations__/signature). Re-expose ctx as
            # a keyword-only Context param so FastMCP INJECTS the client
            # session per call, while keeping it OUT of the public tool schema
            # (FastMCP excludes the resolved context_kwarg from the schema).
            _expose_context_param(wrapper, fn)
            return mcp.tool(name=name, description=description)(wrapper)
        return decorator

    async def _resolve_call_handlers(ctx, target):
        """Resolve the handlers for this call, or an actionable error string.

        Returns an :class:`AlephHandlers` to use, or a ``str`` error message
        when the resolved project has no reachable index (so the caller can
        surface a "run `aleph build` in <repo>" instead of a bare failure).
        """
        client_roots = await fetch_client_roots(ctx)
        # No roots and no path target → unchanged single-project behaviour:
        # use the served-root handlers (kept fresh by deferred startup).
        if not client_roots and not target:
            return _handlers if _handlers is not None else _handler_cache.get(_served_root)

        chosen = resolve_project_dir(_served_root, client_roots, target)
        # The served root is the historical fallback; its handlers (which the
        # deferred-startup thread may have rebuilt) take precedence so an
        # in-flight auto-build is reflected.
        if os.path.abspath(chosen) == os.path.abspath(_served_root) and _handlers is not None:
            return _handlers
        if not has_index(chosen):
            return no_index_message(chosen, _served_root)
        return _handler_cache.get(chosen)

    # ── Navigation tools ──

    @capped_tool(
        name="aleph_map",
        description=(
            "ALEPH:MAP — Project manifest and component list. "
            "Start here always. Returns files, semantic hashes, "
            "and token statistics. Shows at most `limit` files "
            "(default 200, with a truncation note); projects over 500 files "
            "return a directory-level rollup unless path_prefix is given."
        ),
    )
    def aleph_map(path_prefix: str = "", limit: int = 200) -> str:
        return _active_handlers().handle_map(path_prefix or None, limit=limit)

    @capped_tool(
        name="aleph_fs",
        description=(
            "ALEPH:FS — Filesystem layout and module boundaries. "
            "Shows source files with language, symbol count, and directory "
            "structure. Truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_fs(limit: int = 100) -> str:
        return _active_handlers().handle_fs(limit=limit)

    @capped_tool(
        name="aleph_struct",
        description=(
            "ALEPH:STRUCT — Call graph, signatures, hierarchy. "
            "Project-level or file-level structure. "
            "Truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_struct(file: str = "", limit: int = 100) -> str:
        return _active_handlers().handle_struct(file or None, limit=limit)

    @capped_tool(
        name="aleph_bodies",
        description=(
            "ALEPH:BODIES — Compressed function bodies for a file."
        ),
    )
    def aleph_bodies(file: str) -> str:
        return _active_handlers().handle_bodies(file)

    @capped_tool(
        name="aleph_errors",
        description=(
            "ALEPH:ERRORS — Error flow layer for a file."
        ),
    )
    def aleph_errors(file: str) -> str:
        return _active_handlers().handle_errors(file)

    @capped_tool(
        name="aleph_intents",
        description=(
            "ALEPH:INTENTS — Intent and invariant annotations for a file."
        ),
    )
    def aleph_intents(file: str) -> str:
        return _active_handlers().handle_intents(file)

    @capped_tool(
        name="aleph_tests",
        description=(
            "ALEPH:TESTS — Test coverage map for a file."
        ),
    )
    def aleph_tests(file: str) -> str:
        return _active_handlers().handle_tests(file)

    @capped_tool(
        name="aleph_coverage",
        description=(
            "ALEPH:COVERAGE — Project-wide test coverage and high-risk gaps. "
            "Truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_coverage(limit: int = 100) -> str:
        return _active_handlers().handle_coverage(limit=limit)

    # ── Resolution tools ──

    @capped_tool(
        name="aleph_expand",
        description=(
            "ALEPH:EXPAND — Full body of a symbol by ID."
        ),
    )
    def aleph_expand(symbol_id: str) -> str:
        return _active_handlers().handle_expand(symbol_id)

    @capped_tool(
        name="aleph_resolve",
        description=(
            "ALEPH:RESOLVE — Dictionary entry: name, kind, file, signature."
        ),
    )
    def aleph_resolve(symbol_id: str) -> str:
        return _active_handlers().handle_resolve(symbol_id)

    @capped_tool(
        name="aleph_callers",
        description=(
            "ALEPH:CALLERS — All symbols that call or reference this one."
        ),
    )
    def aleph_callers(symbol_id: str) -> str:
        return _active_handlers().handle_callers(symbol_id)

    @capped_tool(
        name="aleph_context",
        description=(
            "ALEPH:CONTEXT — Symbol plus immediate call neighborhood."
        ),
    )
    def aleph_context(symbol_id: str) -> str:
        return _active_handlers().handle_context(symbol_id)

    @capped_tool(
        name="aleph_search",
        description=(
            "ALEPH:SEARCH — Symbol search: lexical + optional semantic "
            "(when built with --semantic). Lexical matching covers symbol "
            "names, qualified names, and file path components (camelCase/"
            "snake_case subtokens, ranked by match quality and token "
            "rarity, exact > prefix > subtoken). Natural-language queries "
            "additionally rank by embedding similarity and are fused with "
            "the lexical ranking when the project was built with `aleph "
            "build --semantic` and the fastembed extra is installed; "
            "identifier-shaped queries always keep pure lexical ranking. "
            "Returns at most `limit` results (default 25) with a note when "
            "more matches exist — refine the query to narrow."
        ),
    )
    def aleph_search(term: str, limit: int = 25) -> str:
        return _active_handlers().handle_search(term, limit=limit)

    # ── Priority tools ──

    @capped_tool(
        name="aleph_attention",
        description=(
            "ALEPH:ATTENTION — Recommended load order and attention budget. "
            "Truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_attention(limit: int = 100) -> str:
        return _active_handlers().handle_attention(limit=limit)

    @capped_tool(
        name="aleph_salience",
        description=(
            "ALEPH:SALIENCE — How load-bearing a symbol is (0-1). "
            "Without a symbol_id, truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_salience(symbol_id: str = "", limit: int = 100) -> str:
        return _active_handlers().handle_salience(symbol_id or None, limit=limit)

    @capped_tool(
        name="aleph_temporal",
        description=(
            "ALEPH:TEMPORAL — Age, churn rate, stability class. "
            "Without a symbol_id, truncated at `limit` entries (default 100)."
        ),
    )
    def aleph_temporal(symbol_id: str = "", limit: int = 100) -> str:
        return _active_handlers().handle_temporal(symbol_id or None, limit=limit)

    # ── Epistemic tools ──

    @capped_tool(
        name="aleph_epistemic",
        description=(
            "ALEPH:EPISTEMIC — Cached inferences and flags (your prior state)."
        ),
    )
    def aleph_epistemic(symbol_id: str = "") -> str:
        return _active_handlers().handle_epistemic(symbol_id or None)

    @capped_tool(
        name="aleph_infer",
        description=(
            "ALEPH:INFER — Record a conclusion about a symbol with confidence."
        ),
    )
    def aleph_infer(symbol_id: str, conclusion: str, confidence: float) -> str:
        return _active_handlers().handle_infer(symbol_id, conclusion, confidence)

    @capped_tool(
        name="aleph_flag",
        description=(
            "ALEPH:FLAG — Flag a symbol as uncertain or needing verification."
        ),
    )
    def aleph_flag(symbol_id: str, reason: str) -> str:
        return _active_handlers().handle_flag(symbol_id, reason)

    @capped_tool(
        name="aleph_verify",
        description=(
            "ALEPH:VERIFY — Mark a flagged symbol as verified."
        ),
    )
    def aleph_verify(symbol_id: str) -> str:
        return _active_handlers().handle_verify(symbol_id)

    # ── Memory resume ──

    @capped_tool(
        name="aleph_memory_resume",
        description=(
            "ALEPH:MEMORY:RESUME — Session briefing with top inferences, "
            "flags, pending patches, and key decisions/learnings. "
            "Use this to resume a prior session."
        ),
    )
    def aleph_memory_resume() -> str:
        return _active_handlers().handle_memory_resume()

    # ── Patch tools ──

    @capped_tool(
        name="aleph_patch_propose",
        description=(
            "ALEPH:PATCH — Propose a semantic change to a symbol. "
            "Propose records any language, but aleph_patch_apply currently "
            "supports Python targets only."
        ),
    )
    def aleph_patch_propose(symbol_id: str, intent: str, file: str = "") -> str:
        return _active_handlers().handle_patch_propose(symbol_id, intent, file=file or None)

    @capped_tool(
        name="aleph_patch",
        description=(
            "ALEPH:PATCH — Propose a semantic change (alias for patch_propose)."
        ),
    )
    def aleph_patch(symbol_id: str, patch_body: str) -> str:
        return _active_handlers().handle_patch(symbol_id, patch_body)

    @capped_tool(
        name="aleph_patch_list",
        description=(
            "ALEPH:PATCH:LIST — List pending semantic patches."
        ),
    )
    def aleph_patch_list() -> str:
        return _active_handlers().handle_patch_list()

    @capped_tool(
        name="aleph_patch_apply",
        description=(
            "ALEPH:PATCH:APPLY — Apply a semantic patch to the source file. "
            "Python only: the target is located by the symbol's recorded "
            "span (file + line range from the dictionary); duplicate names "
            "are disambiguated by qualified name, and unresolved ambiguity "
            "errors out listing the candidates. Non-Python targets return "
            "a clear unsupported-language error."
        ),
    )
    def aleph_patch_apply(patch_id: str, force: bool = False) -> str:
        return _active_handlers().handle_patch_apply(patch_id, force=force)

    @capped_tool(
        name="aleph_patch_reject",
        description=(
            "ALEPH:PATCH:REJECT — Mark a pending patch as rejected."
        ),
    )
    def aleph_patch_reject(patch_id: str) -> str:
        return _active_handlers().handle_patch_reject(patch_id)

    @capped_tool(
        name="aleph_impact",
        description=(
            "ALEPH:IMPACT — Pre-modification change impact analysis. "
            "Shows direct callers, transitive impact (2 hops), risk "
            "assessment (untested high-salience callers = DANGER), "
            "and suggested test targets. Use BEFORE modifying any symbol."
        ),
    )
    def aleph_impact(symbol_id: str) -> str:
        return _active_handlers().handle_impact(symbol_id)

    @capped_tool(
        name="aleph_session_summary",
        description=(
            "ALEPH:SESSION — Summarize this session's tool queries and save "
            "a review trail to the epistemic layer. Records which symbols "
            "were examined without requiring explicit ALEPH:INFER calls. "
            "Call at session end."
        ),
    )
    def aleph_session_summary() -> str:
        return _active_handlers().handle_session_summary()

    @capped_tool(
        name="aleph_brief",
        description=(
            "ALEPH:BRIEF — Task briefing built from symbol search "
            "(lexical + optional semantic, when built with --semantic) "
            "blended with structural salience (call-graph fan-in). "
            "Works best when the task mentions identifier-like terms "
            "(function/class/module names); natural-language tasks also "
            "match by embedding similarity when the semantic index exists. "
            "Returns matching symbols with call context, impact risk, "
            "temporal warnings, prior knowledge, and next steps — or an "
            "explicit no-confident-match message. "
            "Use this FIRST when starting any task."
        ),
    )
    def aleph_brief(task: str) -> str:
        return _active_handlers().handle_brief(task)

    @capped_tool(
        name="aleph_workspace_search",
        description=(
            "ALEPH:WORKSPACE:SEARCH — Search across all projects in the workspace. "
            "Requires .aleph-workspace.json in the project root. "
            "Returns at most `limit` results (default 25) tagged by project "
            "name, plus per-project warnings for missing/corrupt artifacts."
        ),
    )
    def aleph_workspace_search(term: str, limit: int = 25) -> str:
        # Workspace tools are scoped to the served root's
        # .aleph-workspace.json — they span ALL projects, so they must NOT
        # be redirected to a single per-call resolved sub-project.
        return _served_handlers().handle_workspace_search(term, limit=limit)

    @capped_tool(
        name="aleph_workspace_status",
        description=(
            "ALEPH:WORKSPACE:STATUS — Per-project build/staleness report for "
            "the workspace: built or not, source file counts, last build "
            "time, and how many sources are newer than the artifacts."
        ),
    )
    def aleph_workspace_status() -> str:
        return _served_handlers().handle_workspace_status()

    @capped_tool(
        name="aleph_workspace_brief",
        description=(
            "ALEPH:WORKSPACE:BRIEF — Task-aware briefing across all workspace projects. "
            "Shows relevant symbols from each project, cross-project connections "
            "(shared symbol names), and recommended next steps."
        ),
    )
    def aleph_workspace_brief(task: str) -> str:
        return _served_handlers().handle_workspace_brief(task)

    @capped_tool(
        name="aleph_rebuild",
        description=(
            "ALEPH:REBUILD — Force a full rebuild of all artifacts. "
            "Use when artifacts seem stale or after major code changes."
        ),
    )
    def aleph_rebuild() -> str:
        return _active_handlers().handle_rebuild()

    return mcp


def _deferred_startup(project_dir: str) -> threading.Thread:
    """Run every slow or fallible piece of serve startup AFTER the handshake.

    PRE-HANDSHAKE PURITY CONTRACT (enforced by
    tests/unit/test_handshake_purity.py): nothing slow or fallible may run
    between process start and ``mcp_server.run()``. Three shipped hangs came
    from breaking this rule:

      1. the -32000 handshake exit (workspace guard exited mid-handshake),
      2. the pre-handshake auto-migrate rebuild (PR #6 regression), and
      3. the silent 90-minute pre-handshake auto-build.

    So the relocate heal, the missing-artifact auto-build (moved here from
    cli._handle_serve), and the rebuild watcher all run on this daemon
    thread while the server answers ``initialize`` immediately. Tool calls
    that race the build return explicit run-`aleph build` errors instead of
    hanging the client; once the work finishes, fresh handlers are swapped
    in so the next call sees the migrated/built artifacts.
    """

    def _init() -> None:
        global _handlers

        # 1. Self-heal relocated artifacts (machine move / old ID scheme).
        #    Fail-soft: a botched heal must never kill the server.
        from aleph.symbols.id_migration import auto_migrate_ids

        try:
            auto_migrate_ids(project_dir)
        except Exception as e:  # belt and suspenders
            print(f"[aleph] auto-migrate failed: {e}", file=sys.stderr)

        # 2. Auto-build when no artifacts exist yet. This used to run
        #    synchronously in cli._handle_serve before the handshake —
        #    on a large project that was a silent multi-minute stall that
        #    MCP clients report as a hang.
        dict_path = os.path.join(project_dir, ".aleph", "project.aleph.dict")
        if not os.path.isfile(dict_path):
            print(
                "[aleph] No artifacts found — building project in the "
                "background (tools return errors until it finishes)...",
                file=sys.stderr,
            )
            try:
                from aleph.pipeline import auto_build

                result = auto_build(project_dir)
                stats = result.stats
                reduction = (
                    (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
                    if stats.total_original_tokens > 0 else 0.0
                )
                print(
                    f"[aleph] Built: {stats.total_files} files, "
                    f"{stats.total_symbols} symbols, {reduction:.1f}% reduction",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"[aleph] Auto-build failed: {e}", file=sys.stderr)
                print(
                    "[aleph] Tools will return errors until `aleph build` "
                    "succeeds.",
                    file=sys.stderr,
                )

        # 3. Swap in fresh handlers so subsequent tool calls see the
        #    migrated/built artifacts (the tool closures read the module
        #    global on every call). Racing create_server() is harmless:
        #    both construct equivalent handlers for the same directory.
        _handlers = AlephHandlers(
            project_dir=os.path.abspath(project_dir),
            agent_id=os.environ.get("ALEPH_AGENT_ID", "default"),
        )

        # 4. Watch for source changes (incremental background rebuilds).
        _start_auto_rebuild(project_dir)

    thread = threading.Thread(
        target=_init, daemon=True, name="aleph-deferred-startup"
    )
    thread.start()
    return thread


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
        from aleph.project.discovery import discover_source_files
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
                        # Stat-only fast path: only read+hash on stat mismatch
                        if stamps[f].stat_matches(FileStamp.from_stat(f)):
                            continue
                        current = FileStamp.from_file(f)
                        if current.content_hash != stamps[f].content_hash:
                            changed = True
                        # Refresh the stamp either way so an mtime-only
                        # change isn't re-hashed on every poll
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
                        from aleph.pipeline import auto_build
                        auto_build(project_dir)
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


def serve(
    project_dir: str = ".",
    degraded_message: str | None = None,
    skip_root_build: bool = False,
) -> None:
    """Entry point for `aleph serve`. Creates server and runs on stdio.

    PRE-HANDSHAKE PURITY: everything between here and
    ``mcp_server.run()`` must be cheap and infallible (header reads,
    license file reads, config parsing). All slow/fallible startup work —
    the auto-migrate heal, the missing-artifact auto-build, and the
    rebuild watcher — runs on the :func:`_deferred_startup` daemon thread
    so ``initialize`` is answered immediately. Enforced by
    tests/unit/test_handshake_purity.py.

    With ``degraded_message`` set, skips the deferred startup entirely and
    serves a degraded-but-alive server whose tool calls return that
    message (see create_server).

    With ``skip_root_build`` set (workspace root: cli promised "skipping
    single-project auto-build of the root"), the auto-migrate heal, the
    auto-build, and the rebuild watcher are all skipped — each would end
    in ``auto_build(project_dir)``, i.e. a single-project build of the
    whole multi-repo parent (regression in PR #6); hint instead.
    """
    global _startup_thread
    if degraded_message is None and skip_root_build:
        # ALLOWED pre-handshake: maybe_hint_migration is a header string
        # compare (one small file read), bounded and fail-soft.
        from aleph.symbols.id_migration import maybe_hint_migration

        try:
            maybe_hint_migration(project_dir)
        except Exception:  # hints must never hurt the handshake
            pass
    elif degraded_message is None:
        _startup_thread = _deferred_startup(project_dir)
    mcp_server = create_server(project_dir, degraded_message=degraded_message)
    mcp_server.run(transport="stdio")
