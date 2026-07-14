"""Call graph builder: local call graph from AST function call nodes."""

from __future__ import annotations

from tree_sitter import Node, Tree

from aleph.model.symbol import Symbol
from aleph.ingest.node_types import CALL_NODE_TYPES


class CallGraphBuilder:
    """Builds a local call graph from function call AST nodes."""

    def build(self, tree: Tree, source_bytes: bytes, language: str,
              symbols: list[Symbol]) -> list[tuple[str, str]]:
        """Extract call edges (caller_id, callee_id) from the AST.

        Only includes edges where both caller and callee are known symbols.
        """
        edges, _metadata = self.build_with_metadata(tree, source_bytes, language, symbols)
        return edges

    def build_with_metadata(
        self,
        tree: Tree,
        source_bytes: bytes,
        language: str,
        symbols: list[Symbol],
    ) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
        """Extract resolved edges and include unresolved/ambiguous metadata."""
        by_id = {str(s.id): s for s in symbols}
        name_to_ids, qname_to_id = self._build_symbol_lookups(symbols)
        symbol_at_line = self._symbol_spans(symbols)
        call_types = CALL_NODE_TYPES.get(language, [])
        calls: list[tuple[str, str]] = []
        metadata: list[dict[str, str]] = []
        self._find_calls(
            tree.root_node,
            source_bytes,
            call_types,
            name_to_ids,
            qname_to_id,
            symbol_at_line,
            by_id,
            calls,
            metadata,
        )

        # Deduplicate
        return sorted(set(calls)), metadata

    def _build_symbol_lookups(
        self, symbols: list[Symbol]
    ) -> tuple[dict[str, list[str]], dict[str, str]]:
        name_to_ids: dict[str, list[str]] = {}
        qname_to_id: dict[str, str] = {}
        for sym in symbols:
            sid = str(sym.id)
            qname = sym.raw.qualified_name
            qname_to_id[qname] = sid
            name_to_ids.setdefault(sym.raw.name, []).append(sid)
            name_to_ids.setdefault(qname, []).append(sid)
        return name_to_ids, qname_to_id

    def _symbol_spans(self, symbols: list[Symbol]) -> list[tuple[int, int, str]]:
        spans: list[tuple[int, int, str]] = []
        for sym in symbols:
            if sym.raw.kind.value == "f":
                spans.append((sym.raw.span.start_line, sym.raw.span.end_line, str(sym.id)))
        return spans

    def _find_calls(
        self,
        node: Node,
        source_bytes: bytes,
        call_types: list[str],
        name_to_ids: dict[str, list[str]],
        qname_to_id: dict[str, str],
        symbol_at_line: list[tuple[int, int, str]],
        by_id: dict[str, Symbol],
        calls: list[tuple[str, str]],
        metadata: list[dict[str, str]],
    ) -> None:
        if node.type in call_types:
            callee_name = self._extract_callee_name(node, source_bytes)
            caller_id = self._find_enclosing_function(node.start_point[0], symbol_at_line)
            if callee_name and caller_id:
                resolution = self._resolve_callee(
                    callee_name=callee_name,
                    caller_id=caller_id,
                    by_id=by_id,
                    name_to_ids=name_to_ids,
                    qname_to_id=qname_to_id,
                )
                status = str(resolution["status"])
                if status in ("unresolved", "ambiguous"):
                    metadata.append(
                        {
                            "caller_id": caller_id,
                            "callee_name": callee_name,
                            "status": status,
                            "resolved_id": resolution.get("resolved_id", ""),
                            "candidate_count": str(resolution.get("candidate_count", 0)),
                        }
                    )
                callee_id = resolution.get("resolved_id")
                if callee_id and caller_id != callee_id:
                    calls.append((caller_id, callee_id))

        for child in node.children:
            self._find_calls(
                child,
                source_bytes,
                call_types,
                name_to_ids,
                qname_to_id,
                symbol_at_line,
                by_id,
                calls,
                metadata,
            )

    def _resolve_callee(
        self,
        callee_name: str,
        caller_id: str,
        by_id: dict[str, Symbol],
        name_to_ids: dict[str, list[str]],
        qname_to_id: dict[str, str],
    ) -> dict[str, str | int]:
        # Exact qualified lookup.
        if callee_name in qname_to_id:
            return {"status": "resolved_qualified", "resolved_id": qname_to_id[callee_name], "candidate_count": 1}

        caller = by_id[caller_id]
        candidates = name_to_ids.get(callee_name, [])
        if not candidates:
            # Scoped syntax where only suffix exists in registry.
            if "::" in callee_name:
                suffix = callee_name.split("::")[-1]
                candidates = name_to_ids.get(suffix, [])
            if not candidates:
                return {"status": "unresolved", "candidate_count": 0}

        if len(candidates) == 1:
            return {"status": "resolved_unique", "resolved_id": candidates[0], "candidate_count": 1}

        # Scope-aware disambiguation.
        same_scope = [
            sid
            for sid in candidates
            if by_id[sid].raw.scope == caller.raw.scope
        ]
        if len(same_scope) == 1:
            return {"status": "resolved_same_scope", "resolved_id": same_scope[0], "candidate_count": len(candidates)}

        caller_scope = caller.raw.scope
        if caller_scope:
            nested = [
                sid
                for sid in candidates
                if by_id[sid].raw.qualified_name.startswith(f"{caller_scope}::")
            ]
            if len(nested) == 1:
                return {"status": "resolved_nested_scope", "resolved_id": nested[0], "candidate_count": len(candidates)}

        return {"status": "ambiguous", "candidate_count": len(candidates)}

    def _extract_callee_name(self, node: Node, source_bytes: bytes) -> str | None:
        """Extract the function name being called."""
        for child in node.children:
            if child.type in ("identifier", "field_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            if child.type in ("field_expression", "scoped_identifier", "attribute"):
                # For method calls like obj.method() or ns::func()
                # Get the rightmost identifier
                return self._rightmost_identifier(child, source_bytes)
        return None

    def _rightmost_identifier(self, node: Node, source_bytes: bytes) -> str | None:
        """Get the rightmost identifier in a dotted/scoped expression."""
        for child in reversed(node.children):
            if child.type in ("identifier", "field_identifier", "type_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _find_enclosing_function(
        self, line: int, symbol_at_line: list[tuple[int, int, str]]
    ) -> str | None:
        """Find which function symbol encloses a given source line."""
        best = None
        best_size = float("inf")
        for start, end, id_str in symbol_at_line:
            if start <= line <= end:
                size = end - start
                if size < best_size:
                    best = id_str
                    best_size = size
        return best

    def apply_to_symbols(self, edges: list[tuple[str, str]], symbols: list[Symbol]) -> None:
        """Set calls/called_by on Symbol objects from call edges."""
        by_id: dict[str, Symbol] = {str(s.id): s for s in symbols}
        for caller_id, callee_id in edges:
            caller = by_id.get(caller_id)
            callee = by_id.get(callee_id)
            if caller and callee:
                callee_sid = callee.id
                caller_sid = caller.id
                if callee_sid not in caller.calls:
                    caller.calls.append(callee_sid)
                if caller_sid not in callee.called_by:
                    callee.called_by.append(caller_sid)
