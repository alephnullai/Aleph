"""Body compression: FULL/SUMMARY/OMIT with symbol substitution."""

from __future__ import annotations

import re

from aleph.model.symbol import Symbol
from aleph.model.enums import BodyLevel
from aleph.model.components import BodyEntry
from aleph.compress.summarizer import Summarizer
from aleph.compress.policies import CompressionPolicy


class BodyCompressor:
    """Compresses symbol bodies according to policy.

    FULL: replace known identifiers with symbol IDs; preserve string literals verbatim.
    SUMMARY: template-based NL summary from signature analysis (no LLM dependency).
    OMIT: marker only.
    """

    def __init__(
        self,
        policy: CompressionPolicy | None = None,
        summarizer: Summarizer | None = None,
    ) -> None:
        self.policy = policy or CompressionPolicy()
        self.summarizer = summarizer or Summarizer()

    def compress(self, symbol: Symbol, symbol_dict: dict[str, str]) -> BodyEntry:
        """Compress a symbol's body according to policy."""
        level = self.policy.decide(
            symbol.raw.kind, symbol.raw.body_text, symbol.raw.signature_text,
            stability=symbol.stability, coverage=symbol.coverage,
        )
        symbol.body_level = level

        if level == BodyLevel.FULL:
            content = self._compress_full(symbol, symbol_dict)
        elif level == BodyLevel.DOCSTRING:
            content = self._compress_docstring(symbol)
        elif level == BodyLevel.SUMMARY:
            content = self.summarizer.summarize(symbol)
        else:
            content = ""

        return BodyEntry(
            symbol_id=symbol.id,
            level=level,
            content=content,
            original_body=symbol.raw.body_text,
        )

    def _compress_docstring(self, symbol: Symbol) -> str:
        """DOCSTRING compression: signature + docstring, no body."""
        from aleph.compress.summarizer import Summarizer
        docstring = Summarizer._extract_docstring(symbol.raw.body_text, symbol.raw.language)
        sig = symbol.raw.signature_text or symbol.raw.name
        if docstring:
            return f"{sig}\n  doc: {docstring}"
        return sig

    def _compress_full(self, symbol: Symbol, symbol_dict: dict[str, str]) -> str:
        """FULL compression: substitute known identifiers with symbol IDs.

        String literals are preserved verbatim (skip string_literal AST nodes).
        """
        body = symbol.raw.body_text

        # Build reverse dict: qualified_name -> id_str, sorted longest-first
        name_to_id: dict[str, str] = {v: k for k, v in symbol_dict.items()}
        # Also map simple names
        for id_str, qname in symbol_dict.items():
            simple = qname.split("::")[-1]
            if simple not in name_to_id:
                name_to_id[simple] = id_str

        # Extract string literal positions to preserve them
        protected_ranges = self._find_string_literals(body)

        # Sort by name length descending to avoid partial matches
        sorted_names = sorted(name_to_id.keys(), key=len, reverse=True)

        result = body
        for name in sorted_names:
            if not name or len(name) < 2:
                continue
            id_str = name_to_id[name]
            # Don't substitute the symbol's own name in its own definition
            if id_str == str(symbol.id):
                continue
            result = self._substitute_outside_strings(result, name, id_str, protected_ranges)

        return result

    def _find_string_literals(self, text: str) -> list[tuple[int, int]]:
        """Find positions of string literals in source text."""
        ranges = []
        i = 0
        while i < len(text):
            if text[i] in ('"', "'"):
                quote = text[i]
                start = i
                i += 1
                while i < len(text) and text[i] != quote:
                    if text[i] == "\\":
                        i += 1  # Skip escaped char
                    i += 1
                if i < len(text):
                    i += 1  # Skip closing quote
                ranges.append((start, i))
            else:
                i += 1
        return ranges

    def _substitute_outside_strings(
        self, text: str, name: str, replacement: str,
        protected_ranges: list[tuple[int, int]]
    ) -> str:
        """Substitute name with replacement, but not inside string literals."""
        # Use word-boundary matching
        pattern = re.compile(r'\b' + re.escape(name) + r'\b')

        def replace_if_outside(match: re.Match) -> str:
            pos = match.start()
            for start, end in protected_ranges:
                if start <= pos < end:
                    return match.group(0)  # Inside string, don't replace
            return replacement

        return pattern.sub(replace_if_outside, text)
