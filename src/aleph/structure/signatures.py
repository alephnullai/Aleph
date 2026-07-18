"""Signature extraction: return type, params, visibility."""

from __future__ import annotations

from tree_sitter import Node

from aleph.model.symbol import RawSymbol, Symbol
from aleph.model.enums import SymbolKind
from aleph.model.components import SignatureEntry


class SignatureExtractor:
    """Extracts structured signature information from symbols."""

    def extract(self, symbol: Symbol) -> SignatureEntry:
        """Create a SignatureEntry from a Symbol."""
        raw = symbol.raw
        return_type, params = self._parse_signature(raw)
        visibility = self._infer_visibility(raw)

        return SignatureEntry(
            symbol_id=symbol.id,
            name=raw.name,
            qualified_name=raw.qualified_name,
            kind=raw.kind.value,
            signature=raw.signature_text,
            visibility=visibility,
            return_type=return_type,
            params=params,
        )

    def _parse_signature(self, raw: RawSymbol) -> tuple[str, list[str]]:
        """Parse return type and parameters from signature text."""
        sig = raw.signature_text
        if not sig:
            return "", []

        if raw.language == "cpp":
            return self._parse_cpp_sig(sig)
        elif raw.language == "rust":
            return self._parse_rust_sig(sig)
        elif raw.language == "python":
            return self._parse_python_sig(sig)
        return "", []

    def _parse_cpp_sig(self, sig: str) -> tuple[str, list[str]]:
        """Parse C++ function signature for return type and params."""
        # Find parameter list between outermost ( )
        paren_start = sig.find("(")
        if paren_start == -1:
            return "", []

        # Everything before the opening paren (minus function name) is return type + name
        prefix = sig[:paren_start].strip()
        parts = prefix.rsplit(None, 1)
        return_type = parts[0] if len(parts) > 1 else ""

        # Extract params between parens
        paren_end = sig.rfind(")")
        if paren_end == -1:
            return return_type, []

        params_str = sig[paren_start + 1:paren_end].strip()
        if not params_str:
            return return_type, []

        params = [p.strip() for p in self._split_params(params_str)]
        return return_type, params

    def _parse_rust_sig(self, sig: str) -> tuple[str, list[str]]:
        """Parse Rust function signature for return type and params."""
        # Return type is after ->
        return_type = ""
        arrow_idx = sig.find("->")
        if arrow_idx != -1:
            return_type = sig[arrow_idx + 2:].strip()

        # Params between ( )
        paren_start = sig.find("(")
        if paren_start == -1:
            return return_type, []

        paren_end = sig.find(")")
        if paren_end == -1:
            return return_type, []

        params_str = sig[paren_start + 1:paren_end].strip()
        if not params_str:
            return return_type, []

        params = [p.strip() for p in self._split_params(params_str)]
        return return_type, params

    def _parse_python_sig(self, sig: str) -> tuple[str, list[str]]:
        """Parse Python function signature."""
        # Python has no return type in syntax (annotations are optional)
        return_type = ""
        arrow_idx = sig.find("->")
        if arrow_idx != -1:
            return_type = sig[arrow_idx + 2:].strip().rstrip(":")

        paren_start = sig.find("(")
        if paren_start == -1:
            return return_type, []

        paren_end = sig.find(")")
        if paren_end == -1:
            return return_type, []

        params_str = sig[paren_start + 1:paren_end].strip()
        if not params_str:
            return return_type, []

        params = [p.strip() for p in self._split_params(params_str)]
        return return_type, params

    def _split_params(self, params_str: str) -> list[str]:
        """Split parameter string respecting nested angle brackets and parens."""
        params = []
        depth = 0
        current = []
        for ch in params_str:
            if ch in "<([":
                depth += 1
                current.append(ch)
            elif ch in ">)]":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                params.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            params.append("".join(current))
        return [p for p in params if p.strip()]

    def _infer_visibility(self, raw: RawSymbol) -> str:
        """Infer visibility from source text."""
        body = raw.body_text
        if raw.language == "cpp":
            if "private:" in body or body.strip().startswith("private"):
                return "private"
            if "protected:" in body or body.strip().startswith("protected"):
                return "protected"
            return "public"
        elif raw.language == "rust":
            if raw.signature_text.lstrip().startswith("pub"):
                return "public"
            return "private"
        elif raw.language == "python":
            if raw.name.startswith("__") and not raw.name.endswith("__"):
                return "private"
            if raw.name.startswith("_"):
                return "protected"
            return "public"
        return "public"
