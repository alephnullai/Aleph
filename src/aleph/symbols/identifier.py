"""Symbol identification: content-addressed hash assignment.

ID scheme history:

  v1 (legacy, DEPRECATED): the hash input included ``raw.source_file``
      verbatim — in practice an ABSOLUTE path. Moving a checkout, cloning
      to a different directory, or a path-case change on a case-insensitive
      filesystem (the ``Aleph/`` vs ``aleph/`` macOS split) churned every
      symbol ID, orphaning epistemic inferences, temporal entries, and
      pending patches keyed by ID.

  v2 (portable): the hash input uses the project-root-RELATIVE path,
      POSIX-separated and case-lowered, so IDs are identical across
      machines, checkout locations, and path-case variants. Artifacts
      become shareable; ``.aleph`` state survives repo moves.

``ID_SCHEME_VERSION`` is recorded in the project map artifact
(``[ID_SCHEME:n]``) so loaders can detect artifacts built under an older
scheme and suggest ``aleph migrate-ids``.
"""

from __future__ import annotations

import os
import re

from aleph.model.symbol import SymbolID, RawSymbol
from aleph.util.hashing import symbol_id_hash

# Version of the symbol-ID derivation scheme (bump whenever the hash input
# changes shape). v2 = project-root-relative, POSIX, lowercased paths.
ID_SCHEME_VERSION = 2


def normalize_source_path(source_file: str, project_root: str | None) -> str:
    """Return the portable form of *source_file* used as ID hash input.

    With a *project_root* (scheme v2) the result is the root-relative path
    with forward slashes and an explicit ``.lower()``.

    Case normalization is an explicit ``.lower()`` rather than
    ``os.path.normcase`` because normcase lowercases only on Windows — it
    would not have fixed the macOS case-insensitive checkout problem
    (``Aleph/`` vs ``aleph/``) that already churned this repo's IDs, and it
    would make the hash input platform-dependent. The theoretical cost: on
    a case-sensitive filesystem two files whose paths differ only by case
    contribute the same path component — qualified name, scope, and
    signature still differentiate their symbols, and the registry's
    collision auto-extension covers any residue.

    Without a *project_root* the path is returned unchanged — the legacy
    v1 scheme. DEPRECATED: v1 IDs embed absolute paths and are not portable
    across checkouts; pass a project root wherever one exists.
    """
    if not source_file:
        return ""
    if not project_root:
        # Legacy v1 behavior (absolute/verbatim path), preserved for
        # backward compatibility. Deprecated — see docstring.
        return source_file
    try:
        rel = os.path.relpath(source_file, project_root)
    except ValueError:  # pragma: no cover — e.g. different drive on Windows
        rel = source_file
    return rel.replace("\\", "/").lower()


class SymbolIdentifier:
    """Assigns content-addressed SymbolIDs to RawSymbols.

    Args:
        project_root: Project root used to relativize ``source_file`` in
            the hash input (ID scheme v2, portable across machines). When
            None, falls back to the legacy v1 scheme that hashes the path
            verbatim (DEPRECATED — non-portable IDs).
    """

    DEFAULT_LENGTH = 6  # 6-char hex per Open Question 8 resolution

    def __init__(self, project_root: str | None = None) -> None:
        self.project_root = os.path.abspath(project_root) if project_root else None

    def _portable_source_file(self, raw: RawSymbol) -> str:
        return normalize_source_path(raw.source_file, self.project_root)

    def _normalized_signature(self, raw: RawSymbol) -> str:
        # Normalize whitespace and trim for stable overload differentiation.
        return re.sub(r"\s+", " ", raw.signature_text or "").strip()

    def dedup_key(self, raw: RawSymbol) -> str:
        return "|".join(
            [
                raw.qualified_name,
                raw.scope,
                raw.language,
                self._portable_source_file(raw),
                self._normalized_signature(raw),
            ]
        )

    def assign_id(self, raw: RawSymbol, length: int | None = None) -> SymbolID:
        """Create a SymbolID from a RawSymbol identity fields."""
        length = length or self.DEFAULT_LENGTH
        hex_hash = symbol_id_hash(
            raw.qualified_name,
            raw.scope,
            language=raw.language,
            source_file=self._portable_source_file(raw),
            signature=self._normalized_signature(raw),
            length=length,
        )
        return SymbolID(prefix=raw.kind.value, hex_hash=hex_hash)
