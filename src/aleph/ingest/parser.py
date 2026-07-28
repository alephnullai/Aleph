"""Tree-sitter parser wrapper."""

from __future__ import annotations

import os

from tree_sitter import Parser, Tree

from aleph.ingest.languages import LanguageRegistry


class TreeSitterParser:
    """Wraps tree-sitter, returns parsed AST."""

    def __init__(self) -> None:
        self._parsers: dict[str, Parser] = {}

    def _get_parser(self, language: str) -> Parser:
        if language not in self._parsers:
            parser = Parser(LanguageRegistry.get_language(language))
            self._parsers[language] = parser
        return self._parsers[language]

    def parse(self, source: str | bytes, language: str) -> Tree:
        """Parse source code and return the tree-sitter Tree."""
        parser = self._get_parser(language)
        if isinstance(source, str):
            source = source.encode("utf-8")
        return parser.parse(source)

    def parse_string(self, source: str, language: str) -> tuple[Tree, str, str]:
        """Parse a source string with explicit language. Returns (tree, source, language)."""
        tree = self.parse(source, language)
        return tree, source, language

    def parse_file(self, path: str) -> tuple[Tree, str, str]:
        """Parse a file, auto-detecting language. Returns (tree, source, language)."""
        ext = os.path.splitext(path)[1]
        language = LanguageRegistry.language_for_extension(ext)
        if language is None:
            raise ValueError(f"Unsupported file extension: {ext}")
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        tree = self.parse(source, language)
        return tree, source, language
