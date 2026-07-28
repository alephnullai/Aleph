"""Integration tests for the semantic patching end-to-end workflow.

Tests the full patch lifecycle via CLI and MCP handlers against
real or realistic .aleph artifacts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from aleph.mcp.handlers import AlephHandlers
from aleph.patch.manager import PatchManager


# ── Fixtures ──


@pytest.fixture
def project_with_source(tmp_path):
    """Create a realistic project with source code and .aleph artifacts."""
    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()

    # Source files
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "calculator.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def subtract(a, b):\n"
        '    """Subtract b from a."""\n'
        "    return a - b\n"
        "\n"
        "def multiply(a, b):\n"
        '    """Multiply a by b.\n'
        "\n"
        "    Supports integers and floats.\n"
        '    """\n'
        "    return a * b\n"
        "\n"
        "def divide(a, b):\n"
        "    return a / b\n"
    )

    # Dict
    (aleph_dir / "project.aleph.dict").write_text(
        "[ALEPH:DICT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[SYMBOLS]\n"
        "f_add001=add file=src/calculator.py kind=f scope=module sig=aaa111\n"
        "f_sub002=subtract file=src/calculator.py kind=f scope=module sig=bbb222\n"
        "f_mul003=multiply file=src/calculator.py kind=f scope=module sig=ccc333\n"
        "f_div004=divide file=src/calculator.py kind=f scope=module sig=ddd444\n"
        "[/SYMBOLS]\n"
    )

    # Struct
    (aleph_dir / "project.aleph.struct").write_text(
        "[ALEPH:STRUCT:PROJECT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[XREFS]\n"
        "[/XREFS]\n"
    )

    # Salience
    (aleph_dir / "project.aleph.salience").write_text(
        "[ALEPH:SALIENCE:PROJECT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[SCORES]\n"
        "f_add001 add file=src/calculator.py score=0.5 local=0 xfile=0 total=0\n"
        "[/SCORES]\n"
    )

    # Index
    (aleph_dir / ".aleph.index.json").write_text(json.dumps({
        "files": {
            "src/calculator.py": {
                "calls": [],
                "symbols": [
                    {"id": "f_add001", "qualified_name": "add", "kind": "f"},
                    {"id": "f_sub002", "qualified_name": "subtract", "kind": "f"},
                    {"id": "f_mul003", "qualified_name": "multiply", "kind": "f"},
                    {"id": "f_div004", "qualified_name": "divide", "kind": "f"},
                ],
            }
        }
    }))

    # Empty epistemic
    (aleph_dir / "project.aleph.epistemic").write_text("{}")

    return tmp_path


# ── Full lifecycle tests via PatchManager ──


class TestPatchLifecycle:
    def test_propose_list_apply_lifecycle(self, project_with_source):
        mgr = PatchManager(str(project_with_source))

        # Propose
        r = mgr.propose("f_div004", "add zero division check")
        assert r.patch_id == "patch_1"
        assert r.file == "src/calculator.py"
        assert r.semantic_hash == "ddd444"

        # List
        pending = mgr.list_patches(status="pending")
        assert len(pending) == 1
        assert pending[0].intent == "add zero division check"

        # Apply
        result = mgr.apply("patch_1")
        assert result.success

        # Verify source was modified
        src = (project_with_source / "src" / "calculator.py").read_text()
        assert "TODO [patch_1]" in src
        assert "add zero division check" in src

        # Verify status changed
        applied = mgr.list_patches(status="applied")
        assert len(applied) == 1
        pending = mgr.list_patches(status="pending")
        assert len(pending) == 0

    def test_propose_and_reject_lifecycle(self, project_with_source):
        mgr = PatchManager(str(project_with_source))
        mgr.propose("f_add001", "add type hints")
        mgr.reject("patch_1")

        rejected = mgr.list_patches(status="rejected")
        assert len(rejected) == 1
        pending = mgr.list_patches(status="pending")
        assert len(pending) == 0

    def test_multiple_patches_on_same_file(self, project_with_source):
        mgr = PatchManager(str(project_with_source))
        mgr.propose("f_add001", "add type hints")
        mgr.propose("f_sub002", "add input validation")

        pending = mgr.list_patches(status="pending")
        assert len(pending) == 2

        # Apply both
        r1 = mgr.apply("patch_1")
        r2 = mgr.apply("patch_2")
        assert r1.success
        assert r2.success

        src = (project_with_source / "src" / "calculator.py").read_text()
        assert "TODO [patch_1]" in src
        assert "TODO [patch_2]" in src

    def test_apply_with_multiline_docstring(self, project_with_source):
        """Ensure patch is inserted after multi-line docstring."""
        mgr = PatchManager(str(project_with_source))
        mgr.propose("f_mul003", "add overflow check")
        result = mgr.apply("patch_1")
        assert result.success

        src = (project_with_source / "src" / "calculator.py").read_text()
        # TODO should be after the docstring, before "return a * b"
        lines = src.split("\n")
        todo_idx = next(i for i, l in enumerate(lines) if "TODO [patch_1]" in l)
        return_idx = next(i for i, l in enumerate(lines) if "return a * b" in l)
        assert todo_idx < return_idx

    def test_hash_drift_detection(self, project_with_source):
        """Test that hash drift is detected and --force works."""
        mgr = PatchManager(str(project_with_source))
        mgr.propose("f_add001", "add type hints")

        # Simulate hash drift
        data = mgr._load_epistemic()
        data["patches"][0]["semantic_hash"] = "stale_hash"
        mgr._save_epistemic(data)

        # Should fail without force
        result = mgr.apply("patch_1")
        assert not result.success
        assert result.hash_changed

        # Should succeed with force
        result = mgr.apply("patch_1", force=True)
        assert result.success


# ── MCP handler integration tests ──


class TestMCPHandlerPatchIntegration:
    def test_propose_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        result = h.handle_patch_propose("f_div004", "add zero division check")
        assert "patch_1" in result
        assert "f_div004" in result

    def test_list_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        h.handle_patch_propose("f_div004", "add zero division check")
        result = h.handle_patch_list()
        assert "patch_1" in result
        assert "add zero division check" in result

    def test_apply_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        h.handle_patch_propose("f_add001", "add type hints")
        result = h.handle_patch_apply("patch_1")
        assert "applied" in result.lower()

    def test_reject_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        h.handle_patch_propose("f_add001", "add type hints")
        result = h.handle_patch_reject("patch_1")
        assert "rejected" in result.lower()

    def test_backward_compat_handle_patch(self, project_with_source):
        """The old handle_patch() method should still work as alias."""
        h = AlephHandlers(project_dir=str(project_with_source))
        result = h.handle_patch("f_add001", "add type hints")
        assert "patch_1" in result

    def test_apply_nonexistent_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        result = h.handle_patch_apply("patch_999")
        assert "not found" in result.lower()

    def test_reject_nonexistent_via_handler(self, project_with_source):
        h = AlephHandlers(project_dir=str(project_with_source))
        result = h.handle_patch_reject("patch_999")
        assert "not found" in result.lower()


# ── CLI integration tests ──


class TestPatchCLI:
    """Test the `aleph patch` CLI subcommand."""

    def _run_cli(self, *args, cwd=None):
        result = subprocess.run(
            [sys.executable, "-m", "aleph.cli", "patch", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
        )
        return result

    def test_cli_propose(self, project_with_source):
        result = self._run_cli(
            "propose", "f_add001", "add type hints",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        assert "patch_1" in result.stdout

    def test_cli_list_empty(self, project_with_source):
        result = self._run_cli(
            "list",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        assert "No pending" in result.stdout

    def test_cli_list_after_propose(self, project_with_source):
        self._run_cli(
            "propose", "f_add001", "add type hints",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        result = self._run_cli(
            "list",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        assert "patch_1" in result.stdout

    def test_cli_apply(self, project_with_source):
        self._run_cli(
            "propose", "f_add001", "add type hints",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        result = self._run_cli(
            "apply", "patch_1",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        assert "applied" in result.stdout.lower()

    def test_cli_reject(self, project_with_source):
        self._run_cli(
            "propose", "f_add001", "add type hints",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        result = self._run_cli(
            "reject", "patch_1",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        assert "rejected" in result.stdout.lower()

    def test_cli_json_output(self, project_with_source):
        result = self._run_cli(
            "propose", "f_add001", "add type hints",
            "-d", str(project_with_source),
            "--json",
            cwd=str(project_with_source),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["patch_id"] == "patch_1"
        assert data["symbol_id"] == "f_add001"

    def test_cli_propose_missing_args(self, project_with_source):
        result = self._run_cli(
            "propose", "f_add001",
            "-d", str(project_with_source),
            cwd=str(project_with_source),
        )
        assert result.returncode != 0
