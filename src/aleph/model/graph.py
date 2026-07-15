"""Semantic graph: the canonical representation of meaning in Aleph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aleph.model.symbol import SymbolID


@dataclass
class Edge:
    """A directed edge in the semantic graph."""
    source: SymbolID
    target: SymbolID
    kind: str  # "calls", "contains", "imports", "inherits", etc.


@dataclass
class SemanticGraph:
    """The Aleph graph: nodes (symbols), edges (relationships), labels, clusters."""
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)  # id_str -> properties
    edges: list[Edge] = field(default_factory=list)
    labels: dict[str, dict[str, str]] = field(default_factory=dict)  # id_str -> label dict
    clusters: dict[str, list[str]] = field(default_factory=dict)  # cluster_name -> [id_strs]

    def add_node(self, symbol_id: SymbolID, **properties: Any) -> None:
        self.nodes[str(symbol_id)] = properties

    def add_edge(self, source: SymbolID, target: SymbolID, kind: str) -> None:
        self.edges.append(Edge(source=source, target=target, kind=kind))

    def add_to_cluster(self, cluster: str, symbol_id: SymbolID) -> None:
        if cluster not in self.clusters:
            self.clusters[cluster] = []
        self.clusters[cluster].append(str(symbol_id))

    def get_edges_from(self, symbol_id: SymbolID) -> list[Edge]:
        sid = str(symbol_id)
        return [e for e in self.edges if str(e.source) == sid]

    def get_edges_to(self, symbol_id: SymbolID) -> list[Edge]:
        sid = str(symbol_id)
        return [e for e in self.edges if str(e.target) == sid]
