"""Phase 2.7: Semantic hash staleness detection.

Verifies that the map file correctly marks components as stale when their
semantic hash changes, and clean when unchanged (reformat-invariant).
"""

from __future__ import annotations

import os
import tempfile
import textwrap

import pytest

from aleph.cli import run_pipeline
from aleph.project.builder import build_project
from aleph.symbols.fingerprint import SemanticFingerprint


# ── Helpers ──

SIMPLE_PY = textwrap.dedent("""\
    def greet(name: str) -> str:
        return f"Hello, {name}!"

    def add(a: int, b: int) -> int:
        return a + b

    class Calculator:
        def multiply(self, x: int, y: int) -> int:
            return x * y
""")


def _write_file(directory: str, name: str, content: str) -> str:
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def _build(root: str):
    return build_project(root, run_pipeline)


def _hash_for(result, filename: str) -> str:
    for entry in result.map_component.files:
        if entry.path == filename:
            return entry.semantic_hash
    raise KeyError(f"{filename} not in map")


# ── Tests: unchanged source ──


class TestCleanWhenUnchanged:
    def test_identical_source_same_hash(self, tmp_path):
        """Building the same file twice produces the same semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))
        r2 = _build(str(tmp_path))
        assert _hash_for(r1, "module.py") == _hash_for(r2, "module.py")

    def test_reformat_does_not_change_hash(self, tmp_path):
        """Adding blank lines and comments does not change the semantic hash.

        Semantic hashes are computed over the symbol graph (names, kinds,
        signatures, call edges) — not over raw source bytes.  Blank lines
        and body-level whitespace changes don't alter the symbol graph.

        Note: extra spaces *inside* signatures do change the raw signature
        text hash — that's a known limitation (tree-sitter preserves
        signature whitespace).  This test covers the invariants that hold
        today: blank lines, body reformatting, and comment additions.
        """
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        # Reformat: extra blank lines between definitions, different body spacing
        reformatted = textwrap.dedent("""\
            def greet(name: str) -> str:

                return f"Hello, {name}!"


            def add(a: int, b: int) -> int:

                return a + b



            class Calculator:

                def multiply(self, x: int, y: int) -> int:

                    return x * y
        """)
        _write_file(str(tmp_path), "module.py", reformatted)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") == _hash_for(r2, "module.py")

    def test_comment_change_does_not_change_hash(self, tmp_path):
        """Changing only comments does not affect the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        with_comments = SIMPLE_PY + "\n# This is a new comment\n"
        _write_file(str(tmp_path), "module.py", with_comments)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") == _hash_for(r2, "module.py")


# ── Tests: stale when semantics change ──


class TestStaleWhenChanged:
    def test_rename_function_changes_hash(self, tmp_path):
        """Renaming a function changes the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        renamed = SIMPLE_PY.replace("def greet(", "def welcome(").replace(
            "greet", "welcome"
        )
        _write_file(str(tmp_path), "module.py", renamed)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") != _hash_for(r2, "module.py")

    def test_add_function_changes_hash(self, tmp_path):
        """Adding a new function changes the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        extended = SIMPLE_PY + textwrap.dedent("""\

            def subtract(a: int, b: int) -> int:
                return a - b
        """)
        _write_file(str(tmp_path), "module.py", extended)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") != _hash_for(r2, "module.py")

    def test_remove_function_changes_hash(self, tmp_path):
        """Removing a function changes the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        # Remove the add function
        reduced = textwrap.dedent("""\
            def greet(name: str) -> str:
                return f"Hello, {name}!"

            class Calculator:
                def multiply(self, x: int, y: int) -> int:
                    return x * y
        """)
        _write_file(str(tmp_path), "module.py", reduced)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") != _hash_for(r2, "module.py")

    def test_change_signature_changes_hash(self, tmp_path):
        """Changing a function's signature changes the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        # Add a parameter to add()
        changed_sig = SIMPLE_PY.replace(
            "def add(a: int, b: int) -> int:",
            "def add(a: int, b: int, c: int = 0) -> int:",
        ).replace("return a + b", "return a + b + c")
        _write_file(str(tmp_path), "module.py", changed_sig)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") != _hash_for(r2, "module.py")

    def test_add_call_edge_changes_hash(self, tmp_path):
        """Adding a call between functions changes the semantic hash."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        # Make greet call add
        with_call = SIMPLE_PY.replace(
            'return f"Hello, {name}!"',
            'x = add(1, 2)\n    return f"Hello, {name}! {x}"',
        )
        _write_file(str(tmp_path), "module.py", with_call)
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "module.py") != _hash_for(r2, "module.py")


# ── Tests: multi-file staleness ──


class TestMultiFileStaleness:
    def test_unchanged_file_keeps_hash_when_sibling_changes(self, tmp_path):
        """File A's hash stays the same when only file B changes."""
        _write_file(str(tmp_path), "a.py", "def foo(): return 1\n")
        _write_file(str(tmp_path), "b.py", "def bar(): return 2\n")
        r1 = _build(str(tmp_path))

        # Change only b.py
        _write_file(str(tmp_path), "b.py", "def bar(): return 3\ndef baz(): pass\n")
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "a.py") == _hash_for(r2, "a.py")
        assert _hash_for(r1, "b.py") != _hash_for(r2, "b.py")

    def test_adding_file_does_not_affect_existing_hashes(self, tmp_path):
        """Adding a new file doesn't change existing files' semantic hashes."""
        _write_file(str(tmp_path), "existing.py", SIMPLE_PY)
        r1 = _build(str(tmp_path))

        _write_file(str(tmp_path), "new_module.py", "def new_func(): pass\n")
        r2 = _build(str(tmp_path))

        assert _hash_for(r1, "existing.py") == _hash_for(r2, "existing.py")


# ── Fingerprint-level invariants ──


class TestSemanticFingerprintInvariants:
    def test_fingerprint_deterministic(self, tmp_path):
        """Same source always produces the same fingerprint."""
        _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = run_pipeline(os.path.join(str(tmp_path), "module.py"))
        r2 = run_pipeline(os.path.join(str(tmp_path), "module.py"))
        assert r1["semantic_hash"] == r2["semantic_hash"]

    def test_fingerprint_changes_on_semantic_edit(self, tmp_path):
        """Different semantics → different fingerprint."""
        p = _write_file(str(tmp_path), "module.py", SIMPLE_PY)
        r1 = run_pipeline(p)

        renamed = SIMPLE_PY.replace("def add(", "def sum_values(")
        _write_file(str(tmp_path), "module.py", renamed)
        r2 = run_pipeline(p)

        assert r1["semantic_hash"] != r2["semantic_hash"]
