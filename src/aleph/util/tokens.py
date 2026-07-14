"""Token counting utilities using tiktoken."""

from __future__ import annotations

from dataclasses import dataclass

import tiktoken


@dataclass
class TokenComparison:
    """Before/after token count comparison."""
    original_tokens: int
    compressed_tokens: int

    @property
    def reduction(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return 1.0 - (self.compressed_tokens / self.original_tokens)

    @property
    def reduction_percent(self) -> float:
        return self.reduction * 100.0


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Count tokens using tiktoken."""
    if not text:
        return 0
    enc = tiktoken.get_encoding(model)
    return len(enc.encode(text, disallowed_special=()))


def compare_tokens(original: str, compressed: str, model: str = "cl100k_base") -> TokenComparison:
    """Compare token counts between original and compressed text."""
    return TokenComparison(
        original_tokens=count_tokens(original, model),
        compressed_tokens=count_tokens(compressed, model),
    )
