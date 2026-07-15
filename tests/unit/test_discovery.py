"""Tests for source discovery and the build-produced query index.

Migrated from tests/unit/test_indexer.py when the legacy v1.0 indexer
(aleph.project.indexer) was retired: discovery moved to
aleph.project.discovery, and the query index is now produced by
aleph.pipeline.build_index_from_result from a BuildResult.
"""

from __future__ import annotations

import json
import os

from aleph.pipeline import build_index_from_result, load_index, save_index
from aleph.project.discovery import discover_source_files, temporal_pathspecs


class TestDiscoverSourceFiles:
    def test_finds_supported_files(self, tmp_path):
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        (tmp_path / "b.txt").write_text("not source\n")
        files = discover_source_files(str(tmp_path))
        assert [os.path.basename(f) for f in files] == ["a.py"]

    def test_skips_known_build_dirs(self, tmp_path):
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "vendored.py").write_text("def v():\n    pass\n")
        files = discover_source_files(str(tmp_path))
        assert [os.path.basename(f) for f in files] == ["a.py"]

    def test_respects_alephignore(self, tmp_path):
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        gen = tmp_path / "generated"
        gen.mkdir()
        (gen / "gen.py").write_text("def g():\n    pass\n")
        (tmp_path / ".alephignore").write_text("# comment\ngenerated\n")
        files = discover_source_files(str(tmp_path))
        assert [os.path.basename(f) for f in files] == ["a.py"]


def _make_vendor_tree(tmp_path):
    """Project with code in vendor/, third_party/, thirdparty/ and a
    nested src/vendor/ — plus one real source file."""
    (tmp_path / "a.py").write_text("def a():\n    pass\n")
    for name in ("vendor", "third_party", "thirdparty"):
        d = tmp_path / name
        d.mkdir()
        (d / f"{name}_mod.py").write_text("def v():\n    pass\n")
    nested = tmp_path / "src" / "vendor"
    nested.mkdir(parents=True)
    (nested / "nested_mod.py").write_text("def n():\n    pass\n")


class TestVendorExclusion:
    """vendor/third_party are skipped by default, with explicit opt-ins."""

    def test_vendor_dirs_skipped_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        _make_vendor_tree(tmp_path)
        files = discover_source_files(str(tmp_path))
        assert [os.path.basename(f) for f in files] == ["a.py"]

    def test_env_opt_in_includes_vendor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALEPH_INCLUDE_VENDOR", "1")
        _make_vendor_tree(tmp_path)
        names = {os.path.basename(f) for f in discover_source_files(str(tmp_path))}
        assert names == {
            "a.py", "vendor_mod.py", "third_party_mod.py",
            "thirdparty_mod.py", "nested_mod.py",
        }

    def test_alephignore_negation_includes_only_that_dir(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        _make_vendor_tree(tmp_path)
        (tmp_path / ".alephignore").write_text("# keep our vendored fork\n!vendor\n")
        names = {os.path.basename(f) for f in discover_source_files(str(tmp_path))}
        # vendor/ re-included (top-level and nested); third_party still skipped
        assert names == {"a.py", "vendor_mod.py", "nested_mod.py"}

    def test_warning_emitted_with_skipped_vendor_dir_count(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        _make_vendor_tree(tmp_path)
        warnings: list[str] = []
        discover_source_files(str(tmp_path), on_warning=warnings.append)
        assert len(warnings) == 1
        # 3 top-level vendor dirs + pkg/vendor
        assert "skipped 4 vendor dir(s)" in warnings[0]
        assert "ALEPH_INCLUDE_VENDOR" in warnings[0]
        assert "!vendor" in warnings[0]

    def test_no_warning_without_vendor_dirs(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        (tmp_path / "a.py").write_text("def a():\n    pass\n")
        warnings: list[str] = []
        discover_source_files(str(tmp_path), on_warning=warnings.append)
        assert warnings == []

    def test_no_warning_when_vendor_opted_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALEPH_INCLUDE_VENDOR", "1")
        _make_vendor_tree(tmp_path)
        warnings: list[str] = []
        discover_source_files(str(tmp_path), on_warning=warnings.append)
        assert warnings == []


class _Raw:
    def __init__(self, name):
        self.name = name
        self.qualified_name = name
        self.kind = type("Kind", (), {"value": "f"})
        self.scope = ""
        self.signature_text = f"def {name}()"
        self.body_text = f"return {name}"


class _Sym:
    def __init__(self, sid, name):
        self.id = sid
        self.raw = _Raw(name)


class _Struct:
    call_edges = [("f_111111", "f_222222")]


class _Result:
    """Minimal BuildResult stand-in with file_results."""
    def __init__(self, root):
        self.file_results = {
            os.path.join(root, "sample.py"): {
                "language": "python",
                "semantic_hash": "abc123def456",
                "symbols": [_Sym("f_111111", "alpha"), _Sym("f_222222", "beta")],
                "struct_component": _Struct(),
            }
        }


class TestBuildIndexFromResult:
    def test_index_contains_symbols_calls_and_hashes(self, tmp_path):
        root = str(tmp_path)
        payload = build_index_from_result(root, _Result(root))

        assert payload["version"] == "2.0"
        assert payload["root"] == root
        (entry,) = payload["files"].values()
        names = {s["name"] for s in entry["symbols"]}
        assert names == {"alpha", "beta"}
        assert entry["calls"] == [("f_111111", "f_222222")]
        assert entry["semantic_hash"] == "abc123def456"
        # Migrated v1.0 indexer functionality: per-symbol signature/body
        # hashes used by `aleph diff`.
        assert set(entry["signature_hashes"]) == {"f_111111", "f_222222"}
        assert set(entry["body_hashes"]) == {"f_111111", "f_222222"}

    def test_save_and_load_roundtrip(self, tmp_path):
        root = str(tmp_path)
        payload = build_index_from_result(root, _Result(root))
        index_path = str(tmp_path / ".aleph.index.json")
        save_index(index_path, payload)

        loaded = load_index(index_path)
        assert loaded["version"] == "2.0"
        assert len(loaded["files"]) == 1
        # JSON round-trips tuples as lists
        (entry,) = loaded["files"].values()
        assert entry["calls"] == [["f_111111", "f_222222"]]

    def test_load_missing_index_returns_empty(self, tmp_path):
        loaded = load_index(str(tmp_path / "missing.json"))
        assert loaded["files"] == {}

    def test_index_is_valid_json_on_disk(self, tmp_path):
        root = str(tmp_path)
        index_path = str(tmp_path / ".aleph.index.json")
        save_index(index_path, build_index_from_result(root, _Result(root)))
        with open(index_path, "r", encoding="utf-8") as f:
            assert json.load(f)["version"] == "2.0"


class TestTemporalPathspecs:
    """Pathspecs scoping the temporal `git log --numstat` to indexed sources."""

    def test_includes_one_glob_per_supported_extension(self, tmp_path):
        specs = temporal_pathspecs(str(tmp_path))
        positives = [s for s in specs if not s.startswith(":(exclude)")]
        assert "*.py" in positives
        assert "*.rs" in positives
        assert "*.ts" in positives
        # Every positive spec is an extension glob — never a bare '*'
        assert all(p.startswith("*.") and len(p) > 2 for p in positives)

    def test_excludes_vendor_dirs_top_level_and_nested(self, tmp_path):
        specs = temporal_pathspecs(str(tmp_path))
        assert ":(exclude)node_modules" in specs
        assert ":(exclude)*/node_modules/*" in specs
        assert ":(exclude)target" in specs
        assert ":(exclude)*/target/*" in specs
        assert ":(exclude).venv" in specs

    def test_excludes_default_vendor_dirs(self, tmp_path, monkeypatch):
        """The vendor skip list flows into temporal scoping automatically."""
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        specs = temporal_pathspecs(str(tmp_path))
        for name in ("vendor", "third_party", "thirdparty"):
            assert f":(exclude){name}" in specs
            assert f":(exclude)*/{name}/*" in specs

    def test_vendor_opt_in_lifts_temporal_excludes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALEPH_INCLUDE_VENDOR", "1")
        specs = temporal_pathspecs(str(tmp_path))
        for name in ("vendor", "third_party", "thirdparty"):
            assert f":(exclude){name}" not in specs
            assert f":(exclude)*/{name}/*" not in specs
        # Non-vendor excludes are untouched by the opt-in
        assert ":(exclude)node_modules" in specs

    def test_alephignore_negation_lifts_temporal_exclude(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ALEPH_INCLUDE_VENDOR", raising=False)
        (tmp_path / ".alephignore").write_text("!vendor\n")
        specs = temporal_pathspecs(str(tmp_path))
        assert ":(exclude)vendor" not in specs
        assert ":(exclude)third_party" in specs

    def test_alephignore_entries_become_excludes(self, tmp_path):
        (tmp_path / ".alephignore").write_text(
            "# binary capture churn\ncaptures/\ngolden\n"
        )
        specs = temporal_pathspecs(str(tmp_path))
        assert ":(exclude)captures" in specs
        assert ":(exclude)*/captures/*" in specs
        assert ":(exclude)golden" in specs

    def test_specs_are_deterministic(self, tmp_path):
        assert temporal_pathspecs(str(tmp_path)) == temporal_pathspecs(str(tmp_path))
