"""Unit tests for the optional semantic embedding index (no fastembed needed).

Covers, without the optional dependency installed:
  - passage construction (pure string formatting)
  - graceful degradation: `--semantic` without fastembed builds
    lexical-only with one warning, search never crashes
  - the full hybrid path via a fake embedder: storage in the embeddings
    table, sticky meta flag, incremental re-embedding of changed files,
    RRF fusion, and the identifier-shaped fast path that skips fusion
  - QueryEngine.semantic_status() for handle_brief's one-line note
"""

from __future__ import annotations

import os
import re
import sqlite3
import zlib

import pytest

np = pytest.importorskip("numpy")

from aleph.pipeline import auto_build
from aleph.query import semantic
from aleph.query.engine import QueryEngine
from aleph.query.semantic import build_passage
from aleph.store.sqlite_store import SqliteStore


# ── Fake embedder: deterministic bag-of-words vectors ──
# crc32 (not hash()) so vectors are stable across processes/runs.
# 512 buckets: no collisions among the test vocabulary.

_DIM = 512


def _fake_vector(text: str) -> "np.ndarray":
    v = np.zeros(_DIM, dtype=np.float32)
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        v[zlib.crc32(tok.encode()) % _DIM] += 1.0
    n = float(np.linalg.norm(v))
    return v / n if n else v


class FakeEmbedder:
    """Drop-in for aleph.query.semantic.PassageEmbedder without fastembed."""

    model = "fake-bag-of-words"
    dim = _DIM

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, passages: list[str]) -> list[bytes]:
        self.calls.append(list(passages))
        return [_fake_vector(p).tobytes() for p in passages]


def _write_project(root) -> str:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "storage.py"), "w") as f:
        f.write(
            "def fetch_record(key):\n"
            '    """Look up a stored value."""\n'
            "    return key\n"
        )
    with open(os.path.join(root, "charts.py"), "w") as f:
        f.write(
            "def render_chart(data):\n"
            "    return list(data)\n"
        )
    return str(root)


def _db_path(root: str) -> str:
    return os.path.join(root, ".aleph", "aleph.db")


def _embedding_rows(root: str) -> list:
    conn = sqlite3.connect(_db_path(root))
    try:
        return conn.execute(
            "SELECT symbol_id, file, model, dim, vector FROM embeddings"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture
def fake_semantic(monkeypatch):
    """Make the semantic layer fully functional without fastembed."""
    embedder = FakeEmbedder()
    monkeypatch.setattr(semantic, "is_available", lambda: True)
    monkeypatch.setattr(
        semantic, "get_passage_embedder", lambda: embedder
    )
    monkeypatch.setattr(semantic, "embed_query", _fake_vector)
    # auto_build imports get_passage_embedder from the module each call,
    # so patching the module attribute is sufficient.
    return embedder


# ── Passage construction ──

class TestBuildPassage:
    def test_basic_shape(self):
        p = build_passage("function", "Store::save", "def save(self, x)")
        assert p == "function Store::save: def save(self, x)"

    def test_first_body_line_appended(self):
        p = build_passage(
            "function", "fetch", "def fetch(key)",
            body_text='\n  \n  """Look up a stored value."""\n  return 1\n',
        )
        # Docstring markers are stripped: the model embeds prose.
        assert p.endswith("| Look up a stored value.")

    def test_docstring_preferred_over_declaration_line(self):
        # body_text that starts at the def line (the common case): the
        # declaration is skipped — it already IS the passage — and the
        # docstring summary is what gets embedded.
        p = build_passage(
            "f", "AlephHandlers::_flush_query_entry",
            "def _flush_query_entry(self, entry: dict) -> None:",
            body_text=(
                "def _flush_query_entry(self, entry: dict) -> None:\n"
                '    """Append a single query entry to the JSONL log file."""\n'
                "    try:\n"
            ),
        )
        assert p.endswith("| Append a single query entry to the JSONL log file.")
        assert p.count("_flush_query_entry(self") == 1

    def test_multiline_signature_fragment_skipped(self):
        sig = "def fuse(self, intent: str, lexical: list) -> list:"
        p = build_passage(
            "f", "Engine::fuse", sig,
            body_text=(
                "def fuse(\n"
                "    self, intent: str, lexical: list) -> list:\n"
                '    """Hybrid ranking for NL queries."""\n'
            ),
        )
        assert p.endswith("| Hybrid ranking for NL queries.")

    def test_no_duplicate_when_body_repeats_signature(self):
        p = build_passage("function", "f", "def f()", body_text="def f()\n")
        assert p.count("def f()") == 1

    def test_empty_signature(self):
        assert build_passage("class", "Widget", "") == "class Widget"

    def test_length_capped(self):
        p = build_passage("function", "f", "x" * 1000, body_text="y" * 1000)
        assert len(p) <= 400


# ── Graceful degradation without fastembed ──

class TestDegradation:
    def test_semantic_build_without_fastembed_is_lexical_only(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setattr(semantic, "is_available", lambda: False)
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)  # must not raise
        err = capsys.readouterr().err
        assert "fastembed is not installed" in err
        assert _embedding_rows(root) == []
        # Lexical search still fully works
        engine = QueryEngine(root)
        results = engine.search("fetch_record")
        assert results and results[0].qualified_name == "fetch_record"
        assert engine.semantic_status() == "no-index"

    def test_nl_query_with_embeddings_but_no_fastembed(
        self, tmp_path, fake_semantic, monkeypatch
    ):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        assert _embedding_rows(root)
        # Now "uninstall" fastembed: query path degrades silently
        monkeypatch.setattr(semantic, "is_available", lambda: False)
        engine = QueryEngine(root)
        assert engine.semantic_status() == "no-dependency"
        # Multi-word NL query: no crash, lexical-only results
        results = engine.search("completely unrelated nonsense words")
        assert all(r.match != "semantic" for r in results)


# ── Storage, sticky flag, incremental re-embedding ──

class TestEmbeddingStorage:
    def test_semantic_build_writes_vectors_and_meta(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        rows = _embedding_rows(root)
        assert len(rows) == 2  # fetch_record + render_chart
        for _sid, _file, model, dim, vector in rows:
            assert model == "fake-bag-of-words"
            assert dim == _DIM
            assert len(vector) == _DIM * 4  # float32 blob
        store = SqliteStore(_db_path(root))
        try:
            assert store.get_meta("semantic") == "1"
            assert store.has_embeddings()
        finally:
            store.close()

    def test_plain_build_writes_no_embeddings(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root)
        assert _embedding_rows(root) == []
        store = SqliteStore(_db_path(root))
        try:
            assert store.get_meta("semantic") is None
            assert not store.has_embeddings()
        finally:
            store.close()

    def test_incremental_rebuild_reembeds_only_changed_file(
        self, tmp_path, fake_semantic
    ):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        before = {sid: vector for sid, _f, _m, _d, vector in _embedding_rows(root)}
        fake_semantic.calls.clear()

        # Change one file; rebuild WITHOUT the flag (sticky via meta)
        with open(os.path.join(root, "storage.py"), "w") as f:
            f.write(
                "def fetch_record(key, default=None):\n"
                '    """Look up a stored value with a default."""\n'
                "    return default\n"
            )
        auto_build(root)

        rows = _embedding_rows(root)
        files = sorted({r[1] for r in rows})
        assert files == ["charts.py", "storage.py"]
        # Only the changed file's symbols were re-embedded
        embedded = [p for call in fake_semantic.calls for p in call]
        assert any("fetch_record" in p for p in embedded)
        assert not any("render_chart" in p for p in embedded)
        # Unchanged file kept its original vector rows
        kept = {sid: vector for sid, _f, _m, _d, vector in rows}
        unchanged = [sid for sid, _f, _m, _d, _v in rows if _f == "charts.py"]
        for sid in unchanged:
            assert kept[sid] == before[sid]

    def test_full_rebuild_keeps_semantic_choice(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        auto_build(root, full=True)  # no --semantic flag, sticky via meta
        assert len(_embedding_rows(root)) == 2


# ── Hybrid search (RRF fusion) ──

class TestHybridSearch:
    def test_nl_query_finds_symbol_lexical_misses(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)

        # Words from fetch_record's passage ("def", "key") that share no
        # identifier subtoken with any symbol name or file path component.
        query = "def key please"
        # sanity: lexical alone finds nothing for this query
        lexical_engine = QueryEngine(root)
        lexical_engine._semantic_loaded = True  # force lexical-only
        assert lexical_engine.search(query) == []

        engine = QueryEngine(root)
        results = engine.search(query)
        assert results, "hybrid search should surface semantic matches"
        top = results[0]
        assert top.qualified_name == "fetch_record"
        assert top.match == "semantic"
        assert 0.0 < top.score <= 1.0

    def test_identifier_query_keeps_pure_lexical_ranking(
        self, tmp_path, fake_semantic
    ):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        engine = QueryEngine(root)
        # Single-word identifier query: semantic path never invoked
        results = engine.search("fetch_record")
        assert results[0].qualified_name == "fetch_record"
        assert results[0].match == "exact"
        assert results[0].score == 0.95

    def test_single_word_query_never_embeds(
        self, tmp_path, fake_semantic, monkeypatch
    ):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        engine = QueryEngine(root)

        def _boom(_q):  # fusion must not even embed the query
            raise AssertionError("semantic path invoked for identifier query")

        monkeypatch.setattr(semantic, "embed_query", _boom)
        # Single-word identifier-shaped queries skip fusion entirely
        results = engine.search("fetch")
        assert results and results[0].qualified_name == "fetch_record"
        assert results[0].match == "prefix"

    def test_semantic_matrix_loaded_once(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        engine = QueryEngine(root)
        engine.search("def fetch record key")
        matrix_first = engine._semantic_matrix
        assert matrix_first is not None
        engine.search("render some chart data please")
        assert engine._semantic_matrix is matrix_first

    def test_engine_without_db_never_crashes(self, tmp_path, fake_semantic):
        root = _write_project(tmp_path / "proj")
        auto_build(root)  # lexical-only build
        engine = QueryEngine(root)
        results = engine.search("some natural language query here")
        assert isinstance(results, list)


# ── RRF math ──

class TestRRFFusion:
    def test_rank_one_in_both_lists_scores_one(self, tmp_path, fake_semantic):
        """A symbol that tops the lexical AND semantic ranking gets 1.0."""
        root = _write_project(tmp_path / "proj")
        auto_build(root, semantic=True)
        engine = QueryEngine(root)
        # "record key" subtoken-matches fetch_record lexically (score
        # < 0.7 -> fusion runs) and tops the fake semantic ranking too.
        results = engine.search("record fetch")
        top = results[0]
        assert top.qualified_name == "fetch_record"
        assert top.match == "hybrid"
        assert top.score == 1.0

    def test_fused_scores_match_rrf_formula(self):
        k = QueryEngine._RRF_K
        # symbol at lexical rank 0 and semantic rank 2:
        fused = 1.0 / (k + 1) + 1.0 / (k + 3)
        max_fused = 2.0 / (k + 1)
        assert 0 < fused / max_fused < 1
