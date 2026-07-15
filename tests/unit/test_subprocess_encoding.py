"""Subprocess encoding hygiene lint — text-mode children need an explicit encoding.

Windows data-corruption class: ``subprocess.run``/``Popen`` in *text mode*
(``text=True`` / ``universal_newlines=True``) with no ``encoding=`` decodes
child output with the process's locale codec. On Windows that is cp1252, so
the first non-cp1252 byte in the child's stdout/stderr raises
``UnicodeDecodeError`` — and for ``Popen`` that happens inside the reader
thread, crashing it silently. The user-visible symptom is a build that spews
``UnicodeDecodeError: 'charmap' codec can't decode byte ...`` tracebacks and
drops files (git output is UTF-8; source trees carry UTF-8 identifiers and
paths). The fix is always the same: pass ``encoding="utf-8"`` (and, for
non-fatal robustness on genuinely mixed input, ``errors="replace"``).

This test ast-parses everything under src/ and FAILS on any subprocess spawn
that opts into text mode without pinning ``encoding=``. It mirrors
test_subprocess_hygiene.py (timeout lint) — same auditor shape, different
invariant. There is deliberately no allowlist: pinning an encoding is free and
correct at every text-mode call site, so a violation should be fixed, never
excused.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"

_TEXT_MODE_KWARGS = {"text", "universal_newlines"}
_SPAWN_CALLS = {
    "subprocess.run",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.Popen",
}


def _iter_source_files() -> list[Path]:
    files = sorted(SRC_ROOT.rglob("*.py"))
    assert files, f"no Python sources found under {SRC_ROOT}"
    return files


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to canonical dotted names for subprocess imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                for alias in node.names:
                    aliases[alias.asname or alias.name] = (
                        f"subprocess.{alias.name}"
                    )
    return aliases


class _Auditor(ast.NodeVisitor):
    """Collects text-mode subprocess spawns and whether they pin encoding."""

    def __init__(self, relpath: str, aliases: dict[str, str]) -> None:
        self.relpath = relpath
        self.aliases = aliases
        self.scope: list[str] = []
        # (relpath, qualname, lineno, text_mode, has_encoding, has_splat)
        self.sites: list[tuple[str, str, int, bool, bool, bool]] = []

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

    def _canonical(self, func: ast.expr) -> str:
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
        if callee in _SPAWN_CALLS:
            text_mode = False
            has_encoding = False
            has_splat = False
            for kw in node.keywords:
                if kw.arg is None:
                    has_splat = True  # **kwargs: cannot verify statically
                elif kw.arg in _TEXT_MODE_KWARGS:
                    # text=True / universal_newlines=True opts into text mode.
                    # A literal False leaves the call in bytes mode (safe).
                    if not (
                        isinstance(kw.value, ast.Constant)
                        and kw.value.value is False
                    ):
                        text_mode = True
                elif kw.arg == "encoding":
                    has_encoding = True
            if text_mode:
                self.sites.append((
                    self.relpath, ".".join(self.scope) or "<module>",
                    node.lineno, text_mode, has_encoding, has_splat,
                ))
        self.generic_visit(node)


def _audit_all() -> list[tuple[str, str, int, bool, bool, bool]]:
    sites: list[tuple[str, str, int, bool, bool, bool]] = []
    for path in _iter_source_files():
        relpath = path.relative_to(SRC_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        auditor = _Auditor(relpath, _import_aliases(tree))
        auditor.visit(tree)
        sites.extend(auditor.sites)
    return sites


class TestSubprocessEncoding:
    def test_text_mode_children_pin_encoding(self):
        """Every text-mode subprocess spawn in src/ must set encoding=."""
        violations: list[str] = []
        for relpath, qualname, lineno, _text, has_encoding, splat in _audit_all():
            if has_encoding or splat:
                continue
            violations.append(
                f"{relpath}:{lineno} in {qualname}: text-mode subprocess "
                f"without encoding= — on Windows this decodes child output "
                f"with cp1252 and crashes on UTF-8 bytes; pass "
                f'encoding="utf-8", errors="replace"'
            )
        assert not violations, (
            "Text-mode subprocess spawns missing encoding= (Windows cp1252 "
            "data-corruption class):\n  " + "\n  ".join(violations)
        )

    def test_audit_sees_known_call_sites(self):
        """Self-check: the auditor finds the known text-mode spawn sites."""
        sites = _audit_all()
        seen = {(s[0], s[1]) for s in sites}
        # The per-file git blame stream and the scoped/global commit counts.
        assert ("aleph/temporal/git_history.py", "GitHistory.blame") in seen
        # The MCP selftest client and fixture build.
        assert any(s[0] == "aleph/cli.py" for s in sites)
