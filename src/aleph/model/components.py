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


@dataclass
class TemporalComponent:
    """File-level temporal metadata."""
    source_file: str
    computed_date: str         # ISO date
    entries: list[TemporalEntry] = field(default_factory=list)


# ── Intents ──

@dataclass
class IntentEntry:
    """An inferred or authored intent annotation."""
    symbol_id: SymbolID
    tag_type: str              # INTENT | PRECONDITION | POSTCONDITION | INVARIANT
    description: str
    confidence: str            # inferred:high | inferred:medium | authored


@dataclass
class IntentsComponent:
    """File-level intent annotations."""
    source_file: str
    entries: list[IntentEntry] = field(default_factory=list)


# ── Errors ──

@dataclass
class ErrorSource:
    """An error origination point."""
    symbol_id: SymbolID
    error_type: str            # e.g. "IoError", "ValueError"
    propagation: str           # "throws", "propagates via ?", "returns Err(...)"
    surfaces_at: str           # symbol_id str or "caller"


@dataclass
class ErrorBoundary:
    """An error handling boundary."""
    symbol_id: SymbolID
    catches: str
    recovery: str


@dataclass
class UnhandledError:
    """An unhandled error detected in analysis."""
    symbol_id: SymbolID
    error_type: str
    description: str


@dataclass
class ErrorsComponent:
    """File-level error flow analysis."""
    source_file: str
    sources: list[ErrorSource] = field(default_factory=list)
    boundaries: list[ErrorBoundary] = field(default_factory=list)
    unhandled: list[UnhandledError] = field(default_factory=list)


# ── Test Coverage ──

@dataclass
class CoverageEntry:
    """Coverage status for a symbol."""
    symbol_id: SymbolID
    status: str                # covered | partial | none
    test_ids: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)  # names of uncovered sub-items


@dataclass
class TestDetail:
    """Details about a test function."""
    test_id: SymbolID
    covers: list[str] = field(default_factory=list)  # symbol IDs
    behaviors: list[str] = field(default_factory=list)  # inferred from test name


@dataclass
class TestsComponent:
    """File-level test coverage mapping."""
    source_file: str
    coverage: list[CoverageEntry] = field(default_factory=list)
    test_details: list[TestDetail] = field(default_factory=list)


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
