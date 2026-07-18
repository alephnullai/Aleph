"""Symbol extraction: walks tree-sitter AST and yields RawSymbols."""

from __future__ import annotations

from tree_sitter import Node, Tree

from aleph.model.symbol import RawSymbol, Span
from aleph.model.enums import SymbolKind
from aleph.ingest.node_types import get_symbol_kind


class SymbolExtractor:
    """Walks a tree-sitter AST and extracts RawSymbols."""

    def extract(
        self, tree: Tree, source: str, language: str, source_file: str = ""
    ) -> list[RawSymbol]:
        """Extract all symbols from a parsed tree."""
        source_bytes = source.encode("utf-8")
        symbols: list[RawSymbol] = []
        self._walk(
            tree.root_node,
            source_bytes,
            language,
            scope_stack=[],
            symbols=symbols,
            source_file=source_file,
        )
        return symbols

    # Node types whose children should NOT be walked for symbol extraction
    # (function bodies contain local variables that aren't top-level symbols)
    _LEAF_SYMBOL_TYPES = {
        "function_definition", "function_item",  # C++, Rust functions
        "function_declaration", "arrow_function",  # TS/JS functions
        "method_definition", "generator_function_declaration",
        "method_declaration",  # Go methods
    }

    # Node types that are containers — walk children for nested symbols
    _CONTAINER_TYPES = {
        SymbolKind.TYPE, SymbolKind.MODULE,
    }

    def _walk(
        self,
        node: Node,
        source_bytes: bytes,
        language: str,
        scope_stack: list[str],
        symbols: list[RawSymbol],
        source_file: str,
    ) -> None:
        kind = get_symbol_kind(language, node.type)
        if kind is None and language == "cpp" and node.type == "declaration":
            decl_child = node.child_by_field_name("declarator")
            if decl_child is not None:
                curr = decl_child
                while curr is not None:
                    if curr.type in ("function_declarator", "destructor_name"):
                        kind = SymbolKind.FUNCTION
                        break
                    curr = curr.child_by_field_name("declarator")

        if kind is not None:
            raw = self._extract_symbol(
                node, source_bytes, language, kind, scope_stack, source_file
            )
            if raw is not None:
                symbols.append(raw)
                # Container types: push scope and walk children
                if kind in self._CONTAINER_TYPES:
                    scope_stack.append(raw.name)
                    for child in node.children:
                        self._walk(
                            child,
                            source_bytes,
                            language,
                            scope_stack,
                            symbols,
                            source_file,
                        )
                    scope_stack.pop()
                    return
                # Leaf symbol types (functions): don't walk children
                if node.type in self._LEAF_SYMBOL_TYPES:
                    return

        for child in node.children:
            self._walk(
                child, source_bytes, language, scope_stack, symbols, source_file
            )

    def _extract_symbol(
        self,
        node: Node,
        source_bytes: bytes,
        language: str,
        kind: SymbolKind,
        scope_stack: list[str],
        source_file: str,
    ) -> RawSymbol | None:
        name = self._extract_name(node, source_bytes, language, kind)
        if not name:
            return None

        scope = "::".join(scope_stack) if scope_stack else ""
        qualified_name = f"{scope}::{name}" if scope else name

        span = Span(
            start_line=node.start_point[0],
            start_col=node.start_point[1],
            end_line=node.end_point[0],
            end_col=node.end_point[1],
        )

        body_text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        signature_text = self._extract_signature_text(node, source_bytes, language, kind)

        return RawSymbol(
            name=name,
            qualified_name=qualified_name,
            kind=kind,
            scope=scope,
            span=span,
            language=language,
            source_file=source_file,
            body_text=body_text,
            signature_text=signature_text,
        )

    def _extract_name(
        self, node: Node, source_bytes: bytes, language: str, kind: SymbolKind
    ) -> str | None:
        """Extract the name of a symbol from its AST node."""
        # Language-specific name extraction
        if language == "cpp":
            return self._extract_name_cpp(node, source_bytes, kind)
        elif language == "rust":
            return self._extract_name_rust(node, source_bytes, kind)
        elif language == "python":
            return self._extract_name_python(node, source_bytes, kind)
        elif language in ("typescript", "tsx", "javascript"):
            return self._extract_name_typescript(node, source_bytes, kind)
        elif language == "go":
            return self._extract_name_go(node, source_bytes, kind)
        # No generic fallback — unmapped languages are skipped to avoid
        # extracting wrong identifiers (e.g., return types as names).
        # Unknown node types are reported via get_unknown_node_types().
        return None

    # Leaf nodes that ARE a name (vs. a type sitting in front of one).
    _CPP_NAME_LEAVES = (
        "identifier", "field_identifier", "type_identifier",
        "namespace_identifier", "qualified_identifier",
        "destructor_name", "operator_name",
    )

    def _extract_name_cpp(self, node: Node, source_bytes: bytes, kind: SymbolKind) -> str | None:
        if kind == SymbolKind.DEPENDENCY:
            text = source_bytes[node.start_byte:node.end_byte].decode(
                "utf-8", errors="replace").strip()
            # A `using X::y;` / `using namespace N;` is a dependency, but its
            # NAME is the entity it imports — not the raw statement. The old
            # code returned the full text, so `using Base::method;` was indexed
            # as a symbol literally named "using Base::method;" (the `using`
            # keyword AND the trailing `;` baked into the name). Strip them so
            # the symbol is `Base::method`. #include etc. keep their full text.
            if node.type == "using_declaration":
                text = text.rstrip(";").strip()
                for _kw in ("using namespace ", "using "):
                    if text.startswith(_kw):
                        text = text[len(_kw):].strip()
                        break
                return text or None
            return text
        return self._cpp_name_from(node, source_bytes)

    def _cpp_name_from(self, node: Node, source_bytes: bytes) -> str | None:
        """Resolve a C++ declaration's name via tree-sitter FIELD names.

        The old positional child-scan returned the first identifier-ish child,
        but a C++ declaration lays the return/member TYPE before the name:

            std::string name() const   ->  [type] std::string  [declarator] name
            Point       center_;       ->  [type] Point         [declarator] center_

        so the scan captured the type (`std::string`, `Point`) as the symbol
        name and dropped the real one. It only looked correct for primitive
        returns, because `double`/`int` parse as `primitive_type`, which the
        scan skipped. Following the `declarator`/`name` fields instead skips
        the type by construction and works for pointers, references, out-of-
        line definitions (`Circle::area`), destructors, and operators alike.
        """
        # A declarator field is the name path for functions and variables; it
        # nests (pointer_declarator -> function_declarator -> field_identifier)
        # and always sits AFTER the type, so following it can't grab the type.
        decl = node.child_by_field_name("declarator")
        if decl is not None:
            return self._cpp_name_from(decl, source_bytes)

        # We've reached the leaf that actually is the name.
        if node.type in self._CPP_NAME_LEAVES:
            return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

        # class/struct/union/enum/namespace expose a bare `name` field.
        name = node.child_by_field_name("name")
        if name is not None:
            return source_bytes[name.start_byte:name.end_byte].decode("utf-8", errors="replace")

        # Defensive fallback for a node shape without the expected fields:
        # take the first name-leaf child, but NEVER one in the `type` field.
        type_child = node.child_by_field_name("type")
        for child in node.children:
            if child is type_child:
                continue
            if "declarator" in child.type:
                res = self._cpp_name_from(child, source_bytes)
                if res is not None:
                    return res
            if child.type in self._CPP_NAME_LEAVES:
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None

    def _extract_name_rust(self, node: Node, source_bytes: bytes, kind: SymbolKind) -> str | None:
        if kind == SymbolKind.DEPENDENCY:
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            return text.strip()

        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        return None

    def _extract_name_python(self, node: Node, source_bytes: bytes, kind: SymbolKind) -> str | None:
        if kind == SymbolKind.DEPENDENCY:
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            return text.strip()

        if kind == SymbolKind.VARIABLE and node.type == "assignment":
            # Get the left-hand side
            for child in node.children:
                if child.type == "identifier":
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                if child.type == "pattern_list":
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            return None

        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        return None

    def _extract_signature_text(
        self, node: Node, source_bytes: bytes, language: str, kind: SymbolKind
    ) -> str:
        """Extract the signature portion (without body) of a symbol."""
        if kind not in (SymbolKind.FUNCTION, SymbolKind.TYPE):
            return ""

        if language == "cpp":
            return self._extract_sig_cpp(node, source_bytes)
        elif language == "rust":
            return self._extract_sig_rust(node, source_bytes)
        elif language == "python":
            return self._extract_sig_python(node, source_bytes)
        elif language in ("typescript", "tsx", "javascript"):
            return self._extract_sig_typescript(node, source_bytes)
        elif language == "go":
            return self._extract_sig_go(node, source_bytes)
        return ""

    def _extract_sig_cpp(self, node: Node, source_bytes: bytes) -> str:
        """For C++, signature is everything before the compound_statement body."""
        for child in node.children:
            if child.type == "compound_statement":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
            if child.type == "field_declaration_list":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

    def _extract_sig_rust(self, node: Node, source_bytes: bytes) -> str:
        for child in node.children:
            if child.type == "block":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
            if child.type == "declaration_list":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

    def _extract_sig_python(self, node: Node, source_bytes: bytes) -> str:
        for child in node.children:
            if child.type == "block":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

    def _extract_name_typescript(
        self, node: Node, source_bytes: bytes, kind: SymbolKind
    ) -> str | None:
        if kind == SymbolKind.DEPENDENCY:
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            return text.strip()

        if kind == SymbolKind.VARIABLE and node.type == "variable_declarator":
            for child in node.children:
                if child.type in ("identifier", "property_identifier"):
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
            return None

        # Arrow functions assigned to variables: name comes from parent variable_declarator
        if node.type == "arrow_function" and node.parent and node.parent.type == "variable_declarator":
            for child in node.parent.children:
                if child.type in ("identifier", "property_identifier"):
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        for child in node.children:
            if child.type in ("identifier", "type_identifier", "property_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        return None

    def _extract_sig_typescript(self, node: Node, source_bytes: bytes) -> str:
        """For TS/JS, signature is everything before the statement_block body."""
        for child in node.children:
            if child.type in ("statement_block", "class_body"):
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

    def _extract_name_go(
        self, node: Node, source_bytes: bytes, kind: SymbolKind
    ) -> str | None:
        if kind == SymbolKind.DEPENDENCY:
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            return text.strip()

        if kind == SymbolKind.MODULE:
            # package clause: look for package_identifier
            for child in node.children:
                if child.type == "package_identifier":
                    return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        if kind == SymbolKind.TYPE:
            # type_declaration contains type_spec children with the actual name
            for child in node.children:
                if child.type == "type_spec":
                    for grandchild in child.children:
                        if grandchild.type == "type_identifier":
                            return source_bytes[grandchild.start_byte:grandchild.end_byte].decode("utf-8", errors="replace")

        if kind in (SymbolKind.CONSTANT, SymbolKind.VARIABLE):
            # const/var declarations may have spec children
            for child in node.children:
                if child.type in ("const_spec", "var_spec"):
                    for grandchild in child.children:
                        if grandchild.type == "identifier":
                            return source_bytes[grandchild.start_byte:grandchild.end_byte].decode("utf-8", errors="replace")

        # Functions and methods: look for identifier child
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "package_identifier"):
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        return None

    def _extract_sig_go(self, node: Node, source_bytes: bytes) -> str:
        """For Go, signature is everything before the block body."""
        for child in node.children:
            if child.type == "block":
                return source_bytes[node.start_byte:child.start_byte].decode("utf-8", errors="replace").strip()
        return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()

    # NOTE: No generic fallback extractors. Unmapped languages are skipped
    # to avoid extracting wrong identifiers (e.g., return types as function
    # names in Java/C#). Use get_unknown_node_types() to discover what needs
    # mapping, then add proper language-specific extractors.
