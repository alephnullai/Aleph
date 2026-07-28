"""Error flow analysis: sources, boundaries, and unhandled errors."""

from __future__ import annotations

import re

from tree_sitter import Node, Tree

from aleph.model.symbol import Symbol
from aleph.model.components import (
    ErrorsComponent, ErrorSource, ErrorBoundary, UnhandledError,
)
from aleph.util.ast_utils import find_enclosing_symbol


class ErrorFlowAnalyzer:
    """Analyze error sources, boundaries, and unhandled paths from AST."""

    def analyze(
        self,
        tree: Tree,
        source_bytes: bytes,
        language: str,
        symbols: list[Symbol],
    ) -> ErrorsComponent:
        source_file = symbols[0].raw.source_file if symbols else ""
        sources: list[ErrorSource] = []
        boundaries: list[ErrorBoundary] = []

        self._walk(tree.root_node, source_bytes, language, symbols, sources, boundaries)

        # Detect unhandled errors: sources whose symbol has no boundary in the same file
        boundary_sids = {str(b.symbol_id) for b in boundaries}
        unhandled: list[UnhandledError] = []
        for src in sources:
            if str(src.symbol_id) not in boundary_sids:
                unhandled.append(UnhandledError(
                    symbol_id=src.symbol_id,
                    error_type=src.error_type,
                    description=f"No error boundary in {str(src.symbol_id)} for {src.error_type}",
                ))

        return ErrorsComponent(
            source_file=source_file,
            sources=sources,
            boundaries=boundaries,
            unhandled=unhandled,
        )

    def _walk(
        self,
        node: Node,
        source_bytes: bytes,
        language: str,
        symbols: list[Symbol],
        sources: list[ErrorSource],
        boundaries: list[ErrorBoundary],
    ) -> None:
        # ── Error sources ──
        if language == "cpp" and node.type == "throw_statement":
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                error_type = self._extract_cpp_throw_type(node, source_bytes)
                sources.append(ErrorSource(
                    symbol_id=sym.id,
                    error_type=error_type,
                    propagation="throws",
                    surfaces_at="caller",
                ))

        elif language == "python" and node.type == "raise_statement":
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                error_type = self._extract_python_raise_type(node, source_bytes)
                sources.append(ErrorSource(
                    symbol_id=sym.id,
                    error_type=error_type,
                    propagation="throws",
                    surfaces_at="caller",
                ))

        elif language == "rust" and node.type == "try_expression":
            # ? operator
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                sources.append(ErrorSource(
                    symbol_id=sym.id,
                    error_type="Error",
                    propagation="propagates via ?",
                    surfaces_at="caller",
                ))

        elif language == "rust" and node.type == "call_expression":
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if text.startswith("Err("):
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    sources.append(ErrorSource(
                        symbol_id=sym.id,
                        error_type=self._extract_rust_err_type(text),
                        propagation="returns Err(...)",
                        surfaces_at="caller",
                    ))

        elif language == "rust" and node.type == "macro_invocation":
            macro_name = self._first_child_text(node, source_bytes, "identifier")
            if macro_name and macro_name in ("panic", "bail", "anyhow"):
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    sources.append(ErrorSource(
                        symbol_id=sym.id,
                        error_type="panic" if macro_name == "panic" else macro_name,
                        propagation="throws",
                        surfaces_at="caller",
                    ))

        # ── Error boundaries ──
        if node.type == "try_statement" and language in ("cpp", "python"):
            sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
            if sym:
                catches = self._extract_catch_types(node, source_bytes, language)
                recovery = self._infer_recovery(node, source_bytes, language)
                boundaries.append(ErrorBoundary(
                    symbol_id=sym.id,
                    catches=catches,
                    recovery=recovery,
                ))

        elif language == "rust" and node.type == "match_expression":
            # Check if matching on Result/Option
            text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if "Err(" in text or "None" in text:
                sym = find_enclosing_symbol(node.start_point[0], symbols, "f")
                if sym:
                    boundaries.append(ErrorBoundary(
                        symbol_id=sym.id,
                        catches="Result/Option",
                        recovery="match arms",
                    ))

        for child in node.children:
            self._walk(child, source_bytes, language, symbols, sources, boundaries)

    def _extract_cpp_throw_type(self, node: Node, source_bytes: bytes) -> str:
        """Extract the type from a C++ throw statement."""
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        # throw std::runtime_error("...") → runtime_error
        match = re.search(r'throw\s+(?:std::)?(\w+)', text)
        if match:
            return match.group(1)
        return "exception"

    def _extract_python_raise_type(self, node: Node, source_bytes: bytes) -> str:
        """Extract the type from a Python raise statement."""
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        # raise ValueError("...") → ValueError
        match = re.search(r'raise\s+(\w+)', text)
        if match:
            return match.group(1)
        return "Exception"

    def _extract_rust_err_type(self, text: str) -> str:
        """Extract type from Err(...)."""
        match = re.search(r'Err\((\w+)', text)
        if match:
            return match.group(1)
        return "Error"

    def _extract_catch_types(self, node: Node, source_bytes: bytes, language: str) -> str:
        """Extract what types a try-catch/try-except catches."""
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if language == "cpp":
            catches = re.findall(r'catch\s*\(\s*(?:const\s+)?(?:std::)?(\w+)', text)
            return ", ".join(catches) if catches else "all"
        elif language == "python":
            catches = re.findall(r'except\s+(\w+)', text)
            return ", ".join(catches) if catches else "all"
        return "all"

    def _infer_recovery(self, node: Node, source_bytes: bytes, language: str) -> str:
        """Infer recovery strategy from catch/except body."""
        text = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if "return" in text:
            return "returns"
        if "raise" in text or "throw" in text:
            return "re-raises"
        if "log" in text.lower() or "print" in text or "cerr" in text:
            return "logs"
        return "handles"

    @staticmethod
    def _first_child_text(node: Node, source_bytes: bytes, child_type: str) -> str | None:
        for child in node.children:
            if child.type == child_type:
                return source_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
        return None
