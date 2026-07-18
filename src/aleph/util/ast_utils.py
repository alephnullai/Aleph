"""Shared AST utilities used by callgraph, intent inference, and error flow."""

from __future__ import annotations

from aleph.model.symbol import Symbol


def find_enclosing_symbol(
    line: int, symbols: list[Symbol], kind_filter: str | None = None
) -> Symbol | None:
    """Find the smallest symbol whose span encloses the given line.

    Args:
        line: 0-based line number.
        symbols: List of symbols to search.
        kind_filter: If set, only consider symbols of this kind value (e.g. "f").
    """
    best: Symbol | None = None
    best_size = float("inf")
    for sym in symbols:
        if kind_filter and sym.raw.kind.value != kind_filter:
            continue
        span = sym.raw.span
        if span.start_line <= line <= span.end_line:
            size = span.end_line - span.start_line
            if size < best_size:
                best = sym
                best_size = size
    return best


def symbol_spans(symbols: list[Symbol], kind_filter: str = "f") -> list[tuple[int, int, str]]:
    """Return (start_line, end_line, id_str) tuples for symbols of the given kind."""
    spans: list[tuple[int, int, str]] = []
    for sym in symbols:
        if sym.raw.kind.value == kind_filter:
            spans.append((sym.raw.span.start_line, sym.raw.span.end_line, str(sym.id)))
    return spans
