"""Pre-handshake purity — `aleph serve` must answer initialize immediately.

Three shipped hangs came from slow or fallible work running between
process start and ``mcp_server.run()``:

  1. the -32000 handshake exit (workspace guard sys.exit mid-handshake),
  2. the pre-handshake auto-migrate rebuild (PR #6 regression), and
  3. the silent 90-minute pre-handshake auto-build.

This test makes the fourth incident unmergeable, two ways:

* LIVE TIMING — spawn a real ``aleph serve`` child over stdio on each of
  the three boot paths that have each shipped a hang (built project,
  unbuilt directory, multi-repo parent) and require an ``initialize``
  answer within the handshake budget.

* STATIC AST GUARD — parse the pre-handshake path (cli._handle_serve,
  server.serve, server.create_server) and fail if ANY call appears there
  that is not in the explicit cheap allowlist below. Known-slow ops
  (auto_build, auto_migrate_ids, the rebuild watcher, subprocess/git
  spawns) are additionally named in a denylist for a sharper message.
  Slow startup work belongs in aleph.mcp.server._deferred_startup, which
  runs on a daemon thread AFTER the handshake.

The same contract is exercised release-side by `aleph selftest`
(docs/RESPONSIVENESS_CONTRACT.md).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"

# ── budgets ──
# Same knob as `aleph selftest`: ALEPH_SELFTEST_BUDGET_MULT loosens every
# responsiveness budget uniformly on slow CI runners.


def _budget_multiplier() -> float:
    try:
        mult = float(os.environ.get("ALEPH_SELFTEST_BUDGET_MULT", "1"))
    except ValueError:
        return 1.0
    return mult if mult > 0 else 1.0


HANDSHAKE_BUDGET = 10.0 * _budget_multiplier()


# ════════════════════════════════════════════════════════════════════
# Static AST guard
# ════════════════════════════════════════════════════════════════════

# The pre-handshake path: every statement in these scopes runs before the
# MCP client receives its `initialize` response. Nested function bodies
# are excluded — in create_server those are the tool handlers, which run
# per tool call, after the handshake.
_PRE_HANDSHAKE_SCOPES: tuple[tuple[str, str], ...] = (
    ("src/aleph/cli.py", "_handle_serve"),
    ("src/aleph/mcp/server.py", "serve"),
    ("src/aleph/mcp/server.py", "create_server"),
)

# Explicit cheap allowlist, per scope. EVERYTHING called pre-handshake
# must be listed here with a reason; an unlisted call fails the guard and
# forces this review. "Cheap" means: bounded local file reads (license,
# config, artifact headers), small directory scans with hard caps, pure
# in-process bookkeeping. NOT cheap: builds, migrations, git, network,
# anything proportional to project size.
_CHEAP_ALLOWLIST: dict[tuple[str, str], set[str]] = {
    ("src/aleph/cli.py", "_handle_serve"): {
        # stdlib / bookkeeping
        "print", "getattr", "sys.exit", "os.path.abspath", "os.path.join",
        # bounded workspace-guard scan (hard-capped directory listing)
        "_workspace_guard_reason",
        "_workspace_guard_tool_message",
        "_print_workspace_guard_message",
        "_detect_project_for_cwd",
        "_stdio_client_attached",
        # workspace config parse (one small file)
        "find_workspace_file",
        # artifact header read (version stamp only, not the artifacts)
        "_check_artifact_version",
        # hand-off into the server (which must itself be pure — below)
        "serve",
    },
    ("src/aleph/mcp/server.py", "serve"): {
        # migration HINT is a header string compare (one small file read,
        # fail-soft) — the migration itself is deferred
        "maybe_hint_migration",
        # starts the post-handshake daemon thread; returns immediately
        "_deferred_startup",
        # tool registration + handler construction (in-process, no I/O
        # proportional to project size)
        "create_server",
        # the handshake itself
        "mcp_server.run",
    },
    ("src/aleph/mcp/server.py", "create_server"): {
        "os.path.abspath", "os.environ.get",
        # handler construction is lazy: artifacts load on first tool call
        "AlephHandlers",
        # per-project handler LRU: an empty in-process OrderedDict, no I/O.
        # Per-call resolution lives in the nested _resolve_call_handlers /
        # tool wrappers, which the guard skips (they run after handshake).
        "HandlerCache",
        # MCP plumbing
        "FastMCP",
    },
}

# Known-slow operations that have each already shipped a hang (or are git
# spawns, which can block on locks/huge repos). Named separately so a
# regression gets a message that says exactly what happened, not just
# "call not in allowlist".
_KNOWN_SLOW = {
    "auto_build", "_auto_build",          # full project build
    "auto_migrate_ids",                   # artifact rebuild/heal
    "_start_auto_rebuild",                # rebuild watcher (ends in build)
    "GitHistory",                         # git log/blame spawns
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_call", "subprocess.check_output",
    "subprocess.getoutput", "subprocess.getstatusoutput",
    "os.system", "os.popen",
}


def _dotted(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            return ".".join([value.id] + list(reversed(parts)))
        return "<expr>." + ".".join(reversed(parts))
    return "<dynamic>"


def _find_function(path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"{path}: function {name}() not found — if it was renamed, update "
        f"_PRE_HANDSHAKE_SCOPES in {__file__}"
    )


def _direct_calls(fn: ast.FunctionDef) -> list[tuple[str, int]]:
    """(callee, lineno) for calls in fn's own body, skipping nested defs."""
    calls: list[tuple[str, int]] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # runs later (tool handlers / deferred thread)
            if isinstance(child, ast.Call):
                calls.append((_dotted(child.func), child.lineno))
            walk(child)

    walk(fn)
    return calls


class TestPreHandshakeStaticGuard:
    def test_no_slow_ops_on_pre_handshake_path(self):
        """Known-slow ops must not appear between serve() entry and run()."""
        violations: list[str] = []
        for relpath, name in _PRE_HANDSHAKE_SCOPES:
            fn = _find_function(REPO_ROOT / relpath, name)
            for callee, lineno in _direct_calls(fn):
                if callee in _KNOWN_SLOW:
                    violations.append(
                        f"{relpath}:{lineno} in {name}(): {callee}() runs "
                        f"PRE-HANDSHAKE — this exact class of call has "
                        f"shipped a hang three times. Move it into "
                        f"aleph.mcp.server._deferred_startup."
                    )
        assert not violations, "\n  ".join(["Pre-handshake purity broken:"] + violations)

    def test_every_pre_handshake_call_is_allowlisted(self):
        """Anything new on the pre-handshake path needs an audited entry."""
        violations: list[str] = []
        for key in _PRE_HANDSHAKE_SCOPES:
            relpath, name = key
            allowed = _CHEAP_ALLOWLIST[key]
            fn = _find_function(REPO_ROOT / relpath, name)
            for callee, lineno in _direct_calls(fn):
                if callee not in allowed:
                    violations.append(
                        f"{relpath}:{lineno} in {name}(): {callee}() is not "
                        f"in the pre-handshake cheap allowlist. If it is "
                        f"genuinely cheap (bounded local read / in-process "
                        f"bookkeeping), add an audited entry in {__file__}; "
                        f"otherwise move it to _deferred_startup."
                    )
        assert not violations, "\n  ".join(["Unaudited pre-handshake call(s):"] + violations)

    def test_allowlist_entries_are_live(self):
        """Stale allowlist entries must be removed (they mask regressions)."""
        for key, allowed in _CHEAP_ALLOWLIST.items():
            relpath, name = key
            fn = _find_function(REPO_ROOT / relpath, name)
            seen = {callee for callee, _ in _direct_calls(fn)}
            stale = allowed - seen
            assert not stale, (
                f"stale allowlist entries for {relpath}:{name}(): "
                f"{sorted(stale)} — delete them"
            )

    def test_slow_work_lives_on_the_deferred_thread(self):
        """The heal + auto-build run in _deferred_startup, post-handshake."""
        server_py = REPO_ROOT / "src/aleph/mcp/server.py"
        fn = _find_function(server_py, "_deferred_startup")
        names = {
            _dotted(node.func)
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
        }
        assert "auto_migrate_ids" in names, (
            "_deferred_startup no longer runs auto_migrate_ids — the "
            "relocate heal must still happen (post-handshake)"
        )
        assert "auto_build" in names, (
            "_deferred_startup no longer runs auto_build — the "
            "missing-artifact build must still happen (post-handshake)"
        )
        # And serve() must actually start it.
        serve_fn = _find_function(server_py, "serve")
        serve_calls = {callee for callee, _ in _direct_calls(serve_fn)}
        assert "_deferred_startup" in serve_calls


# ════════════════════════════════════════════════════════════════════
# Live initialize timing — the three boot paths that each shipped a hang
# ════════════════════════════════════════════════════════════════════

_SAMPLE_SOURCE = (
    '"""Sample module for the handshake purity test."""\n'
    "\n"
    "\n"
    "def greet(name):\n"
    '    """Return a greeting for name."""\n'
    '    return "hello " + name\n'
)


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    return env


def _time_initialize(project: str, budget: float) -> tuple[float, str, list[str]]:
    """Spawn `aleph serve project`, time the MCP initialize round-trip.

    Returns (elapsed, status, stderr_lines). Status: OK / TIMEOUT / FAIL.
    The child is always torn down (terminate, then kill) before returning,
    so a hung server can never hang the test suite itself.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "aleph.cli", "serve", project],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_child_env(),
    )
    response: dict = {}
    got = threading.Event()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id") == 1:
                response.update(obj)
                got.set()
                return

    def drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line.rstrip("\n"))

    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=drain_stderr, daemon=True).start()

    start = time.monotonic()
    try:
        try:
            proc.stdin.write(json.dumps({  # type: ignore[union-attr]
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "handshake-purity-test", "version": "1"},
                },
            }) + "\n")
            proc.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError):
            return time.monotonic() - start, "FAIL", stderr_lines
        got.wait(budget)
        elapsed = time.monotonic() - start
        if not got.is_set():
            return elapsed, ("TIMEOUT" if proc.poll() is None else "FAIL"), stderr_lines
        if "error" in response:
            return elapsed, "FAIL", stderr_lines
        return elapsed, "OK", stderr_lines
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except OSError:
            pass


@pytest.fixture(scope="module")
def built_project(tmp_path_factory) -> str:
    """A tiny project with artifacts already built (boot path a)."""
    d = tmp_path_factory.mktemp("hp-built")
    (d / "sample.py").write_text(_SAMPLE_SOURCE, encoding="utf-8")
    build = subprocess.run(
        [sys.executable, "-m", "aleph.cli", "build", str(d), "--quiet"],
        capture_output=True,
        text=True,
        timeout=120.0 * _budget_multiplier(),
        env=_child_env(),
    )
    assert build.returncode == 0 and (d / ".aleph").is_dir(), (
        f"fixture build failed:\n{build.stderr}"
    )
    return str(d)


@pytest.fixture()
def unbuilt_dir(tmp_path) -> str:
    """A source directory with no .aleph artifacts (boot path b)."""
    (tmp_path / "sample.py").write_text(_SAMPLE_SOURCE, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture()
def multi_repo_parent(tmp_path) -> str:
    """A multi-repo parent dir that triggers the workspace guard (path c)."""
    for repo in ("repo-a", "repo-b"):
        (tmp_path / repo / ".git").mkdir(parents=True)
        (tmp_path / repo / "main.py").write_text(
            "def f():\n    return 1\n", encoding="utf-8"
        )
    return str(tmp_path)


def _assert_handshake_ok(label: str, project: str) -> None:
    elapsed, status, stderr_lines = _time_initialize(project, HANDSHAKE_BUDGET)
    tail = "\n".join(stderr_lines[-20:])
    assert status == "OK", (
        f"{label}: initialize {status} after {elapsed:.1f}s "
        f"(budget {HANDSHAKE_BUDGET:.1f}s) — pre-handshake purity broken.\n"
        f"server stderr tail:\n{tail}"
    )
    assert elapsed <= HANDSHAKE_BUDGET, (
        f"{label}: initialize answered but took {elapsed:.1f}s "
        f"(budget {HANDSHAKE_BUDGET:.1f}s)"
    )


class TestHandshakeTiming:
    def test_initialize_within_budget_on_built_project(self, built_project):
        _assert_handshake_ok("built-project", built_project)

    def test_initialize_within_budget_on_unbuilt_dir(self, unbuilt_dir):
        """Auto-build must be deferred — the 90-minute silent stall path."""
        _assert_handshake_ok("unbuilt-dir", unbuilt_dir)

    def test_initialize_within_budget_on_multi_repo_parent(self, multi_repo_parent):
        """Degraded mode must still complete the handshake — the -32000 path."""
        _assert_handshake_ok("multi-repo-parent", multi_repo_parent)
