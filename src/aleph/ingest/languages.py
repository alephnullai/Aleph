"""Language registry: loads tree-sitter grammars and maps file extensions."""

from __future__ import annotations

import tree_sitter_cpp as tscpp
import tree_sitter_rust as tsrust
import tree_sitter_python as tspython
from tree_sitter import Language


EXTENSION_MAP: dict[str, str] = {
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".rs": "rust",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
}


class LanguageRegistry:
    """Loads and caches tree-sitter language grammars."""

    _languages: dict[str, Language] = {}

    @classmethod
    def get_language(cls, name: str) -> Language:
        if name not in cls._languages:
            cls._languages[name] = cls._load_language(name)
        return cls._languages[name]

    @classmethod
    def _load_language(cls, name: str) -> Language:
        if name == "cpp":
            return Language(tscpp.language())
        elif name == "rust":
            return Language(tsrust.language())
        elif name == "python":
            return Language(tspython.language())
        elif name == "go":
            import tree_sitter_go as tsgo
            return Language(tsgo.language())
        elif name in ("typescript", "tsx", "javascript"):
            import tree_sitter_typescript as tstypescript
            if name == "typescript":
                return Language(tstypescript.language_typescript())
            elif name == "tsx":
                return Language(tstypescript.language_tsx())
            else:  # javascript
                return Language(tstypescript.language_typescript())
        else:
            raise ValueError(f"Unsupported language: {name}")

    @classmethod
    def language_for_extension(cls, ext: str) -> str | None:
        return EXTENSION_MAP.get(ext)

    @classmethod
    def supported_languages(cls) -> list[str]:
        return ["cpp", "rust", "python", "typescript", "tsx", "javascript", "go"]

    @classmethod
    def supported_extensions(cls) -> list[str]:
        return sorted(EXTENSION_MAP.keys())
