"""SQLite storage engine for Aleph artifacts.

One ``.aleph/aleph.db`` per project is the canonical store. The text
artifacts (project.aleph.*) become an export format regenerated from the
db (see aleph.store.export). The db replaces the monolithic JSON build
cache: incremental rebuilds are per-file DELETE+INSERT inside one
transaction instead of a wholesale rewrite, and function bodies live
ONLY here (fixing the per-file bodies nested/flat path bug — expand
reads from the db on any build, no ``--per-file`` required).

Schema (SCHEMA_VERSION bumps drop + recreate — the db is a cache of the
source tree, always regenerable):

  meta(key, value)           schema_version, aleph_version, root ('.'),
                             built_at, temporal_* — no absolute paths so
                             the db is committable/portable.
  files(...)                 one row per source file: relative POSIX
                             path, language, semantic hash, stat stamp
                             (mtime_ns/size/content_hash for the P1
                             stat-fast-path freshness check), token
                             stats, and per-file component JSON blobs
                             (call-edge metadata, temporal, tests,
                             intents, errors) used to reconstruct the
                             incremental build cache.
  symbols(...)               one row per symbol incl. signature text and
                             body_text. ``id`` is indexed but NOT unique:
                             6-hex content hashes do collide across files
                             in real projects (the Aleph self-index has
                             3 such collisions), matching the text
                             dictionary which also allows duplicates.
  call_edges(...)            kind='local' rows belong to a file (cascade
                             on rebuild); kind='cross' rows are derived
                             per build and carry denormalized file/name
                             columns for exact artifact export.
  salience/coverage/temporal derived per build (project-wide analyses
                             are recomputed from all files every build),
                             replaced wholesale — these are small rows,
                             unlike symbol bodies.
  embeddings(...)            optional semantic vectors (one row per
                             symbol; float32 blob + model/dim), written
                             only by builds with --semantic (the choice
                             is remembered in meta['semantic']). Rows
                             cascade with their file, so the per-file
                             DELETE+INSERT pattern keeps embeddings
                             incremental: only changed files' symbols
                             are re-embedded.

All writes happen inside a single transaction (SQLite is crash-safe).
WAL mode keeps concurrent reads (MCP server) working during rebuilds.
"""

from __future__ import annotations

import json
import os
import sqlite3

from aleph.project.cache import BuildCache, CachedFileResult, FileStamp
from aleph.model.components import (
    TemporalComponent, TestsComponent, IntentsComponent, ErrorsComponent,
)

SCHEMA_VERSION = "1"
DB_FILENAME = "aleph.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id                 INTEGER PRIMARY KEY,
    path               TEXT NOT NULL UNIQUE,
    lang               TEXT NOT NULL DEFAULT '',
    semantic_hash      TEXT NOT NULL DEFAULT '',
    mtime_ns           INTEGER NOT NULL DEFAULT 0,
    size               INTEGER NOT NULL DEFAULT 0,
    content_hash       TEXT NOT NULL DEFAULT '',
    original_tokens    INTEGER NOT NULL DEFAULT 0,
    compressed_tokens  INTEGER NOT NULL DEFAULT 0,
    reduction_percent  REAL NOT NULL DEFAULT 0,
    symbol_count       INTEGER NOT NULL DEFAULT 0,
    call_edge_count    INTEGER NOT NULL DEFAULT 0,
    call_edge_metadata TEXT,
    temporal_json      TEXT,
    tests_json         TEXT,
    intents_json       TEXT,
    errors_json        TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    id             TEXT NOT NULL,
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind           TEXT NOT NULL,
    scope          TEXT NOT NULL DEFAULT '',
    language       TEXT NOT NULL DEFAULT '',
    signature      TEXT NOT NULL DEFAULT '',
    span_start     INTEGER NOT NULL DEFAULT 0,
    span_start_col INTEGER NOT NULL DEFAULT 0,
    span_end       INTEGER NOT NULL DEFAULT 0,
    span_end_col   INTEGER NOT NULL DEFAULT 0,
    salience       REAL NOT NULL DEFAULT 0,
    body_level     TEXT NOT NULL DEFAULT 'OMIT',
    body_text      TEXT,
    parent         TEXT,
    calls          TEXT,
    called_by      TEXT,
    children       TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_id ON symbols(id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qname ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_id);

CREATE TABLE IF NOT EXISTS call_edges (
    caller_id   TEXT NOT NULL,
    callee_id   TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'local',
    resolved    INTEGER NOT NULL DEFAULT 1,
    file_id     INTEGER REFERENCES files(id) ON DELETE CASCADE,
    source_file TEXT,
    target_file TEXT,
    caller_name TEXT,
    callee_name TEXT
);
CREATE INDEX IF NOT EXISTS idx_edges_caller ON call_edges(caller_id);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON call_edges(callee_id);

CREATE TABLE IF NOT EXISTS salience (
    symbol_id         TEXT NOT NULL,
    qualified_name    TEXT NOT NULL DEFAULT '',
    file              TEXT NOT NULL DEFAULT '',
    score             REAL NOT NULL DEFAULT 0,
    local_fan_in      INTEGER NOT NULL DEFAULT 0,
    cross_file_fan_in INTEGER NOT NULL DEFAULT 0,
    total_fan_in      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol_id, file)
);

CREATE TABLE IF NOT EXISTS coverage (
    symbol_id      TEXT NOT NULL,
    qualified_name TEXT NOT NULL DEFAULT '',
    file           TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'none',
    test_count     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol_id, file)
);

CREATE TABLE IF NOT EXISTS temporal (
    symbol_id          TEXT NOT NULL,
    qualified_name     TEXT NOT NULL DEFAULT '',
    file               TEXT NOT NULL DEFAULT '',
    age_days           INTEGER NOT NULL DEFAULT 0,
    last_modified_days INTEGER NOT NULL DEFAULT 0,
    churn              INTEGER NOT NULL DEFAULT 0,
    churn_label        TEXT NOT NULL DEFAULT '',
    stability          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (symbol_id, file)
);

CREATE TABLE IF NOT EXISTS embeddings (
    symbol_id      TEXT NOT NULL,
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    qualified_name TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT '',
    file           TEXT NOT NULL DEFAULT '',
    vector         BLOB NOT NULL,
    model          TEXT NOT NULL DEFAULT '',
    dim            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_embeddings_file ON embeddings(file_id);
"""

_TABLES = (
    "meta", "files", "symbols", "call_edges", "salience", "coverage",
    "temporal", "embeddings",
)


def rel_posix(path: str, root: str) -> str:
    """Project-relative POSIX path (the canonical files.path key)."""
    return os.path.relpath(path, root).replace(os.sep, "/")


def abs_from_rel(rel_path: str, root: str) -> str:
    """Reverse of rel_posix: absolute OS path under root."""
    return os.path.normpath(os.path.join(root, rel_path.replace("/", os.sep)))


class SqliteStore:
    """Read/write access to one project's aleph.db."""

    def __init__(self, path: str, create: bool = False) -> None:
        self.path = path
        if create:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # check_same_thread=False: the MCP server queries from its worker
        # thread while the auto-rebuild thread writes via its own store.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Without a busy_timeout, a watch-rebuild writer and a concurrent
        # MCP reader fail INSTANTLY with "database is locked" instead of
        # waiting out the (brief) lock — WAL minimizes contention but
        # checkpoints and schema changes still take exclusive locks.
        # 5000ms matches null_memory's stores.
        self._conn.execute("PRAGMA busy_timeout=5000")
        if create:
            self._ensure_schema()

    def vacuum(self) -> None:
        """Reclaim freelist pages so the db file shrinks to its live data.

        Full rebuilds delete every prior row; without VACUUM the freed
        pages stay allocated and the file only ever grows. Cheap (~1.5s
        on a 590MB store) — callers run it after full rebuilds.
        """
        self._conn.execute("VACUUM")

    def close(self) -> None:
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self._conn.close()

    # ── Schema ──

    def _ensure_schema(self) -> None:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        if cur.fetchone() is not None:
            version = self.get_meta("schema_version")
            if version != SCHEMA_VERSION:
                # Old schema: the db is a regenerable cache — recreate.
                with self._conn:
                    for table in _TABLES:
                        self._conn.execute(f"DROP TABLE IF EXISTS {table}")
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def is_valid(self) -> bool:
        """True when the db has a usable schema of the current version."""
        try:
            cur = self._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            )
            row = cur.fetchone()
        except sqlite3.Error:
            return False
        return row is not None and row[0] == SCHEMA_VERSION

    # ── Meta ──

    def get_meta(self, key: str) -> str | None:
        try:
            cur = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        except sqlite3.Error:
            return None
        row = cur.fetchone()
        return row[0] if row else None

    # ── Build persistence ──

    def persist_build(
        self,
        root: str,
        result,
        prev_cache: BuildCache | None = None,
        embedder=None,
    ) -> dict:
        """Write a BuildResult into the db inside one transaction.

        Per-file data (files/symbols/local edges/component blobs) is
        replaced ONLY for files that were actually rebuilt — a reused
        file's CachedFileResult is the same object instance the builder
        copied over from ``prev_cache``, so identity tells us which rows
        to leave alone. Derived project-wide tables (cross edges,
        salience, coverage, temporal) are recomputed every build by the
        builder and replaced wholesale (small rows, no bodies).

        ``embedder`` (optional, see aleph.query.semantic.PassageEmbedder)
        enables the semantic index: in the same transaction, symbols of
        every file that has no embedding rows yet are embedded (changed
        files lose their rows via the file cascade, so this re-embeds
        exactly the rebuilt files — plus a one-time backfill when
        --semantic is first enabled on an existing db), and
        meta['semantic'] records the choice so incremental rebuilds keep
        it without re-passing the flag.

        Returns {"files_written": n, "files_removed": m,
        "symbols_embedded": k}.
        """
        from datetime import datetime, timezone
        from aleph.__version__ import __version__

        cache = result.cache
        if cache is None:
            cache = BuildCache(root=root)

        keep: dict[str, CachedFileResult] = {
            rel_posix(abs_path, root): cached
            for abs_path, cached in cache.files.items()
        }
        prev_by_rel: dict[str, CachedFileResult] = {}
        if prev_cache is not None:
            prev_by_rel = {
                rel_posix(abs_path, root): cached
                for abs_path, cached in prev_cache.files.items()
            }

        files_written = 0
        files_removed = 0
        with self._conn:
            existing = {
                row["path"]: row["id"]
                for row in self._conn.execute("SELECT id, path FROM files")
            }

            # Remove files that no longer exist in the build
            for path, file_id in existing.items():
                if path not in keep:
                    self._conn.execute("DELETE FROM files WHERE id=?", (file_id,))
                    files_removed += 1

            # Per-file DELETE+INSERT for rebuilt files only
            for path, cached in keep.items():
                reused = (
                    path in existing
                    and prev_by_rel.get(path) is cached
                )
                if reused:
                    continue
                if path in existing:
                    self._conn.execute("DELETE FROM files WHERE path=?", (path,))
                self._insert_file(path, cached)
                files_written += 1

            # Derived project-level data: replaced wholesale each build
            self._conn.execute("DELETE FROM call_edges WHERE kind='cross'")
            self._conn.executemany(
                "INSERT INTO call_edges (caller_id, callee_id, kind, resolved,"
                " file_id, source_file, target_file, caller_name, callee_name)"
                " VALUES (?, ?, 'cross', 1, NULL, ?, ?, ?, ?)",
                [
                    (x.caller_id, x.callee_id, x.source_file, x.target_file,
                     x.caller_name, x.callee_name)
                    for x in result.struct_component.cross_refs
                ],
            )

            self._conn.execute("DELETE FROM salience")
            self._conn.executemany(
                "INSERT OR REPLACE INTO salience VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (e.symbol_id, e.qualified_name, e.file, e.score,
                     e.local_fan_in, e.cross_file_fan_in, e.total_fan_in)
                    for e in result.salience_component.entries
                ],
            )

            self._conn.execute("DELETE FROM coverage")
            self._conn.executemany(
                "INSERT OR REPLACE INTO coverage VALUES (?, ?, ?, ?, ?)",
                [
                    (e.symbol_id, e.qualified_name, e.file, e.status, e.test_count)
                    for e in result.coverage_component.entries
                ],
            )

            self._conn.execute("DELETE FROM temporal")
            self._conn.executemany(
                "INSERT OR REPLACE INTO temporal VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (e.symbol_id, e.qualified_name, e.file, e.age_days,
                     e.last_modified_days, e.churn_count, e.churn_label,
                     e.stability)
                    for e in result.temporal_component.entries
                ],
            )

            symbols_embedded = 0
            if embedder is not None:
                symbols_embedded = self._embed_missing(embedder)

            temporal = result.temporal_component
            meta = {
                "schema_version": SCHEMA_VERSION,
                "aleph_version": __version__,
                # Relative root only — no absolute paths in the db.
                "root": ".",
                "built_at": datetime.now(timezone.utc).isoformat(),
                "temporal_computed_date": temporal.computed_date,
                "temporal_insufficient_history": (
                    "1" if temporal.insufficient_history else "0"
                ),
            }
            if embedder is not None:
                # Remember the choice: incremental rebuilds keep
                # embedding without `--semantic` being re-passed.
                meta["semantic"] = "1"
                meta["semantic_model"] = embedder.model
            self._conn.executemany(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                list(meta.items()),
            )

        return {
            "files_written": files_written,
            "files_removed": files_removed,
            "symbols_embedded": symbols_embedded,
        }

    def _embed_missing(self, embedder) -> int:
        """Embed symbols of every file that has no embedding rows yet.

        Runs inside the persist_build transaction. Changed files were
        DELETE+INSERTed (cascade dropped their old embeddings), so this
        is the incremental re-embed; on a db where --semantic was just
        enabled it backfills everything once.
        """
        from aleph.query.semantic import build_passage

        rows = self._conn.execute(
            "SELECT s.id, s.file_id, s.qualified_name, s.kind, s.signature,"
            " s.body_text, f.path"
            " FROM symbols s JOIN files f ON s.file_id = f.id"
            " WHERE s.file_id NOT IN (SELECT DISTINCT file_id FROM embeddings)"
            # Directives (kind='d': imports/exports) are not embedded — their
            # qualified names are whole import statements that token-match
            # nearly any natural-language query and polluted semantic top-K
            # (benchmark: 'import json' ranked #1 for a JSONL-logging query,
            # driving find-mode accuracy to 17%).
            " AND s.kind != 'd'"
            " ORDER BY f.path, s.rowid"
        ).fetchall()
        if not rows:
            return 0
        passages = [
            build_passage(r["kind"], r["qualified_name"], r["signature"],
                          r["body_text"] or "")
            for r in rows
        ]
        vectors = embedder.embed(passages)
        self._conn.executemany(
            "INSERT INTO embeddings (symbol_id, file_id, qualified_name,"
            " kind, file, vector, model, dim) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (r["id"], r["file_id"], r["qualified_name"], r["kind"],
                 r["path"], vec, embedder.model, embedder.dim)
                for r, vec in zip(rows, vectors)
            ],
        )
        return len(rows)

    def _insert_file(self, path: str, cached: CachedFileResult) -> None:
        cur = self._conn.execute(
            "INSERT INTO files (path, lang, semantic_hash, mtime_ns, size,"
            " content_hash, original_tokens, compressed_tokens,"
            " reduction_percent, symbol_count, call_edge_count,"
            " call_edge_metadata, temporal_json, tests_json, intents_json,"
            " errors_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                path,
                cached.language,
                cached.semantic_hash,
                cached.stamp.mtime_ns,
                cached.stamp.size,
                cached.stamp.content_hash,
                cached.original_tokens,
                cached.compressed_tokens,
                cached.token_reduction_percent,
                cached.symbols_extracted,
                cached.call_edges_count,
                json.dumps(cached.call_edge_metadata) if cached.call_edge_metadata else None,
                json.dumps(cached.temporal_component.to_dict()) if cached.temporal_component else None,
                json.dumps(cached.tests_component.to_dict()) if cached.tests_component else None,
                json.dumps(cached.intents_component.to_dict()) if cached.intents_component else None,
                json.dumps(cached.errors_component.to_dict()) if cached.errors_component else None,
            ),
        )
        file_id = cur.lastrowid

        self._conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind,"
            " scope, language, signature, span_start, span_start_col,"
            " span_end, span_end_col, salience, body_level, body_text,"
            " parent, calls, called_by, children)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    d["id"], file_id, d["name"], d["qualified_name"], d["kind"],
                    d.get("scope", ""), d.get("language", ""),
                    d.get("signature_text", ""),
                    d["span"][0], d["span"][1], d["span"][2], d["span"][3],
                    d.get("salience", 0.0), d.get("body_level", "OMIT"),
                    d.get("body_text", ""),
                    d.get("parent"),
                    json.dumps(d.get("calls", [])),
                    json.dumps(d.get("called_by", [])),
                    json.dumps(d.get("children", [])),
                )
                for d in cached.symbols_data
            ],
        )

        self._conn.executemany(
            "INSERT INTO call_edges (caller_id, callee_id, kind, resolved,"
            " file_id) VALUES (?, ?, 'local', 1, ?)",
            [(caller, callee, file_id) for caller, callee in cached.call_edges],
        )

    # ── Build cache reconstruction ──

    def load_build_cache(self, root: str) -> BuildCache:
        """Reconstruct the incremental BuildCache from the db.

        Keys are absolute source paths (what discover_source_files
        yields); db paths are relative POSIX, joined against ``root``.
        """
        cache = BuildCache(root=root)
        if not self.is_valid():
            return cache
        for row in self._conn.execute("SELECT * FROM files ORDER BY path"):
            abs_path = abs_from_rel(row["path"], root)
            cache.files[abs_path] = self._cached_file_result(row, abs_path)
        return cache

    def file_stamps(self, root: str) -> dict[str, FileStamp]:
        """Lightweight {abs_path: FileStamp} projection (staleness checks).

        One db read of the files table — no symbols, no bodies. Callers
        stat each file and only hash on a stat mismatch (FileStamp's
        matches_file fast path).
        """
        stamps: dict[str, FileStamp] = {}
        if not self.is_valid():
            return stamps
        for row in self._conn.execute(
            "SELECT path, mtime_ns, size, content_hash FROM files"
        ):
            stamps[abs_from_rel(row["path"], root)] = FileStamp(
                mtime_ns=row["mtime_ns"],
                size=row["size"],
                content_hash=row["content_hash"],
            )
        return stamps

    def _cached_file_result(self, row: sqlite3.Row, abs_path: str) -> CachedFileResult:
        file_id = row["id"]
        symbols_data = [
            self._symbol_row_to_dict(s, abs_path)
            for s in self._conn.execute(
                "SELECT * FROM symbols WHERE file_id=? ORDER BY rowid", (file_id,)
            )
        ]
        call_edges = [
            (e["caller_id"], e["callee_id"])
            for e in self._conn.execute(
                "SELECT caller_id, callee_id FROM call_edges"
                " WHERE file_id=? AND kind='local' ORDER BY rowid",
                (file_id,),
            )
        ]
        metadata = json.loads(row["call_edge_metadata"]) if row["call_edge_metadata"] else []
        return CachedFileResult(
            stamp=FileStamp(
                mtime_ns=row["mtime_ns"],
                size=row["size"],
                content_hash=row["content_hash"],
            ),
            language=row["lang"],
            semantic_hash=row["semantic_hash"],
            symbols_extracted=row["symbol_count"],
            call_edges_count=row["call_edge_count"],
            original_tokens=row["original_tokens"],
            compressed_tokens=row["compressed_tokens"],
            token_reduction_percent=row["reduction_percent"],
            symbols_data=symbols_data,
            call_edges=call_edges,
            call_edge_metadata=metadata,
            temporal_component=(
                TemporalComponent.from_dict(json.loads(row["temporal_json"]))
                if row["temporal_json"] else None
            ),
            tests_component=(
                TestsComponent.from_dict(json.loads(row["tests_json"]))
                if row["tests_json"] else None
            ),
            intents_component=(
                IntentsComponent.from_dict(json.loads(row["intents_json"]))
                if row["intents_json"] else None
            ),
            errors_component=(
                ErrorsComponent.from_dict(json.loads(row["errors_json"]))
                if row["errors_json"] else None
            ),
        )

    @staticmethod
    def _symbol_row_to_dict(s: sqlite3.Row, abs_path: str) -> dict:
        """Symbol row -> the serialize_symbol() dict format used by the cache."""
        return {
            "id": s["id"],
            "name": s["name"],
            "qualified_name": s["qualified_name"],
            "kind": s["kind"],
            "scope": s["scope"],
            "language": s["language"],
            # Not stored (no absolute paths in the db) — reconstructed
            # from the file row it belongs to.
            "source_file": abs_path,
            "signature_text": s["signature"],
            "body_text": s["body_text"] if s["body_text"] is not None else "",
            "span": [s["span_start"], s["span_start_col"],
                     s["span_end"], s["span_end_col"]],
            "salience": s["salience"],
            "body_level": s["body_level"],
            "calls": json.loads(s["calls"]) if s["calls"] else [],
            "called_by": json.loads(s["called_by"]) if s["called_by"] else [],
            "children": json.loads(s["children"]) if s["children"] else [],
            "parent": s["parent"],
        }

    # ── Query readers (used by QueryEngine) ──

    _ENTRY_SELECT = (
        "SELECT s.id, s.name, s.qualified_name, s.kind, s.scope, s.language,"
        " s.signature, s.span_start, s.span_end, f.path AS file_path,"
        " f.lang AS file_lang"
        " FROM symbols s JOIN files f ON s.file_id = f.id"
    )

    def get_symbol(self, symbol_id: str) -> sqlite3.Row | None:
        """Dictionary row for a symbol id.

        On (rare) cross-file id collisions, the text path's symbol index
        is built dict-style with last-wins over entries sorted by
        (file, id) — mirror that by taking the greatest file path.
        """
        cur = self._conn.execute(
            self._ENTRY_SELECT + " WHERE s.id=? ORDER BY f.path DESC LIMIT 1",
            (symbol_id,),
        )
        return cur.fetchone()

    def find_symbols(self, name: str, qualified: bool) -> list[sqlite3.Row]:
        col = "s.qualified_name" if qualified else "s.name"
        cur = self._conn.execute(
            self._ENTRY_SELECT + f" WHERE {col}=? ORDER BY f.path, s.id",
            (name,),
        )
        return list(cur.fetchall())

    def get_body(self, symbol_id: str) -> str | None:
        """Original body text for a symbol, or None when the id is unknown."""
        cur = self._conn.execute(
            "SELECT s.body_text FROM symbols s JOIN files f ON s.file_id=f.id"
            " WHERE s.id=? ORDER BY f.path DESC LIMIT 1",
            (symbol_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return row[0] if row[0] is not None else ""

    def symbol_projection(self) -> list[tuple]:
        """Lightweight (id, name, qualified_name, kind, file) projection.

        Ordered by (file, id) to match the text dictionary's entry order
        (search-result tie-breaking parity). No bodies, no signatures.
        """
        cur = self._conn.execute(
            "SELECT s.id, s.name, s.qualified_name, s.kind, f.path"
            " FROM symbols s JOIN files f ON s.file_id = f.id"
            " ORDER BY f.path, s.id"
        )
        return cur.fetchall()

    def has_embeddings(self) -> bool:
        """True when the optional semantic index has at least one vector.

        Guarded: dbs built before the embeddings table existed (schema
        unchanged — the table is additive) simply report False.
        """
        try:
            cur = self._conn.execute("SELECT 1 FROM embeddings LIMIT 1")
        except sqlite3.Error:
            return False
        return cur.fetchone() is not None

    def embedding_rows(self) -> list[sqlite3.Row]:
        """All embedding rows (symbol_id, qualified_name, kind, file,
        vector, model, dim) in (file, rowid) order. Empty when the
        semantic index was never built."""
        try:
            return list(self._conn.execute(
                "SELECT symbol_id, qualified_name, kind, file, vector,"
                " model, dim FROM embeddings ORDER BY file, rowid"
            ))
        except sqlite3.Error:
            return []

    def all_edges(self) -> list[tuple[str, str]]:
        """All (caller_id, callee_id) pairs — cross first, then local.

        Matches the text path which seeds the caller/callee indexes from
        project cross-refs and then the per-file index edges.
        """
        cur = self._conn.execute(
            "SELECT caller_id, callee_id FROM call_edges"
            " ORDER BY CASE kind WHEN 'cross' THEN 0 ELSE 1 END, rowid"
        )
        return cur.fetchall()

    # ── Export readers (used by aleph.store.export) ──

    def file_rows(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM files ORDER BY path"))

    def symbol_rows_for_file(self, file_id: int) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM symbols WHERE file_id=? ORDER BY rowid", (file_id,)
        ))

    def cross_edge_rows(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM call_edges WHERE kind='cross' ORDER BY rowid"
        ))

    def local_edges_for_file(self, file_id: int) -> list[tuple[str, str]]:
        return [
            (r[0], r[1]) for r in self._conn.execute(
                "SELECT caller_id, callee_id FROM call_edges"
                " WHERE file_id=? AND kind='local' ORDER BY rowid", (file_id,)
            )
        ]

    def salience_rows(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM salience"))

    def coverage_rows(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM coverage"))

    def temporal_rows(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM temporal"))


def open_store(artifact_dir: str) -> SqliteStore | None:
    """Open the project's aleph.db for reading, or None when absent/invalid."""
    path = os.path.join(artifact_dir, DB_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        store = SqliteStore(path)
    except sqlite3.Error:
        return None
    if not store.is_valid():
        store.close()
        return None
    return store
