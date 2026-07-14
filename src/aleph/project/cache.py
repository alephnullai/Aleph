"""Build cache for incremental recompilation (Phase 2.3).

Stores per-file pipeline results so unchanged files can be skipped on rebuild.
Change detection uses file mtime + size (fast) with content hash (accurate).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from aleph.model.symbol import Symbol, RawSymbol, SymbolID, Span
from aleph.model.enums import SymbolKind, BodyLevel
from aleph.model.components import (
    StructComponent, TemporalComponent, TestsComponent,
    IntentsComponent, ErrorsComponent,
)
from aleph.util.hashing import byte_hash


# 2.4: portable symbol-ID scheme v2 (root-relative paths) — old caches hold
# v1 absolute-path IDs and must not be mixed into a v2 build.
CACHE_VERSION = "2.4"
CACHE_FILENAME = ".aleph.build_cache.json"

# A stat fast-path match is only trusted when the file's mtime is at least
# this far in the past. Windows filesystem timestamps are coarse (writes
# within the same timer tick share an mtime_ns), so an edit made in the
# same instant as the recorded stamp can be invisible to stat — the same
# "racy timestamp" problem git's index solves the same way. Within the
# window we verify by content hash instead.
_RACY_MTIME_WINDOW_NS = 2_000_000_000  # 2 seconds


def _is_racy_mtime(mtime_ns: int, now_ns: int | None = None) -> bool:
    """True when *mtime_ns* is too close to now for stat to be trusted."""
    if now_ns is None:
        now_ns = time.time_ns()
    return now_ns - mtime_ns < _RACY_MTIME_WINDOW_NS


@dataclass
class FileStamp:
    """Fast change detection for a source file."""
    mtime_ns: int
    size: int
    content_hash: str

    def matches(self, other: FileStamp) -> bool:
        # Content hashes are authoritative when both sides carry one
        # (they are already computed — no extra I/O). Stat equality alone
        # is NOT sufficient: on Windows two writes within the same
        # filesystem timer tick produce identical mtime_ns.
        if self.content_hash and other.content_hash:
            return self.content_hash == other.content_hash
        # Stat-only stamps carry an empty hash — fall back to stat.
        return self.stat_matches(other)

    def stat_matches(self, other: FileStamp) -> bool:
        """Stat-only comparison (no content involved)."""
        return self.mtime_ns == other.mtime_ns and self.size == other.size

    def matches_file(self, path: str) -> bool:
        """Check this (cached) stamp against a file on disk.

        Fast path: when mtime_ns + size are unchanged the file is
        considered fresh WITHOUT reading it. Content is read and hashed
        on a stat mismatch (e.g. touch without edit, git checkout) — or
        when the mtime is so recent that coarse filesystem timestamps
        (Windows) could hide an edit made in the same tick as the stamp.
        """
        try:
            stat = os.stat(path)
        except OSError:
            return False
        stat_fresh = (
            stat.st_mtime_ns == self.mtime_ns and stat.st_size == self.size
        )
        if stat_fresh and not self.content_hash:
            # Stat-only stamp: stat is all we have to compare.
            return True
        if stat_fresh and not _is_racy_mtime(stat.st_mtime_ns):
            return True
        if not self.content_hash:
            return False
        return _hash_file(path) == self.content_hash

    def to_dict(self) -> dict:
        return {
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "content_hash": self.content_hash,
        }

    @staticmethod
    def from_dict(d: dict) -> FileStamp:
        return FileStamp(
            mtime_ns=d["mtime_ns"],
            size=d["size"],
            content_hash=d["content_hash"],
        )

    @staticmethod
    def from_file(path: str) -> FileStamp:
        stat = os.stat(path)
        return FileStamp(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            content_hash=_hash_file(path),
        )

    @staticmethod
    def from_stat(path: str) -> FileStamp:
        """Stat-only stamp (empty content hash) — never persist these."""
        stat = os.stat(path)
        return FileStamp(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            content_hash="",
        )


def _hash_file(path: str) -> str:
    with open(path, "rb") as f:
        content = f.read()
    return byte_hash(content.decode("utf-8", errors="replace"))


@dataclass
class CachedFileResult:
    """Serializable subset of a file's pipeline result needed by build_project."""
    stamp: FileStamp
    language: str
    semantic_hash: str
    symbols_extracted: int
    call_edges_count: int
    original_tokens: int
    compressed_tokens: int
    token_reduction_percent: float
    symbols_data: list[dict]  # serialized symbol data
    call_edges: list[tuple[str, str]]  # (caller_id, callee_id)
    call_edge_metadata: list[dict] = field(default_factory=list)
    temporal_component: TemporalComponent | None = None
    tests_component: TestsComponent | None = None
    intents_component: IntentsComponent | None = None
    errors_component: ErrorsComponent | None = None

    def to_dict(self) -> dict:
        return {
            "stamp": self.stamp.to_dict(),
            "language": self.language,
            "semantic_hash": self.semantic_hash,
            "symbols_extracted": self.symbols_extracted,
            "call_edges_count": self.call_edges_count,
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "token_reduction_percent": self.token_reduction_percent,
            "symbols_data": self.symbols_data,
            "call_edges": self.call_edges,
            "call_edge_metadata": self.call_edge_metadata,
            "temporal_component": (
                self.temporal_component.to_dict() if self.temporal_component else None
            ),
            "tests_component": (
                self.tests_component.to_dict() if self.tests_component else None
            ),
            "intents_component": (
                self.intents_component.to_dict() if self.intents_component else None
            ),
            "errors_component": (
                self.errors_component.to_dict() if self.errors_component else None
            ),
        }

    @staticmethod
    def from_dict(d: dict) -> CachedFileResult:
        # The component keys below are required: entries written by older
        # cache versions raise KeyError here, which BuildCache.from_dict
        # treats as a per-file cache miss (the file is simply rebuilt).
        return CachedFileResult(
            stamp=FileStamp.from_dict(d["stamp"]),
            language=d["language"],
            semantic_hash=d["semantic_hash"],
            symbols_extracted=d["symbols_extracted"],
            call_edges_count=d["call_edges_count"],
            original_tokens=d["original_tokens"],
            compressed_tokens=d["compressed_tokens"],
            token_reduction_percent=d["token_reduction_percent"],
            symbols_data=d["symbols_data"],
            call_edges=[tuple(e) for e in d["call_edges"]],
            call_edge_metadata=d.get("call_edge_metadata", []),
            temporal_component=(
                TemporalComponent.from_dict(d["temporal_component"])
                if d["temporal_component"] else None
            ),
            tests_component=(
                TestsComponent.from_dict(d["tests_component"])
                if d["tests_component"] else None
            ),
            intents_component=(
                IntentsComponent.from_dict(d["intents_component"])
                if d["intents_component"] else None
            ),
            errors_component=(
                ErrorsComponent.from_dict(d["errors_component"])
                if d["errors_component"] else None
            ),
        )


def serialize_symbol(sym: Symbol) -> dict:
    """Serialize a Symbol to a dict for cache storage."""
    return {
        "id": str(sym.id),
        "name": sym.raw.name,
        "qualified_name": sym.raw.qualified_name,
        "kind": sym.raw.kind.value,
        "scope": sym.raw.scope,
        "language": sym.raw.language,
        "source_file": sym.raw.source_file,
        "signature_text": sym.raw.signature_text,
        "body_text": sym.raw.body_text,
        "span": [sym.raw.span.start_line, sym.raw.span.start_col,
                 sym.raw.span.end_line, sym.raw.span.end_col],
        "salience": sym.salience,
        "body_level": sym.body_level.value,
        "calls": [str(c) for c in sym.calls],
        "called_by": [str(c) for c in sym.called_by],
        "children": [str(c) for c in sym.children],
        "parent": str(sym.parent) if sym.parent else None,
    }


def deserialize_symbol(d: dict) -> Symbol:
    """Reconstruct a Symbol from cached dict."""
    span_data = d["span"]
    raw = RawSymbol(
        name=d["name"],
        qualified_name=d["qualified_name"],
        kind=SymbolKind(d["kind"]),
        scope=d["scope"],
        span=Span(span_data[0], span_data[1], span_data[2], span_data[3]),
        language=d["language"],
        source_file=d.get("source_file", ""),
        body_text=d.get("body_text", ""),
        signature_text=d.get("signature_text", ""),
    )
    sym = Symbol(
        id=SymbolID.from_string(d["id"]),
        raw=raw,
        salience=d.get("salience", 0.0),
        body_level=BodyLevel(d.get("body_level", "OMIT")),
        calls=[SymbolID.from_string(c) for c in d.get("calls", [])],
        called_by=[SymbolID.from_string(c) for c in d.get("called_by", [])],
        children=[SymbolID.from_string(c) for c in d.get("children", [])],
        parent=SymbolID.from_string(d["parent"]) if d.get("parent") else None,
    )
    return sym


def cache_from_pipeline_result(source_file: str, result: dict) -> CachedFileResult:
    """Extract cacheable data from a pipeline result dict."""
    stamp = FileStamp.from_file(source_file)
    symbols_data = [serialize_symbol(sym) for sym in result["symbols"]]
    return CachedFileResult(
        stamp=stamp,
        language=result["language"],
        semantic_hash=result["semantic_hash"],
        symbols_extracted=result["symbols_extracted"],
        call_edges_count=result["call_edges"],
        original_tokens=result["original_tokens"],
        compressed_tokens=result["compressed_tokens"],
        token_reduction_percent=result["token_reduction_percent"],
        symbols_data=symbols_data,
        call_edges=result["struct_component"].call_edges,
        call_edge_metadata=result.get("call_edge_metadata", []),
        temporal_component=result.get("temporal_component"),
        tests_component=result.get("tests_component"),
        intents_component=result.get("intents_component"),
        errors_component=result.get("errors_component"),
    )


def reconstruct_build_result(cached: CachedFileResult, source_file: str) -> dict:
    """Reconstruct the minimal pipeline result dict needed by build_project."""
    symbols = [deserialize_symbol(d) for d in cached.symbols_data]
    struct_component = StructComponent(
        source_file=source_file,
        call_edges=cached.call_edges,
        call_edge_metadata=cached.call_edge_metadata,
    )
    return {
        "source_file": source_file,
        "language": cached.language,
        "semantic_hash": cached.semantic_hash,
        "symbols_extracted": cached.symbols_extracted,
        "call_edges": cached.call_edges_count,
        "original_tokens": cached.original_tokens,
        "compressed_tokens": cached.compressed_tokens,
        "token_reduction_percent": cached.token_reduction_percent,
        "symbols": symbols,
        "struct_component": struct_component,
        "call_edge_metadata": cached.call_edge_metadata,
        "temporal_component": cached.temporal_component,
        "tests_component": cached.tests_component,
        "intents_component": cached.intents_component,
        "errors_component": cached.errors_component,
    }


@dataclass
class BuildCache:
    """Persistent build cache for incremental recompilation."""
    version: str = CACHE_VERSION
    root: str = ""
    files: dict[str, CachedFileResult] = field(default_factory=dict)

    def get_cached(self, source_file: str) -> CachedFileResult | None:
        return self.files.get(source_file)

    def is_fresh(self, source_file: str) -> bool:
        """Check if cached result is still valid for the given file.

        Stat-only fast path: unchanged mtime+size skips reading/hashing
        the file content entirely.
        """
        cached = self.files.get(source_file)
        if cached is None:
            return False
        if not os.path.isfile(source_file):
            return False
        return cached.stamp.matches_file(source_file)

    def update(self, source_file: str, result: dict) -> None:
        """Store a pipeline result in the cache."""
        self.files[source_file] = cache_from_pipeline_result(source_file, result)

    def remove_stale(self, current_files: set[str]) -> list[str]:
        """Remove entries for files that no longer exist. Returns removed paths."""
        stale = [f for f in self.files if f not in current_files]
        for f in stale:
            del self.files[f]
        return stale

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "root": self.root,
            "files": {
                path: cached.to_dict()
                for path, cached in self.files.items()
            },
        }

    @staticmethod
    def from_dict(d: dict) -> BuildCache:
        if d.get("version") != CACHE_VERSION:
            return BuildCache()
        cache = BuildCache(
            version=d["version"],
            root=d.get("root", ""),
        )
        for path, file_data in d.get("files", {}).items():
            try:
                cache.files[path] = CachedFileResult.from_dict(file_data)
            except (KeyError, TypeError, ValueError):
                continue
        return cache


def load_build_cache(path: str) -> BuildCache:
    """Load build cache from disk. Returns empty cache if not found or invalid."""
    if not os.path.isfile(path):
        return BuildCache()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return BuildCache.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError):
        return BuildCache()


def save_build_cache(path: str, cache: BuildCache) -> None:
    """Save build cache to disk (compact JSON for speed)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache.to_dict(), f, separators=(",", ":"))
        f.write("\n")
