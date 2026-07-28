"""Golden-set regression gate for aleph_brief task->symbol relevance.

HOW THIS GATE WORKS — read before touching ranking code:

- 20 realistic task descriptions, each with 1-3 acceptable expected
  symbols (qualified names or file paths), evaluated against the Aleph
  repo's OWN live self-index (.aleph/) — the same self-application
  pattern as test_self_application_project.py / test_mcp_server.py.
- A case is a hit when any acceptable symbol appears in the top-5
  [RELEVANT SYMBOLS] of handle_brief(task, max_symbols=5)  (hit@5).
- This set gates ALL future ranking changes.  When a ranking regression
  is found in the wild, ADD a case here reproducing it — never remove
  or reword existing cases to make the gate pass.
- Floors are the honest measured baseline per search mode and may only
  ratchet UP.  The target is 0.80; the gap is diagnosed in the case
  comments below (mostly zero-salience test symbols whose names echo
  task vocabulary drowning implementation symbols, plus derivational
  vocabulary gaps like truncate/cap that only semantic search bridges).
- Skips cleanly when the self-index is absent or stale: CI checkouts
  carry committed project.aleph.* artifacts that may lag the source
  tree, and have no aleph.db / semantic index at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from aleph.mcp.handlers import AlephHandlers

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
ALEPH_DIR = os.path.join(PROJECT_ROOT, ".aleph")
REQUIRED_ARTIFACTS = (
    "project.aleph.dict",
    "project.aleph.struct",
    "project.aleph.salience",
)

# Aspirational target for hit@5 — see module docstring for the gap diagnosis.
GOLDEN_TARGET = 0.80
# Honest measured baselines (2026-06-10).  Ratchet UP only.
FLOOR_HYBRID = 0.75    # semantic index present (aleph build --semantic + fastembed)
FLOOR_LEXICAL = 0.55   # CI parity: text artifacts only, no embeddings


@dataclass(frozen=True)
class GoldenCase:
    task: str
    # Acceptable answers: exact qualified names (as in project.aleph.dict,
    # e.g. "PatchManager::apply") or repo-relative file paths.
    expected: tuple[str, ...]


GOLDEN_CASES = [
    # ── mcp/handlers output caps ──
    GoldenCase(
        "add a new output cap parameter to the map handler",
        ("AlephHandlers::handle_map", "_cap_artifact_lines", "_cap_output"),
    ),
    GoldenCase(
        # KNOWN MISS (lexical + hybrid): pure vocabulary gap — _cap_output
        # shares zero subtokens with this phrasing; TestOutputCaps::* echo
        # "truncates"/"limit" and drown it.
        "truncate oversized tool responses at a byte limit",
        ("_cap_output",),
    ),
    # ── licensing/validator ──
    GoldenCase(
        # KNOWN MISS (lexical): "verification" never matches "verify"
        # (derivational); TestTamperedLicense::* echo license+signature.
        # Hybrid search bridges it.
        "fix license signature verification",
        ("_verify_signature", "validate_license"),
    ),
    GoldenCase(
        "find the license file for a project directory",
        ("_find_license_file", "validate_license"),
    ),
    # ── project/cache + builder ──
    GoldenCase(
        # KNOWN MISS (lexical): TestIncrementalRebuild::* drown the
        # implementation; hybrid recovers via compute_project_temporal.
        "why does incremental rebuild lose temporal data",
        (
            "cache_from_pipeline_result",
            "reconstruct_build_result",
            "compute_project_temporal",
        ),
    ),
    GoldenCase(
        # KNOWN MISS (lexical + hybrid): implementation vocabulary is
        # "fresh"/"stamp" not "skip unchanged"; the class BuildCache makes
        # top-5 but the methods do not.
        "skip unchanged files using the build cache",
        ("BuildCache::is_fresh", "FileStamp::matches"),
    ),
    GoldenCase(
        "detect racy mtimes in the file stamp cache",
        ("_is_racy_mtime",),
    ),
    GoldenCase(
        "compute test coverage status for each symbol",
        ("_compute_project_coverage",),
    ),
    GoldenCase(
        "resolve cross file function calls during build",
        ("_resolve_cross_file_calls",),
    ),
    # ── patch/manager containment ──
    GoldenCase(
        # KNOWN MISS (lexical + hybrid): the implementation names the
        # concept by its inverse (_contained_path vs "outside") and
        # TestPatchPathContainment::* echo the task wording exactly.
        "make patch apply refuse paths outside the project",
        ("PatchManager::_contained_path", "PatchManager::apply"),
    ),
    GoldenCase(
        "propose a patch for a symbol",
        ("PatchManager::propose", "AlephHandlers::handle_patch_propose"),
    ),
    # ── query/engine search + tokenizer ──
    GoldenCase(
        "tokenize identifiers into subtokens for search",
        ("tokenize_identifier",),
    ),
    GoldenCase(
        # KNOWN MISS (lexical + hybrid): "blend"/"rankings" vs
        # "fuse"/"rank" (derivational), and test_semantic_search.py test
        # names literally contain "semantic ... lexical ... ranking".
        "blend semantic and lexical search rankings",
        ("QueryEngine::_maybe_semantic_fuse", "QueryEngine::_semantic_rank"),
    ),
    # ── store/sqlite_store ──
    GoldenCase(
        "validate the sqlite store schema on open",
        ("SqliteStore::_ensure_schema", "SqliteStore::is_valid", "open_store"),
    ),
    GoldenCase(
        "embed missing symbols for the semantic index",
        ("SqliteStore::_embed_missing",),
    ),
    # ── epistemic/store ──
    GoldenCase(
        "recover the epistemic store from a corrupt file",
        ("EpistemicStore::_recover_corrupt",),
    ),
    # ── temporal/git_history ──
    GoldenCase(
        "batch git log queries per repository",
        ("GitHistory::_repo_log", "GitHistory::file_log"),
    ),
    # ── pipeline auto_build / mcp rebuild ──
    GoldenCase(
        "force a full project rebuild from the mcp tool",
        ("AlephHandlers::handle_rebuild", "auto_build"),
    ),
    # ── workspace engine ──
    GoldenCase(
        "search across multiple workspace projects",
        ("WorkspaceEngine::search", "AlephHandlers::handle_workspace_search"),
    ),
    GoldenCase(
        "load workspace project config from json",
        ("load_workspace_projects", "find_workspace_file"),
    ),
    # ── mcp/handlers brief itself ──
    GoldenCase(
        # KNOWN MISS (lexical + hybrid): _clean_task_query shares only
        # "task" with this phrasing; its caller handle_brief ranks #2.
        "strip stop words from a task description in the brief",
        ("AlephHandlers::_clean_task_query",),
    ),
]


@pytest.fixture(scope="module")
def handlers():
    """Handlers over Aleph's own .aleph/, skipping when absent or stale."""
    if not os.path.isdir(ALEPH_DIR):
        pytest.skip("No .aleph/ directory — run `aleph build .` first")
    for name in REQUIRED_ARTIFACTS:
        if not os.path.isfile(os.path.join(ALEPH_DIR, name)):
            pytest.skip(f"Missing artifact {name} — run `aleph build .` first")

    h = AlephHandlers(project_dir=PROJECT_ROOT)

    # Staleness probe: every golden symbol was verified against the source
    # tree when its case was authored.  If any is absent from the index,
    # the committed artifacts lag the source (or a golden symbol was
    # renamed) — skip rather than fail on a stale index.
    idx = h.engine._build_symbol_index()
    known = {e.qualified_name for e in idx.values()}
    known |= {e.file for e in idx.values()}
    missing = sorted({
        sym
        for case in GOLDEN_CASES
        for sym in case.expected
        if sym not in known
    })
    if missing:
        pytest.skip(
            f"self-index stale (run `aleph build .`) or golden symbols "
            f"renamed (update GOLDEN_CASES): {missing}"
        )
    return h


def _top5(handlers: AlephHandlers, task: str) -> list[tuple[str, str, str]]:
    """(symbol_id, qualified_name, file) for brief's top-5 relevant symbols."""
    out = handlers.handle_brief(task, max_symbols=5)
    rows: list[tuple[str, str, str]] = []
    in_section = False
    for line in out.split("\n"):
        if line.startswith("[RELEVANT SYMBOLS]"):
            in_section = True
            continue
        if in_section:
            if not line.strip():
                break
            parts = line.strip().split()
            sid = parts[0] if parts else ""
            qname = parts[1] if len(parts) > 1 else ""
            file = ""
            for p in parts:
                if p.startswith("file="):
                    file = p[len("file="):]
            rows.append((sid, qname, file))
    return rows


def _is_hit(case: GoldenCase, top5: list[tuple[str, str, str]]) -> bool:
    for _sid, qname, file in top5:
        for exp in case.expected:
            if "/" in exp:
                if exp == file:
                    return True
            elif exp == qname:
                return True
    return False


class TestBriefGoldenSet:
    def test_hit_at_5_meets_floor(self, handlers):
        misses: list[str] = []
        hits = 0
        for case in GOLDEN_CASES:
            top5 = _top5(handlers, case.task)
            if _is_hit(case, top5):
                hits += 1
            else:
                got = "\n".join(
                    f"      {sid} {qname} ({file})" for sid, qname, file in top5
                ) or "      <no confident match>"
                misses.append(
                    f"  MISS: {case.task!r}\n"
                    f"    expected one of {list(case.expected)}\n"
                    f"    got top-5:\n{got}"
                )

        rate = hits / len(GOLDEN_CASES)
        mode = handlers.engine.semantic_status()  # 'ok' => hybrid search
        floor = FLOOR_HYBRID if mode == "ok" else FLOOR_LEXICAL
        detail = "\n".join(misses)
        assert rate >= floor, (
            f"aleph_brief hit@5 regressed: {hits}/{len(GOLDEN_CASES)} = "
            f"{rate:.0%} < floor {floor:.0%} (mode={mode}, target "
            f"{GOLDEN_TARGET:.0%}).\nDo NOT lower the floor or edit cases — "
            f"fix the ranking. Misses:\n{detail}"
        )

    def test_case_set_is_intact(self):
        """Guard the gate itself: 20 cases, each with 1-3 expected symbols."""
        assert len(GOLDEN_CASES) >= 20
        for case in GOLDEN_CASES:
            assert 1 <= len(case.expected) <= 3
            assert len(case.task.split()) >= 3, case.task
