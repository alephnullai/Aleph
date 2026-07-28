"""Subprocess hygiene lint — child processes need authoritative timeouts.

Hang-ledger class B: a child process without an enforced wall-clock bound
can park the whole product forever (the user-visible symptom is "Aleph
randomly hangs"). This test ast-parses everything under src/ and FAILS on:

  * ``subprocess.run`` / ``call`` / ``check_call`` / ``check_output``
    without a ``timeout=`` keyword (or with a literal ``timeout=None``,
    which is the same thing spelled louder) — unless the call site is in
    the audited allowlist below;
  * ``subprocess.Popen`` anywhere outside the audited hardened wrappers
    (Popen has no timeout parameter, so a Popen is only safe inside a
    wrapper that owns the child's lifetime with watchdogs/bounded waits);
  * ``subprocess.getoutput`` / ``getstatusoutput`` (no timeout support at
    all) and ``os.system`` / ``os.popen`` — banned outright.

NOT in scope, on purpose: ``concurrent.futures.ProcessPoolExecutor``
worker spawns in src/aleph/project/parallel.py are multiprocessing pool
children, not subprocess children — their lifetime is owned by the
executor context manager and the build's progress instrumentation, and
they run no shell/git commands of their own (per-file ``git blame``
inside workers goes through the allowlisted GitHistory wrappers below).

To add a new child process: use ``subprocess.run(..., timeout=...)``, or
add an allowlist entry HERE with a comment explaining exactly which
mechanism bounds the child's lifetime.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

# ── Audited allowlist ──
#
# Key: (path relative to src/, dotted qualname of the enclosing scope).
# Every entry must say WHY the call is hang-proof. Entries are verified to
# still exist (a stale entry fails the meta-test below).

POPEN_ALLOWLIST: dict[tuple[str, str], str] = {
    ("aleph/temporal/git_history.py", "GitHistory._repo_log"): (
        # The one-per-repo `git log --numstat` stream. Hardened: a
        # threading.Timer(log_timeout) watchdog kills the child on expiry,
        # output is stream-parsed (never buffered whole), and the final
        # proc.wait() is bounded (10s grace, then kill + reap). On any
        # bound being hit the build degrades to partial history with a
        # warning instead of blocking.
        "watchdog timer kills child; bounded final wait"
    ),
    ("aleph/cli.py", "_McpSelftestSession.__init__"): (
        # The `aleph serve` child the selftest drives over stdio. Hardened:
        # every read is deadline-bounded (_await), writes trap
        # BrokenPipeError, stderr is drained into a bounded deque, and
        # close() terminate()s then kill()s with a bounded wait. The
        # session class exists precisely to make this Popen hang-proof.
        "deadline-bounded reads; close() terminates then kills"
    ),
}

# run/call/check_* sites that may omit timeout=. Currently empty: every
# blocking-call site in src/ carries an explicit timeout keyword. Keep it
# that way — prefer fixing the call over extending this list.
RUN_ALLOWLIST: dict[tuple[str, str], str] = {}

# Calls that take a timeout= keyword.
_TIMEOUT_CALLS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
}
# Banned outright: no way to bound them.
_BANNED_CALLS = {
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "os.system",
    "os.popen",
}


def _iter_source_files() -> list[Path]:
    files = sorted(SRC_ROOT.rglob("*.py"))
    assert files, f"no Python sources found under {SRC_ROOT}"
    return files


class _Auditor(ast.NodeVisitor):
    """Collects subprocess/os spawn call sites with their enclosing scope."""

    def __init__(self, relpath: str, aliases: dict[str, str]) -> None:
        self.relpath = relpath
        self.aliases = aliases  # local name -> canonical dotted name
        self.scope: list[str] = []
        # (relpath, qualname, lineno, canonical_callee, has_timeout,
        #  timeout_is_literal_none, has_kwargs_splat)
        self.sites: list[tuple[str, str, int, str, bool, bool, bool]] = []

    # ── scope tracking ──

    def _visit_scoped(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node, node.name)

    # ── call resolution ──

    def _canonical(self, func: ast.expr) -> str:
        """Resolve a call target to a canonical dotted name ('' if N/A)."""
        if isinstance(func, ast.Name):
            return self.aliases.get(func.id, "")
        if isinstance(func, ast.Attribute):
            parts: list[str] = [func.attr]
            value = func.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                root = self.aliases.get(value.id, value.id)
                return ".".join([root] + list(reversed(parts)))
        return ""

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._canonical(node.func)
        if callee in _TIMEOUT_CALLS | {"subprocess.Popen"} | _BANNED_CALLS:
            has_timeout = False
            timeout_is_none = False
            has_splat = False
            for kw in node.keywords:
                if kw.arg is None:
                    has_splat = True  # **kwargs: cannot verify statically
                elif kw.arg == "timeout":
                    has_timeout = True
                    if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                        timeout_is_none = True
            self.sites.append((
                self.relpath, ".".join(self.scope) or "<module>",
                node.lineno, callee, has_timeout, timeout_is_none, has_splat,
            ))
        self.generic_visit(node)


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to canonical dotted names for subprocess/os imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("subprocess", "os"):
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in ("subprocess", "os"):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )
    return aliases


def _audit_all() -> list[tuple[str, str, int, str, bool, bool, bool]]:
    sites: list[tuple[str, str, int, str, bool, bool, bool]] = []
    for path in _iter_source_files():
        relpath = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        auditor = _Auditor(relpath, _import_aliases(tree))
        auditor.visit(tree)
        sites.extend(auditor.sites)
    return sites


class TestSubprocessHygiene:
    def test_no_unbounded_child_processes(self):
        """Every child-process spawn in src/ has an authoritative bound."""
        violations: list[str] = []
        for relpath, qualname, lineno, callee, has_timeout, is_none, splat in _audit_all():
            where = f"{relpath}:{lineno} in {qualname}"
            key = (relpath, qualname)
            if callee in _BANNED_CALLS:
                violations.append(
                    f"{where}: {callee}() is banned — it cannot be bounded; "
                    f"use subprocess.run(..., timeout=...) instead"
                )
            elif callee == "subprocess.Popen":
                if key not in POPEN_ALLOWLIST:
                    violations.append(
                        f"{where}: subprocess.Popen outside the audited "
                        f"hardened wrappers — Popen has no timeout, so it is "
                        f"only allowed inside a wrapper that owns the "
                        f"child's lifetime (see POPEN_ALLOWLIST in "
                        f"{__file__})"
                    )
            elif callee in _TIMEOUT_CALLS:
                if key in RUN_ALLOWLIST:
                    continue
                if is_none:
                    violations.append(
                        f"{where}: {callee}(timeout=None) explicitly disables "
                        f"the bound — pass a finite timeout"
                    )
                elif not has_timeout and not splat:
                    violations.append(
                        f"{where}: {callee}() without timeout= — a child "
                        f"process must carry an authoritative wall-clock bound"
                    )
                elif not has_timeout and splat:
                    violations.append(
                        f"{where}: {callee}(**kwargs) hides whether timeout= "
                        f"is set — pass timeout explicitly at the call site"
                    )
        assert not violations, (
            "Unbounded child-process spawns (hang-ledger class B):\n  "
            + "\n  ".join(violations)
        )

    def test_allowlist_entries_are_live(self):
        """Stale allowlist entries must be removed (they mask new spawns)."""
        popen_seen = {
            (relpath, qualname)
            for relpath, qualname, _, callee, _, _, _ in _audit_all()
            if callee == "subprocess.Popen"
        }
        for key in POPEN_ALLOWLIST:
            assert key in popen_seen, (
                f"stale POPEN_ALLOWLIST entry {key}: no subprocess.Popen "
                f"found there anymore — delete the entry"
            )
        # RUN_ALLOWLIST is meant to stay empty; if someone adds an entry it
        # must at least point at a real call site.
        run_seen = {
            (relpath, qualname)
            for relpath, qualname, _, callee, _, _, _ in _audit_all()
            if callee in _TIMEOUT_CALLS
        }
        for key in RUN_ALLOWLIST:
            assert key in run_seen, f"stale RUN_ALLOWLIST entry {key}"

    def test_audit_sees_known_call_sites(self):
        """Self-check: the auditor actually finds the known spawn sites.

        Guards against the lint silently going blind (e.g. a refactor moves
        files and rglob finds nothing, or alias resolution breaks).
        """
        sites = _audit_all()
        callees = {(s[0], s[3]) for s in sites}
        assert ("aleph/temporal/git_history.py", "subprocess.Popen") in callees
        assert ("aleph/temporal/git_history.py", "subprocess.run") in callees
        assert ("aleph/cli.py", "subprocess.Popen") in callees
        assert ("aleph/cli.py", "subprocess.run") in callees
