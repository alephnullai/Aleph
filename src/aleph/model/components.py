"""Aleph component containers for file-level output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from aleph.model.symbol import Symbol, SymbolID
from aleph.model.enums import BodyLevel, AttentionLevel


@dataclass
class SignatureEntry:
    """A function/type signature in the struct component."""
    symbol_id: SymbolID
    name: str
    qualified_name: str
    kind: str
    signature: str
    visibility: str = "public"
    return_type: str = ""
    params: list[str] = field(default_factory=list)


@dataclass
class HierarchyNode:
    """A node in the containment tree."""
    symbol_id: SymbolID
    children: list[HierarchyNode] = field(default_factory=list)


@dataclass
class StructComponent:
    """File-level .aleph.struct: signatures, hierarchy, local call graph."""
    source_file: str
    signatures: list[SignatureEntry] = field(default_factory=list)
    hierarchy: list[HierarchyNode] = field(default_factory=list)
    call_edges: list[tuple[str, str]] = field(default_factory=list)  # (caller_id, callee_id)
    call_edge_metadata: list[dict[str, str]] = field(default_factory=list)
    symbols: dict[str, Symbol] = field(default_factory=dict)  # id_str -> Symbol


@dataclass
class BodyEntry:
    """A single body entry in the bodies component."""
    symbol_id: SymbolID
    level: BodyLevel
    content: str  # Full body, summary text, or empty for OMIT
    original_body: str = ""  # Preserved for roundtrip expansion


@dataclass
class BodiesComponent:
    """File-level .aleph.bodies: compressed function bodies."""
    source_file: str
    entries: list[BodyEntry] = field(default_factory=list)
    symbol_dict: dict[str, str] = field(default_factory=dict)  # id_str -> qualified_name


# ── Temporal ──

@dataclass
class TemporalEntry:
    """Per-symbol temporal metadata from git history."""
    symbol_id: SymbolID
    age_days: int
    last_modified_days: int
    churn_count: int           # modifications in last 90 days
    stability: str             # StabilityClass value: stable/active/volatile

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "age_days": self.age_days,
            "last_modified_days": self.last_modified_days,
            "churn_count": self.churn_count,
            "stability": self.stability,
        }

    @staticmethod
    def from_dict(d: dict) -> TemporalEntry:
        return TemporalEntry(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            age_days=d["age_days"],
            last_modified_days=d["last_modified_days"],
            churn_count=d["churn_count"],
            stability=d["stability"],
        )


@dataclass
class TemporalComponent:
    """File-level temporal metadata."""
    source_file: str
    computed_date: str         # ISO date
    entries: list[TemporalEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "computed_date": self.computed_date,
            "entries": [e.to_dict() for e in self.entries],
        }

    @staticmethod
    def from_dict(d: dict) -> TemporalComponent:
        return TemporalComponent(
            source_file=d["source_file"],
            computed_date=d["computed_date"],
            entries=[TemporalEntry.from_dict(e) for e in d.get("entries", [])],
        )


# ── Intents ──

@dataclass
class IntentEntry:
    """An inferred or authored intent annotation."""
    symbol_id: SymbolID
    tag_type: str              # INTENT | PRECONDITION | POSTCONDITION | INVARIANT
    description: str
    confidence: str            # inferred:high | inferred:medium | authored

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "tag_type": self.tag_type,
            "description": self.description,
            "confidence": self.confidence,
        }

    @staticmethod
    def from_dict(d: dict) -> IntentEntry:
        return IntentEntry(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            tag_type=d["tag_type"],
            description=d["description"],
            confidence=d["confidence"],
        )


@dataclass
class IntentsComponent:
    """File-level intent annotations."""
    source_file: str
    entries: list[IntentEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "entries": [e.to_dict() for e in self.entries],
        }

    @staticmethod
    def from_dict(d: dict) -> IntentsComponent:
        return IntentsComponent(
            source_file=d["source_file"],
            entries=[IntentEntry.from_dict(e) for e in d.get("entries", [])],
        )


# ── Errors ──

@dataclass
class ErrorSource:
    """An error origination point."""
    symbol_id: SymbolID
    error_type: str            # e.g. "IoError", "ValueError"
    propagation: str           # "throws", "propagates via ?", "returns Err(...)"
    surfaces_at: str           # symbol_id str or "caller"

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "error_type": self.error_type,
            "propagation": self.propagation,
            "surfaces_at": self.surfaces_at,
        }

    @staticmethod
    def from_dict(d: dict) -> ErrorSource:
        return ErrorSource(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            error_type=d["error_type"],
            propagation=d["propagation"],
            surfaces_at=d["surfaces_at"],
        )


@dataclass
class ErrorBoundary:
    """An error handling boundary."""
    symbol_id: SymbolID
    catches: str
    recovery: str

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "catches": self.catches,
            "recovery": self.recovery,
        }

    @staticmethod
    def from_dict(d: dict) -> ErrorBoundary:
        return ErrorBoundary(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            catches=d["catches"],
            recovery=d["recovery"],
        )


@dataclass
class UnhandledError:
    """An unhandled error detected in analysis."""
    symbol_id: SymbolID
    error_type: str
    description: str

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "error_type": self.error_type,
            "description": self.description,
        }

    @staticmethod
    def from_dict(d: dict) -> UnhandledError:
        return UnhandledError(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            error_type=d["error_type"],
            description=d["description"],
        )


@dataclass
class ErrorsComponent:
    """File-level error flow analysis."""
    source_file: str
    sources: list[ErrorSource] = field(default_factory=list)
    boundaries: list[ErrorBoundary] = field(default_factory=list)
    unhandled: list[UnhandledError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "sources": [s.to_dict() for s in self.sources],
            "boundaries": [b.to_dict() for b in self.boundaries],
            "unhandled": [u.to_dict() for u in self.unhandled],
        }

    @staticmethod
    def from_dict(d: dict) -> ErrorsComponent:
        return ErrorsComponent(
            source_file=d["source_file"],
            sources=[ErrorSource.from_dict(s) for s in d.get("sources", [])],
            boundaries=[ErrorBoundary.from_dict(b) for b in d.get("boundaries", [])],
            unhandled=[UnhandledError.from_dict(u) for u in d.get("unhandled", [])],
        )


# ── Test Coverage ──

@dataclass
class CoverageEntry:
    """Coverage status for a symbol."""
    symbol_id: SymbolID
    status: str                # covered | partial | none
    test_ids: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)  # names of uncovered sub-items

    def to_dict(self) -> dict:
        return {
            "symbol_id": str(self.symbol_id),
            "status": self.status,
            "test_ids": list(self.test_ids),
            "uncovered": list(self.uncovered),
        }

    @staticmethod
    def from_dict(d: dict) -> CoverageEntry:
        return CoverageEntry(
            symbol_id=SymbolID.from_string(d["symbol_id"]),
            status=d["status"],
            test_ids=list(d.get("test_ids", [])),
            uncovered=list(d.get("uncovered", [])),
        )


@dataclass
class TestDetail:
    """Details about a test function."""
    test_id: SymbolID
    covers: list[str] = field(default_factory=list)  # symbol IDs
    behaviors: list[str] = field(default_factory=list)  # inferred from test name

    def to_dict(self) -> dict:
        return {
            "test_id": str(self.test_id),
            "covers": list(self.covers),
            "behaviors": list(self.behaviors),
        }

    @staticmethod
    def from_dict(d: dict) -> TestDetail:
        return TestDetail(
            test_id=SymbolID.from_string(d["test_id"]),
            covers=list(d.get("covers", [])),
            behaviors=list(d.get("behaviors", [])),
        )


@dataclass
class TestsComponent:
    """File-level test coverage mapping."""
    source_file: str
    coverage: list[CoverageEntry] = field(default_factory=list)
    test_details: list[TestDetail] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "coverage": [c.to_dict() for c in self.coverage],
            "test_details": [t.to_dict() for t in self.test_details],
        }

    @staticmethod
    def from_dict(d: dict) -> TestsComponent:
        return TestsComponent(
            source_file=d["source_file"],
            coverage=[CoverageEntry.from_dict(c) for c in d.get("coverage", [])],
            test_details=[TestDetail.from_dict(t) for t in d.get("test_details", [])],
        )


# ── Project-level components (Phase 2.1) ──

@dataclass
class ProjectFileEntry:
    """A single file in the project map."""
    path: str
    language: str
    semantic_hash: str
    symbol_count: int
    call_edge_count: int
    original_tokens: int
    compressed_tokens: int
    reduction_percent: float


@dataclass
class ProjectMapComponent:
    """project.aleph.map — manifest of all files with semantic hashes."""
    root: str
    files: list[ProjectFileEntry] = field(default_factory=list)


@dataclass
class ProjectSymbolEntry:
    """A symbol in the global dictionary with file provenance."""
    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    scope: str
    file: str
    signature_hash: str = ""
    # Recorded source span (1-based, inclusive). 0 = unknown (old artifacts).
    start_line: int = 0
    end_line: int = 0
    language: str = ""


@dataclass
class ProjectDictComponent:
    """project.aleph.dict — global symbol dictionary with file provenance."""
    root: str
    symbols: list[ProjectSymbolEntry] = field(default_factory=list)


@dataclass
class ProjectFSEntry:
    """A file entry in the filesystem layout."""
    path: str
    language: str
    symbol_count: int


@dataclass
class ProjectModuleDep:
    """A module-level dependency (file A imports/uses symbols from file B)."""
    source: str
    target: str
    symbol_count: int


@dataclass
class ProjectFSComponent:
    """project.aleph.fs — file system layout and module dependencies."""
    root: str
    files: list[ProjectFSEntry] = field(default_factory=list)
    module_deps: list[ProjectModuleDep] = field(default_factory=list)


@dataclass
class ProjectCrossRef:
    """A cross-file symbol reference."""
    caller_id: str
    callee_id: str
    source_file: str
    target_file: str
    caller_name: str = ""
    callee_name: str = ""


@dataclass
class ProjectFileDep:
    """File-level dependency with symbol count."""
    source: str
    target: str
    symbol_refs: int


@dataclass
class ProjectStructComponent:
    """project.aleph.struct — cross-file call graph and module dependency graph."""
    root: str
    cross_refs: list[ProjectCrossRef] = field(default_factory=list)
    file_deps: list[ProjectFileDep] = field(default_factory=list)


# ── Project-level salience + attention (Phase 2.2) ──

@dataclass
class ProjectSalienceEntry:
    """A symbol's project-wide salience score."""
    symbol_id: str
    qualified_name: str
    file: str
    score: float
    local_fan_in: int
    cross_file_fan_in: int
    total_fan_in: int


@dataclass
class ProjectSalienceComponent:
    """project.aleph.salience — project-wide salience scores."""
    root: str
    entries: list[ProjectSalienceEntry] = field(default_factory=list)


@dataclass
class ProjectAttentionEntry:
    """A symbol's attention level classification."""
    symbol_id: str
    qualified_name: str
    file: str
    level: AttentionLevel
    score: float


@dataclass
class ProjectAttentionComponent:
    """project.aleph.attention — attention budget for all symbols."""
    root: str
    entries: list[ProjectAttentionEntry] = field(default_factory=list)
    budget: dict[str, int] = field(default_factory=dict)  # level -> count


# ── Project-level temporal (Phase 2.5) ──

@dataclass
class ProjectTemporalEntry:
    """Per-symbol temporal metadata aggregated at project level."""
    symbol_id: str
    qualified_name: str
    file: str
    age_days: int
    last_modified_days: int
    churn_count: int
    churn_label: str           # low | medium | high
    stability: str             # stable | active | volatile


@dataclass
class ProjectTemporalComponent:
    """project.aleph.temporal — age and churn data per symbol from git history."""
    root: str
    computed_date: str
    entries: list[ProjectTemporalEntry] = field(default_factory=list)
    insufficient_history: bool = False


# ── Project-level coverage (Phase 2.6) ──

@dataclass
class ProjectCoverageEntry:
    """A symbol's coverage status at the project level."""
    symbol_id: str
    qualified_name: str
    file: str
    status: str            # covered | partial | none
    test_count: int = 0

@dataclass
class ProjectCoverageComponent:
    """project.aleph.coverage — aggregate test coverage across the project."""
    root: str
    symbols_total: int = 0
    covered: int = 0
    partial: int = 0
    none_count: int = 0
    entries: list[ProjectCoverageEntry] = field(default_factory=list)
