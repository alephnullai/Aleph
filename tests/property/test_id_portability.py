"""Property tests for symbol-ID portability (scheme v2) and migration.

Invariants:
  * Building the same project from two different absolute locations yields
    IDENTICAL symbol IDs (root-relative hashing).
  * Case-variant root paths yield identical IDs (explicit lowercasing).
  * IDs are stable across a rebuild with no source changes.
  * migrate-ids maps old (absolute-path, v1) IDs to new (v2) IDs correctly,
    epistemic state survives migration, and the migration is idempotent.
  * The startup hint fires on ROOT case/location mismatch and on
    old-scheme artifacts, and stays silent when everything matches.
"""

import json
import os
import shutil

import pytest
from hypothesis import given, strategies as st

from aleph.emit.file_components import FileComponentWriter
from aleph.emit.loader import AlephLoader
from aleph.epistemic.store import EpistemicStore
from aleph.pipeline import auto_build, run_pipeline
from aleph.project.builder import build_project
from aleph.symbols import id_migration
from aleph.symbols.id_migration import (
    compute_id_mapping,
    maybe_hint_migration,
    migrate_ids,
    read_artifact_meta,
)
from aleph.symbols.identifier import (
    ID_SCHEME_VERSION,
    SymbolIdentifier,
    normalize_source_path,
)
from aleph.model.symbol import RawSymbol, Span
from aleph.model.enums import SymbolKind


PROJECT_FILES = {
    "mod.py": (
        "def foo(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def bar(y):\n"
        "    return foo(y) * 2\n"
    ),
    os.path.join("pkg", "util.py"): (
        "def helper(a, b):\n"
        "    return a - b\n"
    ),
}


def _write_project(root) -> str:
    root = str(root)
    for rel, content in PROJECT_FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return root


def _dict_ids(root: str) -> list[tuple[str, str, str]]:
    """(symbol_id, qualified_name, file) triples from the built dictionary."""
    path = os.path.join(root, ".aleph", "project.aleph.dict")
    with open(path, "r", encoding="utf-8") as f:
        comp = AlephLoader().deserialize_project_dict(f.read())
    return sorted((s.symbol_id, s.qualified_name, s.file) for s in comp.symbols)


def _make_raw(source_file: str, name: str = "foo") -> RawSymbol:
    return RawSymbol(
        name=name,
        qualified_name=name,
        kind=SymbolKind.FUNCTION,
        scope="",
        span=Span(0, 0, 5, 0),
        language="python",
        source_file=source_file,
        signature_text=f"def {name}(x)",
    )


# ── Path normalization unit/property checks ──


class TestNormalizeSourcePath:
    def test_root_relative_posix_lowered(self):
        assert (
            normalize_source_path("/Home/User/Proj/Src/Mod.PY", "/Home/User/Proj")
            == "src/mod.py"
        )

    def test_case_variant_roots_agree(self):
        a = normalize_source_path("/tmp/Proj/src/mod.py", "/tmp/Proj")
        b = normalize_source_path("/tmp/proj/SRC/MOD.py", "/tmp/proj")
        assert a == b == "src/mod.py"

    def test_backslashes_normalized(self):
        raw = RawSymbol(
            name="f", qualified_name="f", kind=SymbolKind.FUNCTION, scope="",
            span=Span(0, 0, 1, 0), language="python",
            source_file="pkg\\mod.py",
        )
        ident = SymbolIdentifier()
        # Legacy path still hashes via symbol_id_hash's separator fix.
        assert ident.assign_id(raw)

    def test_legacy_fallback_is_verbatim(self):
        # No project root => old (v1) behavior: path unchanged.
        assert (
            normalize_source_path("/Abs/Path/File.py", None) == "/Abs/Path/File.py"
        )

    def test_empty_path(self):
        assert normalize_source_path("", "/root") == ""
        assert normalize_source_path("", None) == ""

    @given(
        rel=st.lists(
            st.from_regex(r"[a-zA-Z_][a-zA-Z0-9_]{0,8}", fullmatch=True),
            min_size=1, max_size=4,
        ).map(lambda parts: os.path.join(*parts) + ".py"),
        root_a=st.sampled_from(["/tmp/aleph_a", "/home/other/Checkout", "/Users/X/Repos/Proj"]),
        root_b=st.sampled_from(["/tmp/aleph_b", "/srv/ci/workdir", "/users/x/repos/proj"]),
    )
    def test_location_invariance_property(self, rel, root_a, root_b):
        """normalize(root/rel, root) is independent of the root."""
        a = normalize_source_path(os.path.join(root_a, rel), root_a)
        b = normalize_source_path(os.path.join(root_b, rel), root_b)
        assert a == b
        assert "\\" not in a and not os.path.isabs(a)
        assert a == a.lower()

    def test_assign_id_location_invariant(self):
        id_a = SymbolIdentifier("/loc/one").assign_id(_make_raw("/loc/one/pkg/m.py"))
        id_b = SymbolIdentifier("/elsewhere/two").assign_id(
            _make_raw("/elsewhere/two/pkg/m.py")
        )
        assert id_a == id_b

    def test_assign_id_case_invariant(self):
        id_a = SymbolIdentifier("/Repos/Aleph").assign_id(_make_raw("/Repos/Aleph/m.py"))
        id_b = SymbolIdentifier("/repos/aleph").assign_id(_make_raw("/repos/aleph/m.py"))
        assert id_a == id_b

    def test_legacy_ids_unchanged(self):
        """Without a root, IDs match the historical absolute-path scheme."""
        from aleph.util.hashing import symbol_id_hash

        raw = _make_raw("/abs/checkout/m.py")
        sid = SymbolIdentifier().assign_id(raw)
        expected = symbol_id_hash(
            raw.qualified_name, raw.scope, language=raw.language,
            source_file=raw.source_file, signature="def foo(x)",
        )
        assert sid.hex_hash == expected


# ── Whole-project build invariance ──


class TestBuildPortability:
    def test_identical_ids_from_two_locations(self, tmp_path):
        root_a = _write_project(tmp_path / "checkout_one")
        root_b = _write_project(tmp_path / "deeply" / "nested" / "checkout_two")
        auto_build(root_a)
        auto_build(root_b)
        assert _dict_ids(root_a) == _dict_ids(root_b)

    def test_identical_ids_from_case_variant_dirs(self, tmp_path):
        """Aleph/ vs aleph/ style split must not churn IDs."""
        root_a = _write_project(tmp_path / "one" / "MyProj")
        root_b = _write_project(tmp_path / "two" / "myproj")
        auto_build(root_a)
        auto_build(root_b)
        assert _dict_ids(root_a) == _dict_ids(root_b)

    def test_ids_stable_across_rebuild(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        auto_build(root)
        first = _dict_ids(root)
        auto_build(root)  # incremental (cache hit)
        assert _dict_ids(root) == first
        auto_build(root, full=True)  # full rebuild
        assert _dict_ids(root) == first

    def test_map_records_id_scheme(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        auto_build(root)
        meta = read_artifact_meta(os.path.join(root, ".aleph"))
        assert meta is not None
        assert meta["id_scheme"] == ID_SCHEME_VERSION
        assert meta["root"] == os.path.abspath(root)


# ── Migration ──


def _legacy_build(root: str) -> dict[str, str]:
    """Build artifacts the way the old (v1) scheme did: absolute-path IDs.

    Returns old_id -> qualified_name for the built symbols.
    """
    result = build_project(root, run_pipeline)  # no root threading => v1 IDs
    out = os.path.join(root, ".aleph")
    writer = FileComponentWriter(out)
    writer.write_project_map(result.map_component)
    writer.write_project_dict(result.dict_component)
    return {
        str(sym.id): sym.raw.qualified_name
        for fr in result.file_results.values()
        for sym in fr["symbols"]
    }


def _new_scheme_ids(root: str) -> dict[str, str]:
    """qualified_name -> v2 id, computed via a fresh portable build."""
    result = build_project(
        root, lambda p: run_pipeline(p, project_root=os.path.abspath(root))
    )
    return {
        sym.raw.qualified_name: str(sym.id)
        for fr in result.file_results.values()
        for sym in fr["symbols"]
    }


class TestMigration:
    def test_mapping_matches_both_schemes(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        old_ids = _legacy_build(root)
        expected_new = _new_scheme_ids(root)

        plan = compute_id_mapping(root)
        assert plan.matched_dict_ids == len(old_ids)
        assert not plan.unmatched_dict_ids
        assert not plan.missing_files
        for old_id, qname in old_ids.items():
            assert plan.mapping[old_id] == expected_new[qname]

    def test_epistemic_data_survives_migration(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        old_ids = _legacy_build(root)
        expected_new = _new_scheme_ids(root)
        old_foo = next(oid for oid, name in old_ids.items() if name == "foo")
        new_foo = expected_new["foo"]
        assert old_foo != new_foo  # absolute vs relative scheme must differ

        # Flag/infer/patch under the OLD scheme, like a pre-upgrade agent.
        store = EpistemicStore(
            os.path.join(root, ".aleph", "project.aleph.epistemic")
        )
        with store.transaction() as data:
            data.setdefault("inferences", []).append(
                {"symbol_id": old_foo, "conclusion": "adds one", "confidence": 0.9}
            )
            data.setdefault("flags", []).append(
                {"symbol_id": old_foo, "reason": "verify edge case", "verified": False}
            )
            data.setdefault("patches", []).append(
                {"patch_id": "patch_1", "symbol_id": old_foo,
                 "intent": "rename", "status": "pending"}
            )
            data.setdefault("memories", []).append(
                {"session_id": "s1", "symbol_dict": {old_foo: "foo"}, "entries": []}
            )
            data.setdefault("reviewed", []).append(
                {"session": "s1", "symbols": {old_foo: 3}, "queries": 1}
            )

        report = migrate_ids(root)
        assert report.rewritten_refs == 5
        assert "project.aleph.epistemic" in report.stores_updated

        data = store.load()
        assert data["inferences"][0]["symbol_id"] == new_foo
        assert data["flags"][0]["symbol_id"] == new_foo
        assert data["patches"][0]["symbol_id"] == new_foo
        assert data["memories"][0]["symbol_dict"] == {new_foo: "foo"}
        assert data["reviewed"][0]["symbols"] == {new_foo: 3}
        # Nothing references the old ID anymore.
        assert old_foo not in json.dumps(data)

    def test_migration_is_idempotent(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        old_ids = _legacy_build(root)
        store = EpistemicStore(
            os.path.join(root, ".aleph", "project.aleph.epistemic")
        )
        old_foo = next(iter(old_ids))
        with store.transaction() as data:
            data.setdefault("flags", []).append(
                {"symbol_id": old_foo, "reason": "x", "verified": False}
            )
        first = migrate_ids(root)
        assert first.rewritten_refs == 1
        snapshot = store.load()
        second = migrate_ids(root)
        assert second.rewritten_refs == 0
        assert store.load() == snapshot

    def test_dry_run_writes_nothing(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        old_ids = _legacy_build(root)
        store = EpistemicStore(
            os.path.join(root, ".aleph", "project.aleph.epistemic")
        )
        old_foo = next(iter(old_ids))
        with store.transaction() as data:
            data.setdefault("flags", []).append(
                {"symbol_id": old_foo, "reason": "x", "verified": False}
            )
        before = store.load()
        report = migrate_ids(root, dry_run=True)
        assert report.dry_run
        assert report.plan.changed  # mapping was computed...
        assert store.load() == before  # ...but nothing written
        assert "dry-run" in report.summary()

    def test_moved_project_migrates_via_recorded_root(self, tmp_path):
        """Artifacts built at location A still migrate after a move to B."""
        root_a = _write_project(tmp_path / "Original")
        _legacy_build(root_a)  # ROOT line records root_a

        root_b = os.path.join(str(tmp_path), "relocated")
        shutil.copytree(root_a, root_b)
        old_ids_at_a = {
            str(sym.id): sym.raw.qualified_name
            for fr in build_project(root_a, run_pipeline).file_results.values()
            for sym in fr["symbols"]
        }

        plan = compute_id_mapping(root_b)  # old root taken from ROOT line
        assert plan.old_root == os.path.abspath(root_a)
        assert set(old_ids_at_a) <= set(plan.mapping)
        # New IDs at B equal new IDs at A (portability).
        assert sorted(plan.mapping.values()) == sorted(
            _new_scheme_ids(root_a)[name] for name in old_ids_at_a.values()
        )

    def test_unrecoverable_when_file_changed(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        _legacy_build(root)
        # Change an existing symbol's signature after the build: the IDs the
        # dictionary recorded for it can no longer be re-derived.
        with open(os.path.join(root, "mod.py"), "w", encoding="utf-8") as f:
            f.write("def foo(x, extra):\n    return x + extra\n")
        plan = compute_id_mapping(root)
        assert plan.unmatched_dict_ids  # changed file's dict IDs reported

    def test_missing_artifacts_raise(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compute_id_mapping(str(tmp_path))


# ── Startup hint ──


class TestMigrationHint:
    def _write_map(self, root: str, recorded_root: str, scheme: int | None) -> str:
        out = os.path.join(root, ".aleph")
        os.makedirs(out, exist_ok=True)
        lines = ["[ALEPH:MAP:1.0]", f"[ROOT:{recorded_root}]", "[ALEPH_VERSION:0.5.0]"]
        if scheme is not None:
            lines.append(f"[ID_SCHEME:{scheme}]")
        lines += ["[FILES]", "[/FILES]"]
        with open(os.path.join(out, "project.aleph.map"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        # resolve_artifact_dir requires the dict marker to use .aleph/
        with open(os.path.join(out, "project.aleph.dict"), "w", encoding="utf-8") as f:
            f.write(
                f"[ALEPH:DICT:1.0]\n[ROOT:{recorded_root}]\n[SYMBOLS]\n[/SYMBOLS]\n"
            )
        return out

    def setup_method(self):
        id_migration._HINTED_DIRS.clear()

    def test_case_mismatch_hints(self, tmp_path):
        root = str(tmp_path / "proj")
        os.makedirs(root)
        recorded = os.path.abspath(root).swapcase()
        self._write_map(root, recorded, scheme=ID_SCHEME_VERSION)
        hint = maybe_hint_migration(root)
        assert hint and "migrate-ids" in hint and "case" in hint

    def test_location_mismatch_hints(self, tmp_path):
        root = str(tmp_path / "proj")
        os.makedirs(root)
        self._write_map(root, "/somewhere/else/entirely", scheme=ID_SCHEME_VERSION)
        hint = maybe_hint_migration(root)
        assert hint and "migrate-ids" in hint and "location" in hint

    def test_old_scheme_hints(self, tmp_path):
        root = str(tmp_path / "proj")
        os.makedirs(root)
        self._write_map(root, os.path.abspath(root), scheme=None)  # pre-v2 artifact
        hint = maybe_hint_migration(root)
        assert hint and "migrate-ids" in hint and "scheme v1" in hint

    def test_matching_root_and_scheme_silent(self, tmp_path):
        root = str(tmp_path / "proj")
        os.makedirs(root)
        self._write_map(root, os.path.abspath(root), scheme=ID_SCHEME_VERSION)
        assert maybe_hint_migration(root) is None

    def test_hint_printed_once_per_dir(self, tmp_path, capsys):
        root = str(tmp_path / "proj")
        os.makedirs(root)
        self._write_map(root, "/somewhere/else", scheme=ID_SCHEME_VERSION)
        assert maybe_hint_migration(root) is not None
        assert maybe_hint_migration(root) is None  # suppressed on repeat

    def test_no_artifacts_silent(self, tmp_path):
        assert maybe_hint_migration(str(tmp_path)) is None

    def test_fresh_auto_build_silent(self, tmp_path):
        root = _write_project(tmp_path / "proj")
        auto_build(root)
        assert maybe_hint_migration(root) is None
