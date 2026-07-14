"""Intent inference from AST patterns."""

from __future__ import annotations

from tree_sitter import Node, Tree

from aleph.model.symbol import Symbol
from aleph.model.components import IntentsComponent, IntentEntry
from aleph.util.ast_utils import find_enclosing_symbol


class IntentInferrer:
    """Infer intent/precondition/invariant annotations from AST patterns."""

    def infer(
        self,
        tree: Tree,
        source_bytes: bytes,
        language: str,
        symbols: list[Symbol],
    ) -> IntentsComponent:
        """Walk the AST and infer intents for all symbols."""
        source_file = symbols[0].raw.source_file if symbols else ""
        entries: list[IntentEntry] = []

        self._walk(tree.root_node, source_bytes, language, symbols, entries)

        # Deduplicate entries
        seen: set[tuple[str, str, str]] = set()
        deduped: list[IntentEntry] = []
        for e in entries:
            key = (str(e.symbol_id), e.tag_type, e.description)
            if key not in seen:
                seen.add(key)
                deduped.append(e)

        # Populate symbol intent fields
        for entry in deduped:
            sid = str(entry.symbol_id)
            for sym in symbols:
                if str(sym.id) == sid:
                    tag = f"{entry.tag_type}:{entry.description}"
                    if tag not in sym.intents:
                        sym.intents.append(tag)
                    break

        return IntentsComponent(source_file=source_file, entries=deduped)

    def _walk(
        self,
        node: Node,
        source_bytes: bytes,
        language: str,
        symbols: list[Symbol],
        entries: list[IntentEntry],
    ) -> None:
        node_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        # Assert patterns
        if language == "cpp" and node.type == "call_expression":
            callee = self._first_child_text(node, source_bytes, "identifier")
            if callee and callee.startswith("assert"):
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    entries.append(IntentEntry(
                        symbol_id=sym.id,
                        tag_type="PRECONDITION",
                        description="assert() call",
                        confidence="inferred:high",
                    ))

        elif language == "rust" and node.type == "macro_invocation":
            macro_name = self._first_child_text(node, source_bytes, "identifier")
            if macro_name and ("assert" in macro_name or "debug_assert" in macro_name):
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    entries.append(IntentEntry(
                        symbol_id=sym.id,
                        tag_type="PRECONDITION",
                        description=f"{macro_name} macro",
                        confidence="inferred:high",
                    ))

        elif language == "python" and node.type == "assert_statement":
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                entries.append(IntentEntry(
                    symbol_id=sym.id,
                    tag_type="PRECONDITION",
                    description="assert statement",
                    confidence="inferred:high",
                ))

        # Throw/raise patterns
        if language == "cpp" and node.type == "throw_statement":
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                entries.append(IntentEntry(
                    symbol_id=sym.id,
                    tag_type="PRECONDITION",
                    description="throw in function body",
                    confidence="inferred:high",
                ))

        # try-catch/try-except → error-boundary
        if node.type == "try_statement" and language in ("cpp", "python"):
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                entries.append(IntentEntry(
                    symbol_id=sym.id,
                    tag_type="INTENT",
                    description="error-boundary",
                    confidence="inferred:medium",
                ))

        # Rust unsafe blocks
        if language == "rust" and node.type == "unsafe_block":
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                entries.append(IntentEntry(
                    symbol_id=sym.id,
                    tag_type="INTENT",
                    description="unsafe:reason-unknown",
                    confidence="inferred:medium",
                ))

        # Python decorators
        if language == "python" and node.type == "decorator":
            text = node_text.strip()
            if "property" in text:
                # The decorated function is the parent's first function_definition child
                parent = node.parent
                if parent:
                    for child in parent.children:
                        if child.type == "function_definition":
                            sym = find_enclosing_symbol(child.start_point[0], symbols, "f")
                            if sym:
                                entries.append(IntentEntry(
                                    symbol_id=sym.id,
                                    tag_type="INTENT",
                                    description="accessor",
                                    confidence="inferred:medium",
                                ))
                            break
            elif "staticmethod" in text:
                parent = node.parent
                if parent:
                    for child in parent.children:
                        if child.type == "function_definition":
                            sym = find_enclosing_symbol(child.start_point[0], symbols, "f")
                            if sym:
                                entries.append(IntentEntry(
                                    symbol_id=sym.id,
                                    tag_type="INTENT",
                                    description="static",
                                    confidence="inferred:medium",
                                ))
                            break

        # C++ const qualifier on function signatures
        if language == "cpp" and node.type == "type_qualifier":
            if "const" in node_text:
                # Find enclosing type or function
                sym = find_enclosing_symbol(node.start_point[0], symbols)
                if sym:
                    entries.append(IntentEntry(
                        symbol_id=sym.id,
                        tag_type="INVARIANT",
                        description="immutable",
                        confidence="inferred:high",
                    ))

        # Rust #[bench] attribute
        if language == "rust" and node.type == "attribute_item":
            if "bench" in node_text:
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    entries.append(IntentEntry(
                        symbol_id=sym.id,
                        tag_type="INTENT",
                        description="perf-critical",
                        confidence="inferred:medium",
                    ))

        for child in node.children:
            self._walk(child, source_bytes, language, symbols, entries)

    @staticmethod
    def _first_child_text(node: Node, source_bytes: bytes, child_type: str) -> str | None:
        """Get text of the first child of a specific type."""
        for child in node.children:
            if child.type == child_type:
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None
