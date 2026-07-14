"""Integration tests for the SQLite storage engine (aleph.db).

Covers the acceptance criteria for the canonical-store migration:
  - build -> query parity between the SQLite path and the text-artifact
    path (resolve / search / callers / expand)
  - incremental rebuilds touch only the changed file's rows
  - EXPAND works on a DEFAULT build (no --per-file) for a project with
    nested subdirectories — the old per-file bodies path bug
  - `aleph export` regenerates equivalent (byte-identical) artifacts
"""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import sqlite3

import pytest

from aleph.pipeline import auto_build
from aleph.query.engine import QueryEngine
from aleph.store.export import export_text_artifacts, ARTIFACT_NAMES
from aleph.store.sqlite_store import SqliteStore, DB_FILENAME, SCHEMA_VERSION
from aleph.emit.file_components import FileComponentWriter


MAIN_PY = '''\
from util import helper

def hello(name):
    """Say hello."""
    return helper(name)

def world():
    print("hi")
    return hello("world")
'''

UTIL_PY = '''\
def helper(name):
    return f"hello {name}"

def unused_helper():
    return 0
'''

NESTED_PY = '''\
def deep_function(x):
    if x > 0:
        return x * 2
    return -x
'''


def _make_project(root) -> str:
    root = str(root)
    os.makedirs(os.path.join(root, "src", "nested"), exist_ok=True)
    with open(os.path.join(root, "main.py"), "w") as f:
        f.write(MAIN_PY)
    with open(os.path.join(root, "util.py"), "w") as f:
        f.write(UTIL_PY)
    with open(os.path.join(root, "src", "nested", "deep.py"), "w") as f:
        f.write(NESTED_PY)
    return root


@pytest.fixture
def built_project(tmp_path):
    root = _make_project(tmp_path / "proj")
    result = auto_build(root)
    return root, result


def _db_path(root: str) -> str:
    return os.path.join(root, ".aleph", DB_FILENAME)


class TestStoreBasics:
    def test_build_writes_db(self, built_project):
        root, _ = built_project
        assert os.path.isfile(_db_path(root))

    def test_no_monolithic_json_cache(self, built_project):
        """The db replaces the wholesale-rewritten JSON build cache."""
        root, _ = built_project
        assert not os.path.isfile(
            os.path.join(root, ".aleph", ".aleph.build_cache.json")
        )

    def test_db_has_no_absolute_paths(self, built_project):
        """Portable db: root is '.' and file paths are relative POSIX."""
        root, _ = built_project
        conn = sqlite3.connect(_db_path(root))
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        assert meta["root"] == "."
        assert meta["schema_version"] == SCHEMA_VERSION
        for (path,) in conn.execute("SELECT path FROM files"):
            assert not os.path.isabs(path)
            assert "\\" not in path
        conn.close()

    def test_build_cache_roundtrip(self, built_project):
        """The db-backed cache reconstructs full per-file results."""
        root, result = built_project
        store = SqliteStore(_db_path(root))
        cache = store.load_build_cache(root)
        store.close()

        assert set(cache.files.keys()) == set(result.cache.files.keys())
        for path, cached in result.cache.files.items():
            loaded = cache.files[path]
            assert loaded.semantic_hash == cached.semantic_hash
            assert loaded.language == cached.language
            assert loaded.stamp.mtime_ns == cached.stamp.mtime_ns
            assert loaded.stamp.content_hash == cached.stamp.content_hash
            assert loaded.symbols_data == cached.symbols_data
            assert loaded.call_edges == cached.call_edges
            assert loaded.call_edge_metadata == cached.call_edge_metadata

    def test_schema_version_mismatch_recreates(self, built_project):
        root, _ = built_project
        conn = sqlite3.connect(_db_path(root))
        with conn:
            conn.execute(
                "UPDATE meta SET value='0' WHERE key='schema_version'"
            )
        conn.close()

        # Rebuild: the old-schema db is dropped and rebuilt, not crashed on
        result = auto_build(root)
        assert result.stats.rebuilt_files == 3
        store = SqliteStore(_db_path(root))
        assert store.is_valid()
        store.close()


class TestQueryParity:
    """Same answers from the SQLite path and the text-artifact path."""

    @pytest.fixture
    def engines(self, built_project, tmp_path):
        root, result = built_project

        # Text-mode expand needs per-file bodies WITH original bodies
        # (root-level files only: the flat text layout is what the text
        # loader can find).
        writer = FileComponentWriter(os.path.join(root, ".aleph"))
        for source_file, file_result in result.file_results.items():
            if os.path.dirname(os.path.relpath(source_file, root)):
                continue
            writer.write_bodies(
                file_result["bodies_component"], include_original_bodies=True,
            )

        # Clone the project and strip the db -> pure text-artifact path
        text_root = str(tmp_path / "text_clone")
        shutil.copytree(root, text_root)
        os.remove(_db_path(text_root))

        db_engine = QueryEngine(root)
        text_engine = QueryEngine(text_root)
        assert db_engine._store is not None
        assert text_engine._store is None
        return db_engine, text_engine, result

    def _all_symbol_ids(self, result):
        return [e.symbol_id for e in result.dict_component.symbols]

    def _code_symbol_ids(self, result):
        """Non-import symbols: the text dictionary's IMPORTS section
        deliberately omits span/sig/lang (token economy), so import
        entries resolve with richer fields from the db — code symbols
        must match exactly."""
        return [
            e.symbol_id for e in result.dict_component.symbols if e.kind != "d"
        ]

    def test_resolve_parity(self, engines):
        db_engine, text_engine, result = engines
        ids = self._code_symbol_ids(result)
        assert ids
        for sid in ids:
            db_res = db_engine.resolve(sid)
            text_res = text_engine.resolve(sid)
            assert db_res is not None and text_res is not None
            assert db_res.to_dict() == text_res.to_dict()
        assert db_engine.resolve("f_zzzzzz") is None
        assert text_engine.resolve("f_zzzzzz") is None

    def test_resolve_import_entries_agree_on_core_fields(self, engines):
        db_engine, text_engine, result = engines
        import_ids = [
            e.symbol_id for e in result.dict_component.symbols if e.kind == "d"
        ]
        assert import_ids
        for sid in import_ids:
            db_res = db_engine.resolve(sid)
            text_res = text_engine.resolve(sid)
            assert db_res is not None and text_res is not None
            assert db_res.symbol_id == text_res.symbol_id
            assert db_res.qualified_name == text_res.qualified_name
            assert db_res.kind == text_res.kind
            assert db_res.file == text_res.file

    def test_search_parity(self, engines):
        db_engine, text_engine, _ = engines
        for query in ("helper", "hello", "deep function", "world",
                      "nested deep", "no_such_thing_xyz"):
            db_results = [r.to_dict() for r in db_engine.search(query)]
            text_results = [r.to_dict() for r in text_engine.search(query)]
            assert db_results == text_results, f"search({query!r}) diverged"

    def test_callers_parity(self, engines):
        db_engine, text_engine, result = engines
        found_any = False
        for sid in self._all_symbol_ids(result):
            db_callers = [c.to_dict() for c in db_engine.callers(sid)]
            text_callers = [c.to_dict() for c in text_engine.callers(sid)]
            assert db_callers == text_callers
            found_any = found_any or bool(db_callers)
        assert found_any, "fixture should have at least one call edge"

    def test_context_parity(self, engines):
        db_engine, text_engine, result = engines
        for sid in self._code_symbol_ids(result):
            db_ctx = db_engine.context(sid)
            text_ctx = text_engine.context(sid)
            assert (db_ctx is None) == (text_ctx is None)
            if db_ctx is not None:
                assert db_ctx.to_dict() == text_ctx.to_dict()

    def test_expand_parity(self, engines):
        """For root-level files (where the text path can serve bodies),
        the db must return the same expansion."""
        db_engine, text_engine, result = engines
        compared = 0
        for entry in result.dict_component.symbols:
            if os.path.dirname(entry.file):
                continue  # text path can't expand nested files (old bug)
            text_body = text_engine.expand(entry.symbol_id)
            db_body = db_engine.expand(entry.symbol_id)
            assert db_body == text_body
            compared += 1
        assert compared > 0

    def test_find_by_name_parity(self, engines):
        db_engine, text_engine, _ = engines
        for name in ("helper", "hello", "deep_function", "missing_name"):
            db_matches = [m.to_dict() for m in db_engine.find_by_name(name)]
            text_matches = [m.to_dict() for m in text_engine.find_by_name(name)]
            assert db_matches == text_matches


class TestExpandDefaultBuild:
    def test_expand_nested_subdir_on_default_build(self, built_project):
        """The old bug: a default build (no --per-file) could never serve
        EXPAND, and even per-file builds broke for nested subdirectories
        (writer used nested paths, reader looked flat). Bodies now live
        in the db, so a plain `aleph build` can expand anything."""
        root, _ = built_project
        engine = QueryEngine(root)
        results = [r for r in engine.search("deep_function") if r.kind == "f"]
        assert results
        body = engine.expand(results[0].symbol_id)
        assert body is not None
        assert "def deep_function(x):" in body
        assert "return x * 2" in body

    def test_expand_unknown_symbol_returns_none(self, built_project):
        root, _ = built_project
        engine = QueryEngine(root)
        assert engine.expand("f_zzzzzz") is None


class TestIncrementalRebuild:
    def _snapshot(self, root):
        """(file_id, mtime, symbol rowids+ids) per path."""
        conn = sqlite3.connect(_db_path(root))
        snap = {}
        for fid, path, mtime in conn.execute(
            "SELECT id, path, mtime_ns FROM files"
        ):
            symbols = list(conn.execute(
                "SELECT rowid, id, body_text FROM symbols WHERE file_id=?"
                " ORDER BY rowid", (fid,)
            ))
            snap[path] = (fid, mtime, symbols)
        conn.close()
        return snap

    def test_unchanged_files_rows_untouched(self, built_project):
        root, _ = built_project
        before = self._snapshot(root)

        # Modify ONLY util.py
        with open(os.path.join(root, "util.py"), "a") as f:
            f.write("\ndef brand_new():\n    return 99\n")
        result = auto_build(root)
        assert result.stats.rebuilt_files == 1
        assert result.stats.reused_files == 2

        after = self._snapshot(root)
        # Unchanged files keep their exact rows (same file id, same stamp,
        # same symbol rowids — no DELETE+INSERT happened for them)
        assert before["main.py"] == after["main.py"]
        assert before["src/nested/deep.py"] == after["src/nested/deep.py"]
        # The changed file was replaced and gained the new symbol
        assert before["util.py"] != after["util.py"]
        names = [row[1] for row in after["util.py"][2]]
        conn = sqlite3.connect(_db_path(root))
        qnames = [r[0] for r in conn.execute(
            "SELECT qualified_name FROM symbols s JOIN files f"
            " ON s.file_id=f.id WHERE f.path='util.py'"
        )]
        conn.close()
        assert "brand_new" in qnames

    def test_no_change_rebuild_touches_nothing(self, built_project):
        root, _ = built_project
        before = self._snapshot(root)
        result = auto_build(root)
        assert result.stats.rebuilt_files == 0
        assert result.stats.reused_files == 3
        assert self._snapshot(root) == before

    def test_deleted_file_rows_removed(self, built_project):
        root, _ = built_project
        os.remove(os.path.join(root, "util.py"))
        result = auto_build(root)
        assert result.stats.removed_files == 1
        snap = self._snapshot(root)
        assert "util.py" not in snap
        # Cascade: no orphaned symbols or local edges
        conn = sqlite3.connect(_db_path(root))
        orphans = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE file_id NOT IN"
            " (SELECT id FROM files)"
        ).fetchone()[0]
        edge_orphans = conn.execute(
            "SELECT COUNT(*) FROM call_edges WHERE kind='local' AND"
            " file_id NOT IN (SELECT id FROM files)"
        ).fetchone()[0]
        conn.close()
        assert orphans == 0
        assert edge_orphans == 0

    def test_incremental_query_results_stay_correct(self, built_project):
        root, _ = built_project
        with open(os.path.join(root, "util.py"), "a") as f:
            f.write("\ndef brand_new():\n    return 99\n")
        auto_build(root)
        engine = QueryEngine(root)
        results = [r for r in engine.search("brand_new") if r.kind == "f"]
        assert results
        assert "return 99" in engine.expand(results[0].symbol_id)


class TestExport:
    def test_export_regenerates_identical_artifacts(self, built_project, tmp_path):
        root, _ = built_project
        out = str(tmp_path / "exported")
        written = export_text_artifacts(root, output_dir=out)
        assert len(written) == len(ARTIFACT_NAMES)
        for name in ARTIFACT_NAMES:
            built = os.path.join(root, ".aleph", name)
            exported = os.path.join(out, name)
            assert os.path.isfile(exported), f"{name} not exported"
            assert filecmp.cmp(built, exported, shallow=False), (
                f"{name} differs from the builder-written artifact"
            )

    def test_export_after_db_only_build(self, tmp_path):
        """--no-text-artifacts then export == full dual-write build."""
        root = _make_project(tmp_path / "proj")
        auto_build(root, text_artifacts=False)
        aleph_dir = os.path.join(root, ".aleph")
        assert not os.path.isfile(os.path.join(aleph_dir, "project.aleph.dict"))

        # Engine still answers from the db alone
        engine = QueryEngine(root)
        assert engine._store is not None
        assert engine.search("helper")

        out = str(tmp_path / "exported")
        export_text_artifacts(root, output_dir=out)

        # A subsequent dual-write build (fully incremental, same data)
        # must produce the exact same text artifacts the export did.
        result = auto_build(root)
        assert result.stats.rebuilt_files == 0
        for name in ARTIFACT_NAMES:
            assert filecmp.cmp(
                os.path.join(aleph_dir, name),
                os.path.join(out, name),
                shallow=False,
            ), f"{name} differs between export and dual-write build"

    def test_export_cli(self, built_project, monkeypatch, capsys):
        from aleph import cli
        root, _ = built_project
        monkeypatch.setattr(
            "sys.argv", ["aleph", "export", root, "--json"]
        )
        cli.main()
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["artifacts"]) == len(ARTIFACT_NAMES)

    def test_export_without_db_errors(self, tmp_path, monkeypatch, capsys):
        from aleph import cli
        monkeypatch.setattr(
            "sys.argv", ["aleph", "export", str(tmp_path)]
        )
        with pytest.raises(SystemExit):
            cli.main()
        assert "Run `aleph build` first" in capsys.readouterr().err


class TestNoTextArtifactsFlag:
    def test_cli_build_no_text_artifacts(self, tmp_path, monkeypatch, capsys):
        from aleph import cli
        root = _make_project(tmp_path / "proj")
        monkeypatch.setattr(
            "sys.argv",
            ["aleph", "build", root, "--no-text-artifacts", "--json"],
        )
        cli.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["artifacts"] == ["aleph.db"]
        aleph_dir = os.path.join(root, ".aleph")
        assert os.path.isfile(os.path.join(aleph_dir, DB_FILENAME))
        assert not os.path.isfile(os.path.join(aleph_dir, "project.aleph.map"))
