"""Hashing utilities for symbol IDs, semantic fingerprints, and byte hashes."""

import hashlib
import json
from typing import Any


def symbol_id_hash(
    qualified_name: str,
    scope: str,
    *,
    language: str = "",
    source_file: str = "",
    signature: str = "",
    length: int = 6,
) -> str:
    """Content-addressed hash for symbol identification.

    sha256(qualified_name + "|" + scope + ...)[:length]
    Default 6-char hex (16.7M values) per Open Question 8 resolution.

    source_file is normalized to forward slashes for cross-platform stability.
    """
    # Normalize path separators so Windows and macOS produce the same hash
    normalized_file = source_file.replace("\\", "/")
    content = "|".join(
        [qualified_name, scope, language, normalized_file, signature]
    )
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return digest[:length]


def semantic_hash(graph_data: dict[str, Any]) -> str:
    """Hash of sorted, canonical graph structure. Reformat-invariant by construction.

    Takes a dict with 'nodes' and 'edges' keys, each containing sorted/canonical data.
    """
    canonical = json.dumps(graph_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def byte_hash(content: str | bytes) -> str:
    """Plain SHA256 of raw content."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()
