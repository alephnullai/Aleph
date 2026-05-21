"""Query engine for Aleph project artifacts.

Implements the LLM interaction protocol commands:
  EXPAND  <symbol_id>  — return full body for a symbol
  RESOLVE <symbol_id>  — return dictionary entry (name, kind, file, signature)
  CALLERS <symbol_id>  — return all symbols that call this one
  CONTEXT <symbol_id>  — return symbol + its immediate call neighborhood
  SEARCH  <intent>     — return symbols semantically matching a search term

Operates against already-built project output (.aleph component files on disk).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from aleph.emit.loader import AlephLoader
from aleph.model.components import (
    ProjectDictComponent,
    ProjectStructComponent,
    ProjectSalienceComponent,
    ProjectSymbolEntry,
    BodiesComponent,
)


@dataclass
class ResolveResult:
    """Dictionary entry for a symbol."""
    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    scope: str
    file: str
    signature_hash: str
    project: str = ""

    def to_dict(self) -> dict:
        d = {
            "symbol_id": self.symbol_id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "scope": self.scope,
            "file": self.file,
            "signature_hash": self.signature_hash,
        }
        if self.project:
            d["project"] = self.project
        return d


@dataclass
class CallerEntry:
    """A symbol that calls the target."""
    caller_id: str
    caller_name: str
    caller_file: str
    target_id: str
    project: str = ""

    def to_dict(self) -> dict:
        d = {
            "caller_id": self.caller_id,
            "caller_name": self.caller_name,
            "caller_file": self.caller_file,
            "target_id": self.target_id,
        }
        if self.project:
            d["project"] = self.project
        return d


@dataclass
class ContextResult:
    """A symbol plus its immediate call neighborhood."""
    symbol: ResolveResult
    callers: list[CallerEntry] = field(default_factory=list)
    callees: list[ResolveResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol.to_dict(),
            "callers": [c.to_dict() for c in self.callers],
            "callees": [c.to_dict() for c in self.callees],
        }


@dataclass
class SearchResult:
    """A symbol matching a search query."""
    symbol_id: str
    qualified_name: str
    kind: str
    file: str
    score: float  # relevance score 0-1
    project: str = ""

    def to_dict(self) -> dict:
        d = {
            "symbol_id": self.symbol_id,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "file": self.file,
            "score": self.score,
        }
        if self.project:
            d["project"] = self.project
        return d


class QueryEngine:
    """Query interface over built .aleph project artifacts.

    Loads project-level component files from an output directory and
    answers EXPAND / RESOLVE / CALLERS / CONTEXT / SEARCH queries.
    """

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self._artifact_dir = self._resolve_artifact_dir(project_dir)
        self._loader = AlephLoader()
        self._dict: ProjectDictComponent | None = None
        self._struct: ProjectStructComponent | None = None
        self._salience: ProjectSalienceComponent | None = None
        # Cache: file path -> loaded BodiesComponent
        self._bodies_cache: dict[str, BodiesComponent] = {}
        # Index: symbol_id -> ProjectSymbolEntry (built lazily)
        self._symbol_index: dict[str, ProjectSymbolEntry] | None = None
        # Index: symbol_id -> list of caller symbol_ids
        self._callers_index: dict[str, list[str]] | None = None
        # Index: symbol_id -> list of callee symbol_ids
        self._callees_index: dict[str, list[str]] | None = None

    @staticmethod
    def _resolve_artifact_dir(project_dir: str) -> str:
        """Find the directory containing .aleph artifacts.

        Checks for a .aleph/ subdirectory first (new convention),
        falls back to project_dir itself (backward compat / explicit path).
        """
        aleph_subdir = os.path.join(project_dir, ".aleph")
        if os.path.isdir(aleph_subdir) and os.path.isfile(
            os.path.join(aleph_subdir, "project.aleph.dict")
        ):
            return aleph_subdir
        return project_dir

    def _load_dict(self) -> ProjectDictComponent:
        if self._dict is None:
            path = os.path.join(self._artifact_dir, "project.aleph.dict")
            with open(path, "r", encoding="utf-8") as f:
                self._dict = self._loader.deserialize_project_dict(f.read())
        return self._dict

    def _load_struct(self) -> ProjectStructComponent:
        if self._struct is None:
            path = os.path.join(self._artifact_dir, "project.aleph.struct")
            with open(path, "r", encoding="utf-8") as f:
                self._struct = self._loader.deserialize_project_struct(f.read())
        return self._struct

    def _load_salience(self) -> ProjectSalienceComponent:
        if self._salience is None:
            path = os.path.join(self._artifact_dir, "project.aleph.salience")
            with open(path, "r", encoding="utf-8") as f:
                self._salience = self._loader.deserialize_project_salience(f.read())
        return self._salience

    def _build_symbol_index(self) -> dict[str, ProjectSymbolEntry]:
        if self._symbol_index is None:
            d = self._load_dict()
            self._symbol_index = {entry.symbol_id: entry for entry in d.symbols}
        return self._symbol_index

    def _build_call_indexes(self) -> None:
        if self._callers_index is not None:
            return
        self._callers_index = {}
        self._callees_index = {}
        struct = self._load_struct()
        for xref in struct.cross_refs:
            self._callers_index.setdefault(xref.callee_id, []).append(xref.caller_id)
            self._callees_index.setdefault(xref.caller_id, []).append(xref.callee_id)

        # Also include within-file call edges from per-file index
        index_path = os.path.join(self._artifact_dir, ".aleph.index.json")
        if os.path.isfile(index_path):
            import json
            with open(index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
            for file_entry in index.get("files", {}).values():
                for caller, callee in file_entry.get("calls", []):
                    if caller not in self._callers_index.get(callee, []):
                        self._callers_index.setdefault(callee, []).append(caller)
                    if callee not in self._callees_index.get(caller, []):
                        self._callees_index.setdefault(caller, []).append(callee)

    def _load_bodies_for_file(self, source_file: str) -> BodiesComponent | None:
        """Load the .aleph.bodies file for a given source file."""
        if source_file in self._bodies_cache:
            return self._bodies_cache[source_file]

        # Try output_dir/<basename>.aleph.bodies
        basename = os.path.basename(source_file)
        bodies_path = os.path.join(self._artifact_dir, basename + ".aleph.bodies")
        if not os.path.isfile(bodies_path):
            # Try colocated with source
            bodies_path = source_file + ".aleph.bodies"
        if not os.path.isfile(bodies_path):
            return None

        with open(bodies_path, "r", encoding="utf-8") as f:
            component = self._loader.deserialize_bodies(f.read())
        self._bodies_cache[source_file] = component
        return component

    def _entry_to_resolve(self, entry: ProjectSymbolEntry) -> ResolveResult:
        return ResolveResult(
            symbol_id=entry.symbol_id,
            name=entry.name,
            qualified_name=entry.qualified_name,
            kind=entry.kind,
            scope=entry.scope,
            file=entry.file,
            signature_hash=entry.signature_hash,
        )

    # ── Public query methods ──

    def resolve(self, symbol_id: str) -> ResolveResult | None:
        """ALEPH:RESOLVE — return dictionary entry for a symbol."""
        idx = self._build_symbol_index()
        entry = idx.get(symbol_id)
        if entry is None:
            return None
        return self._entry_to_resolve(entry)

    def expand(self, symbol_id: str) -> str | None:
        """ALEPH:EXPAND — return full body for a symbol.

        Looks up the symbol's file in the dictionary, loads the corresponding
        .aleph.bodies file, and expands the requested symbol.
        """
        idx = self._build_symbol_index()
        entry = idx.get(symbol_id)
        if entry is None:
            return None

        bodies = self._load_bodies_for_file(entry.file)
        if bodies is None:
            return None

        expanded = self._loader.expand_bodies(bodies)
        return expanded.get(symbol_id)

    def callers(self, symbol_id: str) -> list[CallerEntry]:
        """ALEPH:CALLERS — return all symbols that call this one."""
        self._build_call_indexes()
        assert self._callers_index is not None
        idx = self._build_symbol_index()

        caller_ids = self._callers_index.get(symbol_id, [])
        results: list[CallerEntry] = []
        for cid in sorted(set(caller_ids)):
            entry = idx.get(cid)
            results.append(CallerEntry(
                caller_id=cid,
                caller_name=entry.qualified_name if entry else cid,
                caller_file=entry.file if entry else "",
                target_id=symbol_id,
            ))
        return results

    def transitive_callers(self, symbol_id: str, max_hops: int = 2) -> dict[str, int]:
        """BFS to find all callers up to max_hops away.

        Returns dict of {caller_symbol_id: distance_from_target}.
        """
        self._build_call_indexes()
        assert self._callers_index is not None

        visited: dict[str, int] = {}
        queue: list[tuple[str, int]] = [(symbol_id, 0)]

        while queue:
            current, distance = queue.pop(0)
            if current in visited:
                continue
            visited[current] = distance
            if distance < max_hops:
                for caller_id in self._callers_index.get(current, []):
                    if caller_id not in visited:
                        queue.append((caller_id, distance + 1))

        visited.pop(symbol_id, None)
        return visited

    def context(self, symbol_id: str) -> ContextResult | None:
        """ALEPH:CONTEXT — return symbol + its immediate call neighborhood."""
        resolved = self.resolve(symbol_id)
        if resolved is None:
            return None

        caller_entries = self.callers(symbol_id)

        self._build_call_indexes()
        assert self._callees_index is not None
        idx = self._build_symbol_index()
        callee_ids = self._callees_index.get(symbol_id, [])
        callees: list[ResolveResult] = []
        for cid in sorted(set(callee_ids)):
            entry = idx.get(cid)
            if entry:
                callees.append(self._entry_to_resolve(entry))
            else:
                callees.append(ResolveResult(
                    symbol_id=cid, name=cid, qualified_name=cid,
                    kind="", scope="", file="", signature_hash="",
                ))

        return ContextResult(
            symbol=resolved,
            callers=caller_entries,
            callees=callees,
        )

    def search(self, intent: str) -> list[SearchResult]:
        """ALEPH:SEARCH — return symbols semantically matching a search term.

        Performs case-insensitive matching against qualified names, symbol names,
        and kind prefixes. Scores results by match quality.
        """
        idx = self._build_symbol_index()
        intent_lower = intent.lower()
        tokens = intent_lower.split()
        results: list[SearchResult] = []

        for entry in idx.values():
            qname_lower = entry.qualified_name.lower()
            name_lower = entry.name.lower()

            # Exact match on symbol_id
            if intent_lower == entry.symbol_id.lower():
                results.append(SearchResult(
                    symbol_id=entry.symbol_id,
                    qualified_name=entry.qualified_name,
                    kind=entry.kind,
                    file=entry.file,
                    score=1.0,
                ))
                continue

            # Exact match on name or qualified_name
            if intent_lower == name_lower or intent_lower == qname_lower:
                results.append(SearchResult(
                    symbol_id=entry.symbol_id,
                    qualified_name=entry.qualified_name,
                    kind=entry.kind,
                    file=entry.file,
                    score=0.95,
                ))
                continue

            # Substring match
            if intent_lower in qname_lower or intent_lower in name_lower:
                # Score by coverage: how much of the name does the query cover?
                max_len = max(len(qname_lower), 1)
                coverage = len(intent_lower) / max_len
                score = 0.5 + 0.4 * min(coverage, 1.0)
                results.append(SearchResult(
                    symbol_id=entry.symbol_id,
                    qualified_name=entry.qualified_name,
                    kind=entry.kind,
                    file=entry.file,
                    score=round(score, 3),
                ))
                continue

            # Token-based match: all tokens must appear somewhere
            if tokens and all(t in qname_lower or t in name_lower for t in tokens):
                matched_chars = sum(len(t) for t in tokens)
                max_len = max(len(qname_lower), 1)
                score = 0.3 + 0.3 * min(matched_chars / max_len, 1.0)
                results.append(SearchResult(
                    symbol_id=entry.symbol_id,
                    qualified_name=entry.qualified_name,
                    kind=entry.kind,
                    file=entry.file,
                    score=round(score, 3),
                ))

        results.sort(key=lambda r: (-r.score, r.qualified_name))

        # Deduplicate: same qualified_name + kind → keep highest score
        seen_names: dict[tuple[str, str], SearchResult] = {}
        for r in results:
            key = (r.qualified_name, r.kind)
            if key not in seen_names or r.score > seen_names[key].score:
                seen_names[key] = r
        return sorted(seen_names.values(), key=lambda r: (-r.score, r.qualified_name))
