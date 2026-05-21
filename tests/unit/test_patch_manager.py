"""Unit tests for Aleph semantic patch manager.

Tests the patch lifecycle: propose, list, apply, reject.
"""

from __future__ import annotations

import json
import os

import pytest

from aleph.patch.manager import PatchManager, PatchRecord, PatchApplyResult


# ── PatchRecord tests ──


class TestPatchRecord:
    def test_to_dict(self):
        r = PatchRecord(
            patch_id="patch_1",
            symbol_id="f_abc123",
            intent="add null check",
            semantic_hash="deadbeef",
            file="src/main.py",
        )
        d = r.to_dict()
        assert d["patch_id"] == "patch_1"
        assert d["symbol_id"] == "f_abc123"
        assert d["intent"] == "add null check"
        assert d["semantic_hash"] == "deadbeef"
        assert d["file"] == "src/main.py"
        assert d["status"] == "pending"
        assert d["created_at"]  # auto-populated

    def test_from_dict(self):
        d = {
            "patch_id": "patch_2",
            "symbol_id": "f_def456",
            "intent": "change return type",
            "semantic_hash": "cafebabe",
            "file": "src/lib.py",
            "status": "applied",
            "created_at": "2026-03-18T10:00:00Z",
        }
        r = PatchRecord.from_dict(d)
        assert r.patch_id == "patch_2"
        assert r.status == "applied"
        assert r.created_at == "2026-03-18T10:00:00Z"

    def test_from_dict_defaults(self):
        d = {
            "patch_id": "patch_3",
            "symbol_id": "f_x",
            "intent": "test",
        }
        r = PatchRecord.from_dict(d)
        assert r.status == "pending"
        assert r.semantic_hash == ""
        assert r.file == ""

    def test_roundtrip(self):
        r1 = PatchRecord(
            patch_id="patch_5",
            symbol_id="f_test",
            intent="test intent",
            semantic_hash="aabb",
            file="test.py",
        )
        r2 = PatchRecord.from_dict(r1.to_dict())
        assert r1.patch_id == r2.patch_id
        assert r1.symbol_id == r2.symbol_id
        assert r1.intent == r2.intent
        assert r1.semantic_hash == r2.semantic_hash


# ── PatchManager tests ──


@pytest.fixture
def patch_project(tmp_path):
    """Create a minimal project directory for patch testing."""
    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()
    (aleph_dir / "project.aleph.dict").write_text("")  # Required for artifact resolution
    (aleph_dir / "project.aleph.epistemic").write_text("{}")
    return tmp_path


@pytest.fixture
def patch_project_with_source(tmp_path):
    """Project with a source file and minimal .aleph artifacts."""
    aleph_dir = tmp_path / ".aleph"
    aleph_dir.mkdir()
    (aleph_dir / "project.aleph.dict").write_text("")  # Required for artifact resolution

    # Source file
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text(
        'def helper(x):\n'
        '    """A helper function."""\n'
        '    return x + 1\n'
        '\n'
        'def main():\n'
        '    result = helper(42)\n'
        '    return result\n'
    )

    # Dict for symbol resolution
    (aleph_dir / "project.aleph.dict").write_text(
        "[ALEPH:DICT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[SYMBOLS]\n"
        "f_abc123=helper file=src/main.py kind=f scope=module sig=deadbeef\n"
        "f_def456=main file=src/main.py kind=f scope=module sig=cafebabe\n"
        "[/SYMBOLS]\n"
    )

    # Struct for call indexes
    (aleph_dir / "project.aleph.struct").write_text(
        "[ALEPH:STRUCT:PROJECT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[XREFS]\n"
        "f_def456->f_abc123 src=src/main.py dst=src/main.py\n"
        "[/XREFS]\n"
    )

    # Salience
    (aleph_dir / "project.aleph.salience").write_text(
        "[ALEPH:SALIENCE:PROJECT:1.0]\n"
        f"[ROOT:{tmp_path}]\n"
        "[SCORES]\n"
        "f_abc123 helper file=src/main.py score=0.5 local=1 xfile=0 total=1\n"
        "[/SCORES]\n"
    )

    # Index
    (aleph_dir / ".aleph.index.json").write_text(json.dumps({
        "files": {
            "src/main.py": {
                "calls": [["f_def456", "f_abc123"]],
                "symbols": [
                    {"id": "f_abc123", "qualified_name": "helper", "kind": "f"},
                    {"id": "f_def456", "qualified_name": "main", "kind": "f"},
                ],
            }
        }
    }))

    # Empty epistemic
    (aleph_dir / "project.aleph.epistemic").write_text("{}")

    return tmp_path


class TestPatchManagerPropose:
    def test_propose_creates_patch(self, patch_project):
        mgr = PatchManager(str(patch_project))
        record = mgr.propose("f_abc123", "add null check")
        assert record.patch_id == "patch_1"
        assert record.symbol_id == "f_abc123"
        assert record.intent == "add null check"
        assert record.status == "pending"

    def test_propose_increments_id(self, patch_project):
        mgr = PatchManager(str(patch_project))
        r1 = mgr.propose("f_abc123", "first")
        r2 = mgr.propose("f_def456", "second")
        assert r1.patch_id == "patch_1"
        assert r2.patch_id == "patch_2"

    def test_propose_persists_to_epistemic(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_abc123", "add null check")
        # Read back
        path = patch_project / ".aleph" / "project.aleph.epistemic"
        data = json.loads(path.read_text())
        assert len(data["patches"]) == 1
        assert data["patches"][0]["intent"] == "add null check"

    def test_propose_with_file_override(self, patch_project):
        mgr = PatchManager(str(patch_project))
        record = mgr.propose("f_abc123", "test", file="custom/path.py")
        assert record.file == "custom/path.py"

    def test_propose_resolves_symbol(self, patch_project_with_source):
        mgr = PatchManager(str(patch_project_with_source))
        record = mgr.propose("f_abc123", "add null check")
        assert record.file == "src/main.py"
        assert record.semantic_hash == "deadbeef"


class TestPatchManagerList:
    def test_list_empty(self, patch_project):
        mgr = PatchManager(str(patch_project))
        assert mgr.list_patches() == []

    def test_list_all(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "first")
        mgr.propose("f_b", "second")
        patches = mgr.list_patches()
        assert len(patches) == 2

    def test_list_filter_by_status(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "first")
        mgr.propose("f_b", "second")
        mgr.reject("patch_1")
        pending = mgr.list_patches(status="pending")
        assert len(pending) == 1
        assert pending[0].patch_id == "patch_2"

    def test_get_patch(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "test")
        r = mgr.get_patch("patch_1")
        assert r is not None
        assert r.symbol_id == "f_a"

    def test_get_patch_not_found(self, patch_project):
        mgr = PatchManager(str(patch_project))
        assert mgr.get_patch("patch_999") is None


class TestPatchManagerReject:
    def test_reject_pending(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "test")
        msg = mgr.reject("patch_1")
        assert "rejected" in msg.lower()
        r = mgr.get_patch("patch_1")
        assert r.status == "rejected"

    def test_reject_not_found(self, patch_project):
        mgr = PatchManager(str(patch_project))
        msg = mgr.reject("patch_999")
        assert "not found" in msg.lower()

    def test_reject_already_applied(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "test")
        # Manually set to applied
        data = mgr._load_epistemic()
        data["patches"][0]["status"] = "applied"
        mgr._save_epistemic(data)
        msg = mgr.reject("patch_1")
        assert "applied" in msg.lower()


class TestPatchManagerApply:
    def test_apply_not_found(self, patch_project):
        mgr = PatchManager(str(patch_project))
        result = mgr.apply("patch_999")
        assert not result.success
        assert "not found" in result.message.lower()

    def test_apply_not_pending(self, patch_project):
        mgr = PatchManager(str(patch_project))
        mgr.propose("f_a", "test")
        mgr.reject("patch_1")
        result = mgr.apply("patch_1")
        assert not result.success
        assert "rejected" in result.message.lower()

    def test_apply_inserts_todo_block(self, patch_project_with_source):
        mgr = PatchManager(str(patch_project_with_source))
        mgr.propose("f_abc123", "add input validation")
        result = mgr.apply("patch_1")
        assert result.success
        assert "applied" in result.message.lower()

        # Verify TODO was inserted
        src = (patch_project_with_source / "src" / "main.py").read_text()
        assert "TODO [patch_1]" in src
        assert "add input validation" in src
        assert "ALEPH:PATCH applied" in src

    def test_apply_marks_status(self, patch_project_with_source):
        mgr = PatchManager(str(patch_project_with_source))
        mgr.propose("f_abc123", "test intent")
        mgr.apply("patch_1")
        r = mgr.get_patch("patch_1")
        assert r.status == "applied"

    def test_apply_hash_changed_without_force(self, patch_project_with_source):
        mgr = PatchManager(str(patch_project_with_source))
        # Propose with current hash
        record = mgr.propose("f_abc123", "test")
        # Manually change the stored hash to simulate drift
        data = mgr._load_epistemic()
        data["patches"][0]["semantic_hash"] = "oldhash"
        mgr._save_epistemic(data)

        result = mgr.apply("patch_1")
        assert not result.success
        assert result.hash_changed
        assert "--force" in result.message

    def test_apply_hash_changed_with_force(self, patch_project_with_source):
        mgr = PatchManager(str(patch_project_with_source))
        record = mgr.propose("f_abc123", "test with force")
        data = mgr._load_epistemic()
        data["patches"][0]["semantic_hash"] = "oldhash"
        mgr._save_epistemic(data)

        result = mgr.apply("patch_1", force=True)
        assert result.success
        assert result.hash_changed

    def test_apply_missing_source_file(self, patch_project):
        mgr = PatchManager(str(patch_project))
        # Propose with a file that doesn't exist
        mgr.propose("f_a", "test", file="nonexistent.py")
        result = mgr.apply("patch_1")
        assert not result.success
        assert "not found" in result.message.lower() or "nonexistent" in result.message.lower()

    def test_apply_to_main_function(self, patch_project_with_source):
        """Test applying patch to the main function (not just helper)."""
        mgr = PatchManager(str(patch_project_with_source))
        mgr.propose("f_def456", "add logging")
        result = mgr.apply("patch_1")
        assert result.success

        src = (patch_project_with_source / "src" / "main.py").read_text()
        assert "TODO [patch_1]" in src
        assert "add logging" in src


class TestFindSymbolBodyStart:
    def test_find_simple_function(self):
        lines = [
            "def foo():\n",
            "    return 1\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "foo")
        assert idx == 1

    def test_find_function_with_docstring(self):
        lines = [
            "def foo():\n",
            '    """A docstring."""\n',
            "    return 1\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "foo")
        assert idx == 2

    def test_find_function_with_multiline_docstring(self):
        lines = [
            "def foo(x):\n",
            '    """A multi-line\n',
            '    docstring.\n',
            '    """\n',
            "    return x\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "foo")
        assert idx == 4

    def test_find_class(self):
        lines = [
            "class MyClass:\n",
            "    pass\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "MyClass")
        assert idx == 1

    def test_not_found(self):
        lines = [
            "def other():\n",
            "    pass\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "nonexistent")
        assert idx is None

    def test_find_function_with_params(self):
        lines = [
            "def helper(x):\n",
            '    """A helper function."""\n',
            "    return x + 1\n",
        ]
        idx = PatchManager._find_symbol_body_start(lines, "helper")
        assert idx == 2


class TestPatchManagerNoEpistemic:
    """Test behavior when epistemic file doesn't exist yet."""

    def test_propose_creates_epistemic(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text("")  # Required for artifact resolution
        mgr = PatchManager(str(tmp_path))
        record = mgr.propose("f_test", "test intent")
        assert record.patch_id == "patch_1"
        assert (aleph_dir / "project.aleph.epistemic").exists()

    def test_list_empty_no_file(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        mgr = PatchManager(str(tmp_path))
        assert mgr.list_patches() == []
