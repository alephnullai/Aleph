"""Smoke test for the bench/ harness (Claims-Closure Plan, Phase A1).

Keeps bench/run.py alive in CI: runs two tiny inline tasks (resolve +
callers) in both modes against a throwaway fixture project. No
dependence on the big local corpora, ripgrep, tiktoken, or a semantic
index — everything falls back gracefully.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BENCH_RUN = Path(__file__).resolve().parents[2] / "bench" / "run.py"


def _load_bench_module():
    spec = importlib.util.spec_from_file_location("bench_run", BENCH_RUN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bench():
    return _load_bench_module()


@pytest.fixture(scope="module")
def fixture_project(tmp_path_factory):
    """A 2-function project with a built (lexical-only) .aleph index."""
    root = tmp_path_factory.mktemp("bench_fixture")
    (root / "calc.py").write_text(
        '"""Tiny fixture module."""\n'
        "\n"
        "def add_numbers(a, b):\n"
        '    """Add two integers and return the arithmetic sum."""\n'
        "    return a + b\n"
        "\n"
        "def triple_sum(a, b, c):\n"
        "    return add_numbers(add_numbers(a, b), c)\n"
    )
    from aleph import cli

    argv = sys.argv
    sys.argv = ["aleph", "build", str(root)]
    try:
        cli.main()
    finally:
        sys.argv = argv
    return root


TASKS = [
    {
        "id": "smoke-resolve-add_numbers",
        "corpus": "fix",
        "type": "resolve",
        "query": "add_numbers",
        "symbol": "add_numbers",
        "expect": {"file": "calc.py", "line": 3},
    },
    {
        "id": "smoke-callers-add_numbers",
        "corpus": "fix",
        "type": "callers",
        "query": "add_numbers",
        "symbol": "add_numbers",
        "expect": {"callers": ["triple_sum"]},
    },
]


def test_harness_both_modes_correct_on_fixture(bench, fixture_project):
    results = bench.run_benchmark(
        {"fix": str(fixture_project)}, TASKS, warm=False
    )

    assert results["meta"]["n_tasks"] == 2
    assert results["meta"]["token_counter"]

    by_id = {r["id"]: r for r in results["tasks"]}
    for task in TASKS:
        row = by_id[task["id"]]
        for mode in ("aleph", "grep"):
            res = row[mode]
            assert res["correct"], (task["id"], mode, res["answer"])
            assert res["tokens"] > 0
            assert res["calls"] >= 2
            assert res["wall_ms"] >= 0

    summary = results["summary"]
    assert set(summary["by_type"]) == {"resolve", "callers"}
    assert summary["overall"]["aleph"]["accuracy"] == 1.0
    assert summary["overall"]["grep"]["accuracy"] == 1.0
    assert summary["overall"]["grep_to_aleph_token_ratio"] is not None


def test_markdown_report_renders(bench, fixture_project):
    results = bench.run_benchmark(
        {"fix": str(fixture_project)}, TASKS, warm=False
    )
    md = bench.render_markdown(results)
    assert "## Results by task type" in md
    assert "## Methodology" in md
    assert "smoke-resolve-add_numbers" in md
    # every mode/task cell rendered with tokens/calls/correctness
    assert md.count("✓") >= 4


def test_grep_runner_primitives(bench, fixture_project):
    counter, _ = bench.make_token_counter()
    runner = bench.GrepRunner(fixture_project, counter)

    hits, output, tokens = runner.grep(r"def\s+add_numbers\b")
    assert hits == [("calc.py", 3, "def add_numbers(a, b):")]
    assert "calc.py:3:" in output
    assert tokens > 0

    # call site on line 8 sits inside triple_sum (line 7)
    assert runner.enclosing_scope("calc.py", 8) == "triple_sum"

    region, region_tokens = runner.read_file_region("calc.py", [3])
    assert "triple_sum" in region  # whole file: < 400 lines
    assert region_tokens > 0


def test_keyword_extraction_drops_stopwords(bench):
    kws = bench.extract_keywords(
        "the code that splits a fact into atomic children"
    )
    assert "the" not in kws and "that" not in kws and "into" not in kws
    assert "atomic" in kws and "children" in kws
