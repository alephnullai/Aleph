"""Golden-set regression gate for find-mode (NL behavior -> symbol) search.

HOW THIS GATE WORKS — read before touching ranking code:

- The 6 natural-language `find` queries from bench/tasks.yaml, evaluated
  the way the benchmark evaluates them: a case is a hit when the expected
  symbol (by trailing qualified-name segment) OR its file appears in the
  top-5 of engine.search(query)  (hit@5).
- 3 queries run against this repo's OWN live self-index (.aleph/) — the
  same self-application pattern as test_brief_golden.py.  3 run against
  the companion null-memory corpus, whose root is read from
  bench/tasks.yaml (the benchmark's source of truth); they skip cleanly
  on machines without that corpus.
- Requires the semantic index (`aleph build --semantic` + fastembed):
  these queries share almost no subtokens with their targets, so the
  lexical-only mode has nothing to rank — the gate skips rather than
  measuring noise.
- This set gates ALL future ranking changes.  When a find-mode regression
  is found in the wild, ADD a case here — never remove or reword existing
  cases to make the gate pass.
- Floors are the honest measured baseline and may only ratchet UP.
  Measured 2026-06-10 (fusion-stage test-file discount + directive
  exclusion + docstring-aware embedding passages): aleph 3/3, null 3/3.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from aleph.query.engine import QueryEngine

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
ALEPH_DIR = os.path.join(PROJECT_ROOT, ".aleph")

# Honest measured baselines (2026-06-10).  Ratchet UP only.
FLOOR_SELF = 1.0   # 3/3 on the aleph self-index
FLOOR_NULL = 1.0   # 3/3 on the null-memory corpus


@dataclass(frozen=True)
class FindCase:
    query: str
    # Trailing qualified-name segment of the expected symbol, e.g.
    # "_cap_output" matches "AlephHandlers::_cap_output".
    symbol: str
    # Repo-relative file path; a top-5 result in this file also counts
    # (mirrors bench/run.py's find-mode scoring).
    file: str


SELF_CASES = [
    FindCase(
        "cap the size of MCP tool responses at a byte limit",
        "_cap_output", "src/aleph/mcp/handlers.py",
    ),
    FindCase(
        "load embedding vectors into an in-memory matrix for cosine ranking",
        "_load_semantic", "src/aleph/query/engine.py",
    ),
    FindCase(
        "append each tool query to a JSONL log file on disk",
        "_flush_query_entry", "src/aleph/mcp/handlers.py",
    ),
]

NULL_CASES = [
    FindCase(
        "split a verbose memory fact into atomic children using an LLM",
        "crystallize_fact", "src/null_memory/crystallize.py",
    ),
    FindCase(
        "strip secrets and api keys from text before broadcasting",
        "redact", "src/null_memory/redaction.py",
    ),
    FindCase(
        "cosine distance between identity vectors to measure drift",
        "identity_drift", "src/null_memory/fingerprint.py",
    ),
]


def _null_corpus_root() -> str | None:
    """The null-memory corpus root from bench/tasks.yaml, or None."""
    tasks_yaml = os.path.join(PROJECT_ROOT, "bench", "tasks.yaml")
    try:
        with open(tasks_yaml, encoding="utf-8") as fh:
            in_corpora = False
            for line in fh:
                stripped = line.strip()
                if stripped == "corpora:":
                    in_corpora = True
                    continue
                if in_corpora:
                    if not line.startswith(" ") or not stripped:
                        break
                    if stripped.startswith(('"null"', "null:")):
                        root = stripped.split(":", 1)[1].strip().strip('"')
                        return root if os.path.isabs(root) else os.path.join(
                            PROJECT_ROOT, root
                        )
    except OSError:
        return None
    return None


def _semantic_engine(root: str, what: str) -> QueryEngine:
    if not os.path.isdir(os.path.join(root, ".aleph")):
        pytest.skip(f"{what} has no .aleph index at {root}")
    engine = QueryEngine(root)
    status = engine.semantic_status()
    if status != "ok":
        pytest.skip(
            f"{what} semantic index unavailable ({status}); "
            f"run `aleph build {root} --semantic` with fastembed installed"
        )
    return engine


def _is_hit(case: FindCase, engine: QueryEngine) -> bool:
    for r in engine.search(case.query)[:5]:
        if r.qualified_name.split("::")[-1] == case.symbol:
            return True
        if r.file == case.file:
            return True
    return False


def _assert_floor(engine: QueryEngine, cases: list[FindCase],
                  floor: float, corpus: str) -> None:
    misses = []
    hits = 0
    for case in cases:
        if _is_hit(case, engine):
            hits += 1
        else:
            got = "\n".join(
                f"      {r.kind} {r.qualified_name} ({r.file})"
                for r in engine.search(case.query)[:5]
            ) or "      <no results>"
            misses.append(
                f"  MISS: {case.query!r}\n"
                f"    expected {case.symbol} @ {case.file}\n"
                f"    got top-5:\n{got}"
            )
    rate = hits / len(cases)
    detail = "\n".join(misses)
    assert rate >= floor, (
        f"find-mode hit@5 regressed on the {corpus} corpus: "
        f"{hits}/{len(cases)} = {rate:.0%} < floor {floor:.0%}.\n"
        f"Do NOT lower the floor or edit cases — fix the ranking. "
        f"(If the embedding passage format changed, the corpus index "
        f"needs `aleph build --semantic` rebuilt first.)\n"
        f"Misses:\n{detail}"
    )


class TestFindGoldenSet:
    def test_self_corpus_meets_floor(self):
        engine = _semantic_engine(PROJECT_ROOT, "self-index")
        _assert_floor(engine, SELF_CASES, FLOOR_SELF, "aleph (self)")

    def test_null_corpus_meets_floor(self):
        root = _null_corpus_root()
        if root is None or not os.path.isdir(root):
            pytest.skip("null-memory corpus not present on this machine")
        engine = _semantic_engine(root, "null corpus")
        _assert_floor(engine, NULL_CASES, FLOOR_NULL, "null")

    def test_case_set_is_intact(self):
        """Guard the gate itself: the 6 benchmark queries, NL-shaped."""
        assert len(SELF_CASES) + len(NULL_CASES) == 6
        for case in SELF_CASES + NULL_CASES:
            assert len(case.query.split()) >= 4, case.query
            assert case.symbol and "/" in case.file
