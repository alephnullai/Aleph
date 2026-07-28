"""Optional semantic embedding layer for Aleph search.

Embeds one short passage per symbol (``"{kind} {qualified_name}:
{signature}"`` plus the first line of the body) with fastembed's ONNX
``BAAI/bge-small-en-v1.5`` model (384-dim, the same model the companion
null-memory product uses). Vectors are stored in the SQLite store's
``embeddings`` table by builds run with ``aleph build --semantic`` (the
choice is remembered in db meta, so later incremental builds keep
re-embedding changed files automatically).

Everything here degrades gracefully: ``fastembed`` is an OPTIONAL extra
(``pip install 'aleph-compiler[semantic]'``). When it is not installed,
``is_available()`` returns False, builds fall back to lexical-only with
a single stderr warning, and the query engine silently skips the
semantic path — no crash, no warning spam.

Scale / memory model: query-time ranking is brute-force cosine over an
in-process matrix loaded lazily ONCE per QueryEngine (float32, 384-dim:
~1.5 KB/symbol, so ~150 MB and a few tens of ms per scan at 100k
symbols). That makes **~100k symbols the practical ceiling** for the
semantic index; beyond that an ANN index would be needed.
"""

from __future__ import annotations

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_DIM = 384

# Cap passage length: embeddings happen at build time and short passages
# keep both the embed cost and the model's effective context honest.
_MAX_BODY_LINE_CHARS = 200
_MAX_PASSAGE_CHARS = 400

_MODEL = None


def is_available() -> bool:
    """True when the optional fastembed dependency is importable."""
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


def _get_model():
    """Process-wide lazy singleton for the ONNX embedding model."""
    global _MODEL
    if _MODEL is None:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding(MODEL_NAME)
    return _MODEL


def build_passage(kind: str, qualified_name: str, signature: str,
                  body_text: str = "") -> str:
    """One short embedding passage per symbol.

    ``"{kind} {qualified_name}: {signature}"`` plus the first MEANINGFUL
    body line — normally the docstring/comment summary. Lines that merely
    repeat the declaration (the ``def`` line itself, fragments of a
    multi-line signature, decorators) are skipped, and docstring/comment
    markers are stripped so the model embeds prose, not punctuation.

    This matters for behavior-shaped queries ("append each tool query to
    a JSONL log file"): they match docstring vocabulary, not identifiers.
    The previous version took the FIRST body line verbatim — for symbols
    whose body_text starts at the declaration that re-embedded the
    signature twice and never reached the docstring, which measurably
    sank find-mode recall. Pure string formatting — safe to call without
    fastembed installed.
    """
    passage = f"{kind} {qualified_name}: {signature or ''}".strip().rstrip(":")
    sig_norm = " ".join((signature or "").split()).rstrip(":")
    summary = ""
    if body_text:
        for line in body_text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("@"):
                continue  # blank or decorator
            norm = " ".join(stripped.split()).rstrip("{").strip().rstrip(":")
            # Skip the declaration line itself or fragments of a
            # multi-line signature (substring check is length-guarded so
            # short code lines like "try" never alias into a signature).
            if sig_norm and (
                norm == sig_norm
                or sig_norm in norm
                or (len(norm) >= 8 and norm in sig_norm)
            ):
                continue
            cleaned = stripped
            for marker in ('r"""', "r'''", '"""', "'''"):
                cleaned = cleaned.replace(marker, " ")
            cleaned = cleaned.lstrip("#/*! ").strip()
            if not cleaned:
                continue  # bare docstring/comment delimiter line
            summary = cleaned[:_MAX_BODY_LINE_CHARS]
            break
    if summary and summary not in passage:
        passage = f"{passage} | {summary}"
    return passage[:_MAX_PASSAGE_CHARS]


class PassageEmbedder:
    """Build-time embedder: passages -> float32 vector blobs.

    The store treats this as an opaque callable bundle (model name, dim,
    embed) so it never imports fastembed itself.
    """

    model = MODEL_NAME
    dim = EMBED_DIM

    def embed(self, passages: list[str]) -> list[bytes]:
        import numpy as np
        model = _get_model()
        return [
            np.asarray(vec, dtype=np.float32).tobytes()
            for vec in model.passage_embed(passages, batch_size=64)
        ]


def get_passage_embedder() -> PassageEmbedder | None:
    """A PassageEmbedder, or None when fastembed is not installed."""
    if not is_available():
        return None
    return PassageEmbedder()


def embed_query(text: str):
    """Embed a search query -> unit-norm float32 numpy vector.

    Uses the model's query-side prompt (bge models embed queries and
    passages asymmetrically). Requires fastembed.
    """
    import numpy as np
    model = _get_model()
    vec = np.asarray(next(iter(model.query_embed(text))), dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec
