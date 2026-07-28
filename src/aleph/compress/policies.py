"""Compression level policies."""

from aleph.model.enums import BodyLevel, SymbolKind


class CompressionPolicy:
    """Determines compression level for a symbol body.

    Default: OMIT for >10 lines, SUMMARY for <=10, FULL for signatures.
    Phase 1: stability=VOLATILE or coverage=none → FULL override.
    """

    def __init__(self, omit_threshold: int = 10, docstring_threshold: int = 50) -> None:
        self.omit_threshold = omit_threshold
        self.docstring_threshold = docstring_threshold

    def decide(
        self,
        kind: SymbolKind,
        body_text: str,
        signature_text: str,
        stability: str | None = None,
        coverage: str | None = None,
    ) -> BodyLevel:
        """Decide compression level for a symbol.

        Levels (descending fidelity):
            FULL      — complete body with identifier substitution
            DOCSTRING — signature + docstring only (prose preserved, body omitted)
            SUMMARY   — structural template (signature, call count, line count, docstring)
            OMIT      — marker only, available on demand via EXPAND
        """
        # Signatures are always FULL
        if kind in (SymbolKind.CONSTANT, SymbolKind.VARIABLE, SymbolKind.DEPENDENCY):
            return BodyLevel.FULL

        # Volatile symbols always get FULL (Principle XII override)
        if stability == "volatile":
            return BodyLevel.FULL

        # Uncovered code always gets FULL (Principle XIV override)
        if coverage == "none":
            return BodyLevel.FULL

        line_count = body_text.count("\n") + 1 if body_text else 0

        if line_count > self.docstring_threshold:
            return BodyLevel.OMIT
        elif line_count > self.omit_threshold:
            return BodyLevel.DOCSTRING
        elif line_count > 0:
            return BodyLevel.SUMMARY
        else:
            return BodyLevel.FULL
