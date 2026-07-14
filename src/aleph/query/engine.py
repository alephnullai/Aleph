"""Query engine for Aleph project artifacts.

Implements the LLM interaction protocol commands:
  EXPAND  <symbol_id>  — return full body for a symbol
  RESOLVE <symbol_id>  — return dictionary entry (name, kind, file, signature)
  CALLERS <symbol_id>  — return all symbols that call this one
  CONTEXT <symbol_id>  — return symbol + its immediate call neighborhood
  SEARCH  <intent>     — return symbols matching a search term

Operates against already-built project output (.aleph component files on disk).

SEARCH is lexical (identifier subtokens + IDF + tiered scoring) and,
when the project was built with ``aleph build --semantic`` AND the
optional fastembed extra is installed, hybrid: natural-language queries
(multiple words, no exact identifier hit) additionally rank by embedding
cosine similarity and the two rankings are fused with Reciprocal Rank
Fusion (rank-based, unit-free: w/(K+rank+1), K=60). Identifier-shaped
queries (exact/prefix hits) never invoke the semantic path, so exact
matches always rank first. Semantic vectors are loaded lazily ONCE per
engine into an in-process float32 matrix and scored brute-force —
practical ceiling ~100k symbols (see aleph.query.semantic).
"""

from __future__ import annotations

import math
import os
import re
from collections import deque
from dataclasses import dataclass, field

from aleph.emit.loader import AlephLoader
from aleph.project.paths import resolve_artifact_dir
from aleph.symbols.id_migration import maybe_hint_migration
from aleph.store.sqlite_store import open_store
from aleph.util.hashing import byte_hash
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
    # Recorded source span (1-based, inclusive). 0 = unknown (old artifacts).
    start_line: int = 0
    end_line: int = 0
    language: str = ""

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
        if self.start_line > 0:
            d["start_line"] = self.start_line
            d["end_line"] = self.end_line
        if self.language:
            d["language"] = self.language
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
class SymbolRef:
    """Result of resolving a user-supplied reference (id OR name).

    The trust contract: any query surface that takes a ``symbol_id`` must
    first run the input through :meth:`QueryEngine.resolve_ref` so that a
    NAME never silently produces a downstream empty (e.g. 'no callers')
    as if it were a resolved id. Exactly one of the status branches holds:

      status == "id"        — input was already a known symbol id; ``entry``
                              is the resolved symbol (no rename needed).
      status == "resolved"  — input was a name matching exactly one symbol;
                              ``entry`` is it, ``note`` carries 'resolved
                              <name> -> <id>' for echoing to the user.
      status == "ambiguous" — input was a name matching several symbols;
                              ``candidates`` lists them, caller must NOT
                              pick silently — surface the list and ask for
                              an id.
      status == "not_found" — input matched no id and no name; this is a
                              'no such symbol' miss, distinct from 'symbol
                              exists but has zero callers/etc'.
    """
    status: str  # id | resolved | ambiguous | not_found
    query: str
    entry: "ResolveResult | None" = None
    candidates: list["ResolveResult"] = field(default_factory=list)
    note: str = ""

    @property
    def symbol_id(self) -> str | None:
        return self.entry.symbol_id if self.entry is not None else None


@dataclass
class SearchResult:
    """A symbol matching a search query."""
    symbol_id: str
    qualified_name: str
    kind: str
    file: str
    score: float  # relevance score 0-1
    project: str = ""
    # How the match was made: exact-id | exact | prefix | substring |
    # subtoken | path. Lets consumers (e.g. handle_brief) weigh weak
    # name-substring matches differently from identifier-token matches.
    match: str = ""

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
        if self.match:
            d["match"] = self.match
        return d


# ── Identifier tokenization (lexical search support) ──

# Within an alphanumeric word, split camelCase / PascalCase / acronym /
# digit boundaries:  parseHTTPResponse2JSON -> parse HTTP Response 2 JSON
_IDENT_PART_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z])"   # acronym followed by a capitalized word: HTTP|Response
    r"|[A-Z]+(?![a-z])"        # trailing/standalone acronym: JSON, XML
    r"|[A-Z][a-z]*"            # capitalized word: Response
    r"|[0-9]+"                 # digit runs
    r"|[a-z]+"                 # lowercase runs
)

_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")


def is_test_path(path: str) -> bool:
    """True when the file path looks like test code (tests/ dirs,
    test_*.py / *_test.py naming). Canonical helper shared by brief
    ranking (mcp.handlers) and hybrid search fusion."""
    p = path.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{p}" or p.startswith("tests/")
        or base.startswith("test_") or base.endswith("_test.py")
    )


def tokenize_identifier(text: str) -> list[str]:
    """Split an identifier/path/query into lowercase subtokens.

    Handles snake_case, camelCase, PascalCase, acronym runs, and digit
    boundaries. Non-alphanumeric characters (``.``, ``::``, ``/``, ``-``,
    whitespace) are treated as separators.

    >>> tokenize_identifier("parseHTTPResponse")
    ['parse', 'http', 'response']
    >>> tokenize_identifier("load_build_cache")
    ['load', 'build', 'cache']
    """
    tokens: list[str] = []
    for word in _NON_ALNUM_RE.split(text):
        if not word:
            continue
        for part in _IDENT_PART_RE.findall(word):
            tokens.append(part.lower())
    return tokens


@dataclass
class _SearchEntry:
    """Precomputed lexical-search data for one dictionary symbol."""
    entry: ProjectSymbolEntry
    name_lower: str
    qname_lower: str
    name_tokens: frozenset[str]
    path_tokens: frozenset[str]


class QueryEngine:
    """Query interface over built .aleph project artifacts.

    When the project has a SQLite store (.aleph/aleph.db) it is the
    canonical source: resolve/expand become indexed point queries,
    callers/callees load a lightweight edge projection, and search loads
    only the (id, name, qualified_name, kind, file) projection — never
    bodies or signatures. Without a db the engine falls back to the
    legacy text artifacts (project.aleph.* + .aleph.index.json).
    """

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self._artifact_dir = self._resolve_artifact_dir(project_dir)
        # One-line stderr hint when artifacts were built at a different
        # root (case/location change) or under the old symbol-ID scheme.
        maybe_hint_migration(project_dir, self._artifact_dir)
        self._loader = AlephLoader()
        # SQLite store (canonical when present); None -> text artifacts
        self._store = open_store(self._artifact_dir)
        # Per-id resolve cache for the db path
        self._db_entry_cache: dict[str, ProjectSymbolEntry | None] = {}
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
        # Lexical search index (built lazily from the dictionary)
        self._search_index: list[_SearchEntry] | None = None
        # Document frequency per subtoken (for rarity weighting)
        self._token_df: dict[str, int] = {}
        # Semantic index (optional): float32 matrix + row metadata,
        # loaded lazily ONCE per engine (bounded: ~1.5 KB/symbol).
        self._semantic_loaded = False
        self._semantic_matrix = None
        self._semantic_meta: list[tuple[str, str, str, str]] = []

    # Single source of truth: aleph.project.paths.resolve_artifact_dir
    _resolve_artifact_dir = staticmethod(resolve_artifact_dir)

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
            if self._store is not None:
                # DB-backed projects carry no text project.aleph.dict; build
                # the index from the lightweight projection instead of
                # raising FileNotFoundError (which crashed handle_impact on
                # every db-only project). Projection columns match the text
                # dict's id/name/qualified_name/kind/file for index lookups.
                self._symbol_index = {
                    sid: ProjectSymbolEntry(
                        symbol_id=sid, name=name, qualified_name=qname,
                        kind=kind, scope="", file=path,
                    )
                    for sid, name, qname, kind, path in self._store.symbol_projection()
                }
            else:
                d = self._load_dict()
                self._symbol_index = {entry.symbol_id: entry for entry in d.symbols}
        return self._symbol_index

    def _build_call_indexes(self) -> None:
        if self._callers_index is not None:
            return
        self._callers_index = {}
        self._callees_index = {}

        if self._store is not None:
            # Lightweight (caller_id, callee_id) projection — one
            # indexed read, no component parsing.
            for caller_id, callee_id in self._store.all_edges():
                if caller_id not in self._callers_index.get(callee_id, []):
                    self._callers_index.setdefault(callee_id, []).append(caller_id)
                if callee_id not in self._callees_index.get(caller_id, []):
                    self._callees_index.setdefault(caller_id, []).append(callee_id)
            return

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

    # ── SQLite-backed lookups ──

    @staticmethod
    def _row_to_entry(row) -> ProjectSymbolEntry:
        """DB symbol row -> dictionary entry (text-artifact parity).

        Matches the builder's dict construction: sig hash is the first
        8 chars of byte_hash(signature_text), spans become 1-based
        lines, and language falls back to the file's language.
        """
        sig = row["signature"]
        return ProjectSymbolEntry(
            symbol_id=row["id"],
            name=row["name"],
            qualified_name=row["qualified_name"],
            kind=row["kind"],
            scope=row["scope"],
            file=row["file_path"],
            signature_hash=byte_hash(sig)[:8] if sig else "",
            start_line=row["span_start"] + 1,
            end_line=row["span_end"] + 1,
            language=row["language"] or row["file_lang"],
        )

    def _lookup_entry(self, symbol_id: str) -> ProjectSymbolEntry | None:
        """Dictionary entry for one symbol id (db point query or index)."""
        if self._store is not None:
            if symbol_id in self._db_entry_cache:
                return self._db_entry_cache[symbol_id]
            row = self._store.get_symbol(symbol_id)
            entry = self._row_to_entry(row) if row is not None else None
            self._db_entry_cache[symbol_id] = entry
            return entry
        return self._build_symbol_index().get(symbol_id)

    def _entry_to_resolve(self, entry: ProjectSymbolEntry) -> ResolveResult:
        return ResolveResult(
            symbol_id=entry.symbol_id,
            name=entry.name,
            qualified_name=entry.qualified_name,
            kind=entry.kind,
            scope=entry.scope,
            file=entry.file,
            signature_hash=entry.signature_hash,
            start_line=entry.start_line,
            end_line=entry.end_line,
            language=entry.language,
        )

    # ── Public query methods ──

    def resolve(self, symbol_id: str) -> ResolveResult | None:
        """ALEPH:RESOLVE — return dictionary entry for a symbol."""
        entry = self._lookup_entry(symbol_id)
        if entry is None:
            return None
        return self._entry_to_resolve(entry)

    def find_by_name(self, name: str) -> list[ResolveResult]:
        """Return all dictionary entries whose name or qualified name
        exactly matches ``name`` (case-sensitive).

        Used to disambiguate symbols referenced by name instead of ID
        (e.g. patch targets): a qualified name match narrows duplicate
        plain names to one symbol.
        """
        if self._store is not None:
            # Text-path parity: the symbol index dedups by id, last
            # entry (by file, id order) winning the value slot.
            for qualified in (True, False):
                rows = self._store.find_symbols(name, qualified=qualified)
                by_id: dict[str, ProjectSymbolEntry] = {}
                for row in rows:
                    by_id[row["id"]] = self._row_to_entry(row)
                if by_id:
                    return [self._entry_to_resolve(e) for e in by_id.values()]
            return []
        idx = self._build_symbol_index()
        qualified = [e for e in idx.values() if e.qualified_name == name]
        if qualified:
            return [self._entry_to_resolve(e) for e in qualified]
        return [
            self._entry_to_resolve(e) for e in idx.values() if e.name == name
        ]

    # Symbol-id shape: single kind char + '_' + hex hash (e.g. f_16e7f0).
    # Used only to decide *lookup order* (id-first vs name-first); a string
    # that looks like an id but resolves to nothing still falls through to
    # name resolution, so this is a heuristic, never an authority.
    _ID_PREFIXES = ("f_", "t_", "v_", "c_", "m_", "d_", "s_")
    # Canonical id: one kind char + '_' + hex hash (f_16e7f0). A looser shape
    # (kind char + '_' + alnum, no whitespace) covers synthetic/edge-only ids
    # that appear in the call graph but not the dictionary (e.g. f_target).
    _ID_SHAPE_RE = re.compile(r"^[ftvcmds]_[0-9a-f]{6,}$")
    _ID_LOOSE_RE = re.compile(r"^[ftvcmds]_[0-9A-Za-z]+$")

    def looks_like_id(self, ref: str) -> bool:
        """True when ``ref`` has the canonical symbol-id shape (prefix + hex)."""
        return bool(self._ID_SHAPE_RE.match(ref.strip()))

    def _is_graph_node(self, ref: str) -> bool:
        """True when ``ref`` appears as a node in the call graph.

        Only consulted for id-like refs absent from the dictionary — never
        for plain names (a name is never equal to a symbol id), so this
        cannot reintroduce the silent-empty-by-name bug.
        """
        if not self._ID_LOOSE_RE.match(ref.strip()):
            return False
        self._build_call_indexes()
        return ref in (self._callers_index or {}) or ref in (self._callees_index or {})

    def resolve_ref(self, ref: str) -> SymbolRef:
        """Resolve a user-supplied reference (id OR name) to a SymbolRef.

        This is the trust primitive every symbol-id-taking surface routes
        through. It NEVER guesses and NEVER lets a name fall through to a
        downstream empty:

          * Known id            -> status="id".
          * Id-shaped but absent -> still try name resolution; if that also
            misses, status="not_found" (so 'f_deadbe' that doesn't exist is
            an honest miss, not a fake 'no callers').
          * Exactly one name match -> status="resolved" (+ rename note).
          * Multiple name matches  -> status="ambiguous" (+ candidate list);
            never silently picks one.
          * Zero matches           -> status="not_found".
        """
        ref = ref.strip()
        if not ref:
            return SymbolRef(status="not_found", query=ref)

        # Id-first when the input is id-shaped; otherwise resolve as id only
        # as a courtesy (cheap point lookup) before falling to name search.
        direct = self.resolve(ref)
        if direct is not None:
            return SymbolRef(status="id", query=ref, entry=direct)

        matches = self.find_by_name(ref)
        if not matches and self._is_graph_node(ref):
            # The id isn't in the dictionary but IS a node in the call graph
            # (legacy artifacts can carry edge-only ids). It's still a real,
            # explicit id reference — let callers/context operate on it rather
            # than reporting a false 'no such name'. Entry is a minimal stub.
            return SymbolRef(
                status="id", query=ref,
                entry=ResolveResult(
                    symbol_id=ref, name=ref, qualified_name=ref,
                    kind="", scope="", file="", signature_hash="",
                ),
            )
        if len(matches) == 1:
            m = matches[0]
            return SymbolRef(
                status="resolved", query=ref, entry=m,
                note=f"resolved {ref} -> {m.symbol_id}",
            )
        if len(matches) > 1:
            return SymbolRef(
                status="ambiguous", query=ref,
                candidates=sorted(matches, key=lambda r: (r.file, r.symbol_id)),
            )
        return SymbolRef(status="not_found", query=ref)

    def expand(self, symbol_id: str) -> str | None:
        """ALEPH:EXPAND — return full body for a symbol.

        With a SQLite store, bodies live in the db: every build (no
        --per-file needed, any directory depth) can expand. Otherwise
        looks up the symbol's file in the dictionary, loads the
        corresponding .aleph.bodies file, and expands the symbol.
        """
        if self._store is not None:
            return self._store.get_body(symbol_id)

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

        caller_ids = self._callers_index.get(symbol_id, [])
        results: list[CallerEntry] = []
        for cid in sorted(set(caller_ids)):
            entry = self._lookup_entry(cid)
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
        queue: deque[tuple[str, int]] = deque([(symbol_id, 0)])

        while queue:
            current, distance = queue.popleft()
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
        callee_ids = self._callees_index.get(symbol_id, [])
        callees: list[ResolveResult] = []
        for cid in sorted(set(callee_ids)):
            entry = self._lookup_entry(cid)
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

    # ── Lexical search ──

    # Subtoken-only matches below this fraction of the query's rarity
    # weight are noise (e.g. matching one very common word of a 4-word
    # query) and are not returned.
    _MIN_TOKEN_FRACTION = 0.3
    # Path-component matches count at a discount vs name-token matches.
    _PATH_TOKEN_WEIGHT = 0.6

    def _search_entries(self) -> list[ProjectSymbolEntry]:
        """Dictionary entries for the search index.

        DB path: the lightweight (id, name, qualified_name, kind, file)
        projection in (file, id) order — no bodies/signatures loaded,
        matching the text dictionary's entry order for tie-breaking.
        """
        if self._store is not None:
            return [
                ProjectSymbolEntry(
                    symbol_id=sid, name=name, qualified_name=qname,
                    kind=kind, scope="", file=path,
                )
                for sid, name, qname, kind, path in self._store.symbol_projection()
            ]
        return self._load_dict().symbols

    def _build_search_index(self) -> list[_SearchEntry]:
        """Build the lexical search index: per-symbol subtoken sets + DF."""
        if self._search_index is not None:
            return self._search_index

        index: list[_SearchEntry] = []
        df: dict[str, int] = {}
        for entry in self._search_entries():
            name_tokens = frozenset(
                tokenize_identifier(entry.qualified_name)
                + tokenize_identifier(entry.name)
            )
            path_tokens = frozenset(tokenize_identifier(entry.file))
            index.append(_SearchEntry(
                entry=entry,
                name_lower=entry.name.lower(),
                qname_lower=entry.qualified_name.lower(),
                name_tokens=name_tokens,
                path_tokens=path_tokens,
            ))
            for t in name_tokens | path_tokens:
                df[t] = df.get(t, 0) + 1

        self._search_index = index
        self._token_df = df
        return index

    def _idf(self, token: str) -> float:
        """BM25-style inverse document frequency for a subtoken.

        A token matching few symbols scores higher than one matching
        thousands; unseen tokens get the maximum weight.
        """
        n = len(self._search_index or [])
        df = self._token_df.get(token, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    @staticmethod
    def _token_variants(token: str) -> frozenset[str]:
        """A query subtoken plus its simple singular/plural inflections.

        Natural-language task descriptions use plurals ("mtimes", "paths",
        "queries") where identifiers use singular subtokens ("mtime",
        "path", "query") — and vice versa. Without this, a task query like
        "detect racy mtimes" fails to match _is_racy_mtime at all even
        though "racy mtime" ranks it first. Only cheap inflections are
        generated; derivational forms (verification/verify) are left to
        the semantic index.
        """
        variants = {token}
        if len(token) > 4 and token.endswith("ies"):
            variants.add(token[:-3] + "y")
        if len(token) > 4 and token.endswith("es"):
            variants.add(token[:-2])
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            variants.add(token[:-1])
        else:
            variants.add(token + "s")
        return frozenset(variants)

    def _variant_idf(self, variants: frozenset[str]) -> float:
        """IDF for a query token, using its most common indexed inflection.

        The rarity weight must reflect the vocabulary actually present in
        the corpus: "paths" may be unseen while "path" is common — pricing
        the token at the unseen-maximum would let one inflected word drown
        the rest of the query.
        """
        best = max(variants, key=lambda v: self._token_df.get(v, 0))
        return self._idf(best)

    def search(self, intent: str) -> list[SearchResult]:
        """ALEPH:SEARCH — lexical identifier search over the symbol dictionary.

        Matching (best signal wins, exact > prefix > subtoken):
          1.0   exact symbol_id
          0.95  exact name or qualified name (case-insensitive)
          0.7+  name prefix
          ≤0.68 identifier subtokens (camelCase/snake_case-aware), weighted
                by rarity (BM25-style IDF) — rare tokens outrank common ones.
                File path components also match, at a discount.
          0.5+  raw substring of the name/qualified name
        """
        if self._store is None:
            self._build_symbol_index()
        index = self._build_search_index()
        intent_lower = intent.strip().lower()
        if not intent_lower:
            return []
        # Deduplicated query subtokens, preserving order; each token
        # matches via its inflection variants (paths <-> path).
        query_tokens = list(dict.fromkeys(tokenize_identifier(intent)))
        token_variants = {t: self._token_variants(t) for t in query_tokens}
        token_weights = {t: self._variant_idf(token_variants[t]) for t in query_tokens}
        total_weight = sum(token_weights.values())

        results: list[SearchResult] = []
        for se in index:
            entry = se.entry
            score = 0.0
            match = ""

            if intent_lower == entry.symbol_id.lower():
                score, match = 1.0, "exact-id"
            elif intent_lower == se.name_lower or intent_lower == se.qname_lower:
                score, match = 0.95, "exact"
            else:
                # Prefix of the symbol name
                if se.name_lower.startswith(intent_lower):
                    coverage = len(intent_lower) / max(len(se.name_lower), 1)
                    score, match = 0.7 + 0.2 * coverage, "prefix"
                elif intent_lower in se.qname_lower or intent_lower in se.name_lower:
                    coverage = len(intent_lower) / max(len(se.qname_lower), 1)
                    score, match = 0.5 + 0.15 * min(coverage, 1.0), "substring"

                # Subtoken match (rarity-weighted); may beat a weak substring
                if total_weight > 0:
                    matched_weight = 0.0
                    name_hit = False
                    for t, w in token_weights.items():
                        variants = token_variants[t]
                        if not variants.isdisjoint(se.name_tokens):
                            matched_weight += w
                            name_hit = True
                        elif not variants.isdisjoint(se.path_tokens):
                            matched_weight += self._PATH_TOKEN_WEIGHT * w
                    if matched_weight > 0:
                        fraction = matched_weight / total_weight
                        if fraction >= self._MIN_TOKEN_FRACTION:
                            token_score = 0.25 + 0.43 * fraction
                            if token_score > score:
                                score = token_score
                                match = "subtoken" if name_hit else "path"

            if score > 0:
                results.append(SearchResult(
                    symbol_id=entry.symbol_id,
                    qualified_name=entry.qualified_name,
                    kind=entry.kind,
                    file=entry.file,
                    score=round(score, 3),
                    match=match,
                ))

        results.sort(key=lambda r: (-r.score, r.qualified_name))

        # Deduplicate: same qualified_name + kind → keep highest score
        seen_names: dict[tuple[str, str], SearchResult] = {}
        for r in results:
            key = (r.qualified_name, r.kind)
            if key not in seen_names or r.score > seen_names[key].score:
                seen_names[key] = r
        lexical = sorted(
            seen_names.values(), key=lambda r: (-r.score, r.qualified_name)
        )

        fused = self._maybe_semantic_fuse(intent, lexical)
        return fused if fused is not None else lexical

    def search_nearest(self, intent: str, limit: int = 3) -> list[SearchResult]:
        """Best-effort fallback candidates when :meth:`search` dead-ends.

        SEARCH must never bare-empty on a reasonable query. When the
        scored path returns nothing, this looser pass relaxes every gate:

          * each query subtoken (and its inflections) is matched against
            name AND path subtokens with NO _MIN_TOKEN_FRACTION floor —
            so a single shared token still surfaces a candidate;
          * if still nothing, a raw substring scan over qualified names
            catches partial-identifier typing (e.g. 'tokeniz').

        Ranked by fraction of query tokens matched, then name. Returns at
        most ``limit`` — purely advisory ('nearest: a, b'), so callers can
        turn a blank into an actionable miss. Never raises.
        """
        if self._store is None:
            self._build_symbol_index()
        index = self._build_search_index()
        query_tokens = list(dict.fromkeys(tokenize_identifier(intent)))
        token_variants = {t: self._token_variants(t) for t in query_tokens}

        scored: list[tuple[float, SearchResult]] = []
        for se in index:
            if se.entry.kind == "d":
                continue  # import/export directives are never useful hints
            hit = 0.0
            for t in query_tokens:
                variants = token_variants[t]
                if not variants.isdisjoint(se.name_tokens):
                    hit += 1.0
                elif not variants.isdisjoint(se.path_tokens):
                    hit += self._PATH_TOKEN_WEIGHT
            if hit > 0:
                frac = hit / max(len(query_tokens), 1)
                scored.append((frac, SearchResult(
                    symbol_id=se.entry.symbol_id,
                    qualified_name=se.entry.qualified_name,
                    kind=se.entry.kind, file=se.entry.file,
                    score=round(min(frac, 0.99), 3), match="nearest",
                )))

        if not scored:
            # Raw substring of a contiguous query word against names.
            needle = intent.strip().lower()
            words = [w for w in re.split(r"\s+", needle) if len(w) >= 3]
            probes = words or ([needle] if len(needle) >= 3 else [])
            for se in index:
                if se.entry.kind == "d":
                    continue
                if any(p in se.qname_lower or p in se.name_lower for p in probes):
                    scored.append((0.1, SearchResult(
                        symbol_id=se.entry.symbol_id,
                        qualified_name=se.entry.qualified_name,
                        kind=se.entry.kind, file=se.entry.file,
                        score=0.1, match="nearest",
                    )))

        scored.sort(key=lambda x: (-x[0], x[1].qualified_name))
        # Dedup by qualified name.
        seen: set[str] = set()
        out: list[SearchResult] = []
        for _, r in scored:
            if r.qualified_name in seen:
                continue
            seen.add(r.qualified_name)
            out.append(r)
            if len(out) >= limit:
                break
        return out

    # ── Optional semantic (hybrid) search ──

    # Lexical score at/above which the query is considered
    # identifier-shaped (exact-id/exact/prefix tiers all score >= 0.7):
    # exact and prefix matches always win, no semantic fusion.
    _SEMANTIC_FLOOR = 0.7
    # Reciprocal Rank Fusion constant (standard K=60) and weights.
    _RRF_K = 60
    _RRF_LEXICAL_WEIGHT = 1.0
    _RRF_SEMANTIC_WEIGHT = 1.0
    # Semantic candidates considered for fusion per query.
    _SEMANTIC_TOP_K = 50
    # Candidates below this cosine similarity never enter fusion.
    # bge embeddings rarely score below ~0.4 even for unrelated text,
    # so this only drops truly orthogonal vectors.
    _SEMANTIC_MIN_SIM = 0.1
    # Test-file symbols are discounted in the fused (NL) ranking unless
    # the query itself is about tests: natural-language behavior queries
    # want the implementation, but test names echo behavior vocabulary
    # verbatim ("test_callers_capped_at_50" vs _cap_output) and monopolize
    # semantic top-K. Same principle as the brief layer's 0.5x discount
    # (d969be5), applied here at the fusion stage so every search consumer
    # benefits. Identifier-shaped (non-fused) queries are never discounted
    # — searching an exact test name still ranks it first.
    _FUSED_TEST_DISCOUNT = 0.5
    _TESTS_QUERY_RE = re.compile(
        r"\b(tests?|fixtures?|coverage|pytest)\b", re.IGNORECASE
    )

    def semantic_status(self) -> str:
        """Availability of the semantic index for this project.

        'ok'            — embeddings exist and fastembed is importable
        'no-index'      — project was not built with `aleph build --semantic`
        'no-dependency' — index exists but the optional fastembed extra
                          is not installed (lexical-only degradation)
        """
        if self._store is None or not self._store.has_embeddings():
            return "no-index"
        from aleph.query import semantic
        if not semantic.is_available():
            return "no-dependency"
        return "ok"

    def _maybe_semantic_fuse(
        self, intent: str, lexical: list[SearchResult]
    ) -> list[SearchResult] | None:
        """Hybrid ranking for natural-language-shaped queries.

        Triggers only when the query has multiple words AND no lexical
        hit reached the exact/prefix tier (identifier-shaped queries
        keep pure lexical ranking) AND the project has embeddings AND
        fastembed is importable. Lexical and semantic rankings are
        fused with Reciprocal Rank Fusion: w/(K+rank+1), K=60 — rank
        based, so the unit-free lexical scores and cosine similarities
        never need calibrating against each other. Fused scores are
        normalized so a symbol ranked #1 in both lists scores 1.0.

        Returns None when the semantic path does not apply (caller
        falls back to pure lexical results).
        """
        if len(intent.split()) < 2:
            return None
        if lexical and lexical[0].score >= self._SEMANTIC_FLOOR:
            return None
        semantic_ranking = self._semantic_rank(intent)
        if not semantic_ranking:
            return None

        k = self._RRF_K
        fused: dict[str, list] = {}  # symbol_id -> [score, SearchResult, in_both]
        rank = 0
        for r in lexical:
            # Directives (imports/exports) are excluded from the fused NL
            # ranking, mirroring their exclusion from the semantic index:
            # their qualified names are whole import/export statements that
            # subtoken-match almost any natural-language query (and the
            # multi-line ones bloat tool output by kilobytes per hit).
            if r.kind == "d":
                continue
            fused[r.symbol_id] = [
                self._RRF_LEXICAL_WEIGHT / (k + rank + 1), r, False,
            ]
            rank += 1
        for rank, (sid, qname, kind, file) in enumerate(semantic_ranking):
            contrib = self._RRF_SEMANTIC_WEIGHT / (k + rank + 1)
            if sid in fused:
                fused[sid][0] += contrib
                fused[sid][2] = True
            else:
                fused[sid] = [
                    contrib,
                    SearchResult(
                        symbol_id=sid, qualified_name=qname, kind=kind,
                        file=file, score=0.0, match="semantic",
                    ),
                    False,
                ]

        # Normalize: rank #1 in both lists -> 1.0 (keeps downstream
        # consumers' 0-1 score expectations, e.g. brief's floor).
        max_fused = (self._RRF_LEXICAL_WEIGHT + self._RRF_SEMANTIC_WEIGHT) / (k + 1)
        discount_tests = not self._TESTS_QUERY_RE.search(intent)
        results: list[SearchResult] = []
        for score, r, in_both in fused.values():
            final = score / max_fused
            if discount_tests and is_test_path(r.file):
                final *= self._FUSED_TEST_DISCOUNT
            results.append(SearchResult(
                symbol_id=r.symbol_id,
                qualified_name=r.qualified_name,
                kind=r.kind,
                file=r.file,
                score=round(final, 3),
                project=r.project,
                match="hybrid" if in_both else r.match,
            ))
        results.sort(key=lambda r: (-r.score, r.qualified_name))

        # Deduplicate again (semantic rows may alias lexical entries
        # under a different symbol_id for the same qualified name).
        seen: dict[tuple[str, str], SearchResult] = {}
        for r in results:
            key = (r.qualified_name, r.kind)
            if key not in seen or r.score > seen[key].score:
                seen[key] = r
        return sorted(seen.values(), key=lambda r: (-r.score, r.qualified_name))

    def _load_semantic(self) -> None:
        """Lazy-load the embedding matrix ONCE per engine.

        Vectors come from the store's embeddings table and are packed
        into one unit-normalized float32 matrix (~1.5 KB/symbol —
        brute-force cosine is the ranking, so ~100k symbols is the
        practical ceiling, see aleph.query.semantic). Any failure
        (no db, no table, no numpy, corrupt blobs) leaves the matrix
        None and search stays lexical-only.
        """
        if self._semantic_loaded:
            return
        self._semantic_loaded = True
        if self._store is None:
            return
        rows = self._store.embedding_rows()
        if not rows:
            return
        try:
            import numpy as np
        except ImportError:
            return
        dim = rows[0]["dim"] or 0
        if dim <= 0:
            return
        vecs = []
        meta: list[tuple[str, str, str, str]] = []
        for row in rows:
            vec = np.frombuffer(row["vector"], dtype=np.float32)
            if vec.shape[0] != dim:
                continue  # skip rows from a different/older model
            vecs.append(vec)
            meta.append((
                row["symbol_id"], row["qualified_name"],
                row["kind"], row["file"],
            ))
        if not vecs:
            return
        matrix = np.vstack(vecs)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._semantic_matrix = matrix / norms
        self._semantic_meta = meta

    def _semantic_rank(self, query: str) -> list[tuple[str, str, str, str]]:
        """Top-K symbols by embedding cosine similarity to the query.

        Returns [] whenever the semantic path is unavailable (no index,
        fastembed not installed, embedding failure) — callers then fall
        back to lexical-only. Never raises.
        """
        from aleph.query import semantic
        if not semantic.is_available():
            return []
        self._load_semantic()
        if self._semantic_matrix is None:
            return []
        try:
            qvec = semantic.embed_query(query)
        except Exception:
            return []
        sims = self._semantic_matrix @ qvec
        top = sims.argsort()[::-1][: self._SEMANTIC_TOP_K]
        return [
            self._semantic_meta[i]
            for i in top
            if sims[i] >= self._SEMANTIC_MIN_SIM
        ]

    def has_body(self, symbol_id: str) -> bool:
        """True when a per-file bodies artifact contains this symbol.

        Used to decide whether ALEPH:EXPAND can actually serve a body
        (per-file builds only) before recommending it.
        """
        try:
            return self.expand(symbol_id) is not None
        except (FileNotFoundError, OSError):
            return False
