"""Template-based NL summaries (no LLM dependency).

Preserves docstrings/comments in SUMMARY level — the human-authored intent
is included alongside the structural summary.
"""

from __future__ import annotations

import re

from aleph.model.symbol import Symbol
from aleph.model.enums import SymbolKind


class Summarizer:
    """Generates template-based natural language summaries from signature analysis.

    Docstrings and leading comments are preserved in the summary output.
    """

    def summarize(self, symbol: Symbol) -> str:
        """Generate a structural NL summary of a symbol, preserving docstrings."""
        raw = symbol.raw
        kind = raw.kind

        if kind == SymbolKind.FUNCTION:
            summary = self._summarize_function(symbol)
        elif kind == SymbolKind.TYPE:
            summary = self._summarize_type(symbol)
        elif kind == SymbolKind.MODULE:
            summary = self._summarize_module(symbol)
        else:
            summary = f"{kind.value} {raw.name}"

        # Prepend docstring if present
        docstring = self._extract_docstring(raw.body_text, raw.language)
        if docstring:
            return f"{summary}\n  doc: {docstring}"
        return summary

    @staticmethod
    def _extract_docstring(body: str, language: str) -> str | None:
        """Extract the first docstring or leading comment from a symbol body.

        Supports:
        - Python: triple-quoted strings (''' or \"\"\")
        - Rust: /// doc comments
        - Go: // comments preceding function
        - C++/TS/JS: /** JSDoc */ or // leading comments
        """
        if not body:
            return None

        lines = body.split("\n")

        # Python: look for triple-quoted string in first few lines
        if language == "python":
            for i, line in enumerate(lines[:5]):
                stripped = line.strip()
                for q in ('"""', "'''"):
                    if q in stripped:
                        # Find closing triple quote
                        start_idx = body.find(q)
                        if start_idx < 0:
                            continue
                        end_idx = body.find(q, start_idx + 3)
                        if end_idx > start_idx:
                            doc = body[start_idx + 3:end_idx].strip()
                            # Truncate long docstrings
                            if len(doc) > 200:
                                doc = doc[:197] + "..."
                            return doc
            return None

        # Rust: /// doc comments
        if language == "rust":
            doc_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("///"):
                    doc_lines.append(stripped[3:].strip())
                elif doc_lines:
                    break  # End of doc comment block
            if doc_lines:
                doc = " ".join(doc_lines)
                return doc[:200] + "..." if len(doc) > 200 else doc
            return None

        # C++, TS, JS, Go: /** ... */ or // leading comments
        # Check for /** JSDoc */
        jsdoc = re.search(r'/\*\*(.*?)\*/', body, re.DOTALL)
        if jsdoc:
            doc = re.sub(r'\s*\*\s*', ' ', jsdoc.group(1)).strip()
            return doc[:200] + "..." if len(doc) > 200 else doc

        # Check for // leading comment block
        doc_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("//"):
                doc_lines.append(stripped[2:].strip())
            elif doc_lines:
                break
            elif stripped and not stripped.startswith(("func ", "function ", "def ", "pub ", "fn ", "class ", "type ", "const ", "var ", "let ")):
                continue  # Skip non-comment, non-declaration lines
            elif stripped:
                break
        if doc_lines:
            doc = " ".join(doc_lines)
            return doc[:200] + "..." if len(doc) > 200 else doc

        return None

    def _summarize_function(self, symbol: Symbol) -> str:
        raw = symbol.raw
        parts = [f"fn {raw.name}"]

        # Parse param count from signature
        sig = raw.signature_text
        if "(" in sig and ")" in sig:
            params_str = sig[sig.find("(") + 1:sig.rfind(")")]
            if params_str.strip():
                param_count = len([p for p in params_str.split(",") if p.strip()])
                parts.append(f"({param_count} params)")
            else:
                parts.append("(no params)")

        # Return type
        if "->" in sig:
            ret = sig[sig.find("->") + 2:].strip()
            parts.append(f"-> {ret}")

        # Call info
        if symbol.calls:
            parts.append(f"calls {len(symbol.calls)} symbols")
        if symbol.called_by:
            parts.append(f"called by {len(symbol.called_by)}")

        # Body size
        line_count = raw.body_text.count("\n") + 1
        parts.append(f"[{line_count} lines]")

        return "; ".join(parts)

    def _summarize_type(self, symbol: Symbol) -> str:
        raw = symbol.raw
        member_count = len(symbol.children)
        return f"type {raw.name} ({member_count} members)"

    def _summarize_module(self, symbol: Symbol) -> str:
        raw = symbol.raw
        child_count = len(symbol.children)
        return f"module {raw.name} ({child_count} definitions)"
