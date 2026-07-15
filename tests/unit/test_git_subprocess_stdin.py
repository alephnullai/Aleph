"""Every git child must get stdin=DEVNULL (Windows serve hang guard).

An MCP server's stdin IS the client's JSON-RPC pipe. A subprocess spawned
without an explicit stdin inherits it; on Windows the child then blocks on
that pipe rather than exiting, so the call burns its full timeout and is
killed. Every git call site is fail-soft, so this never raised — it just
made things mysteriously slow: `git rev-parse` (timeout=5) in _detect_root
put a flat +5s on EVERY build inside `aleph serve`, taking a one-file
rebuild from 0.1s to 5.1s and blowing the 10s selftest per-tool budget on
Windows CI. POSIX doesn't block the same way, so CI stayed green there and
the bug shipped.

A timing assertion would be flaky, so this guards the invariant at the
source: parse the module and require the stdin kwarg on every spawn. If you
add a git call here, pass stdin=subprocess.DEVNULL.
"""

from __future__ import annotations

import ast
import inspect

import aleph.temporal.git_history as git_history

SPAWNERS = {"run", "Popen", "call", "check_call", "check_output"}


def _spawn_calls(tree: ast.AST) -> list[ast.Call]:
    """Every subprocess.<spawner>(...) call in the module."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in SPAWNERS
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]


def _stdin_kwarg(call: ast.Call) -> ast.keyword | None:
    return next((k for k in call.keywords if k.arg == "stdin"), None)


class TestGitChildrenDoNotInheritStdin:
    def test_module_actually_spawns_git(self):
        """Guard the guard: if the spawns move, this test must not silently pass."""
        tree = ast.parse(inspect.getsource(git_history))
        assert len(_spawn_calls(tree)) >= 4

    def test_every_spawn_passes_stdin_devnull(self):
        tree = ast.parse(inspect.getsource(git_history))
        offenders = []
        for call in _spawn_calls(tree):
            kw = _stdin_kwarg(call)
            devnull = (
                kw is not None
                and isinstance(kw.value, ast.Attribute)
                and kw.value.attr == "DEVNULL"
            )
            if not devnull:
                offenders.append(
                    f"line {call.lineno}: "
                    + ("stdin not passed" if kw is None else "stdin is not subprocess.DEVNULL")
                )
        assert not offenders, (
            "git children must not inherit the MCP client's stdin pipe "
            "(they hang on Windows until their timeout kills them):\n  "
            + "\n  ".join(offenders)
        )
