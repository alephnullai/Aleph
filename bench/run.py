#!/usr/bin/env python
"""Aleph vs grep+read baseline benchmark (Claims-Closure Plan, Phase A1).

Runs each task in bench/tasks.yaml in two modes and reports tokens
consumed, tool calls, wall time, and answer correctness vs ground truth:

  aleph mode  — drives aleph.query.engine.QueryEngine programmatically,
                simulating the tool-call sequence an agent makes
                (search -> resolve / callers / expand). Token cost is
                the text the agent would consume from each tool call.
  grep mode   — simulates the grep+read baseline an agent without Aleph
                uses: grep -rn for identifiers / query keywords, then
                read the candidate file region(s) (whole file if <400
                lines, else 120-line windows around hits, max 3 files,
                max 3 windows per file).

Usage:
    PYTHONPATH=src python bench/run.py            # full run, writes
                                                  # bench/results.json +
                                                  # bench/BENCHMARK.md
    PYTHONPATH=src python bench/run.py --tasks F  # alternate task file

This directory is NOT shipped in the wheel; it is a measurement rig for
the repo's own claims. The harness API (run_benchmark, AlephRunner,
GrepRunner) is exercised by tests/unit/test_bench_harness.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from aleph.query.engine import QueryEngine, SearchResult  # noqa: E402

# ── Token counting ──────────────────────────────────────────────────────


def make_token_counter():
    """Return (counter_fn, method_name).

    Prefers tiktoken (o200k_base) when importable; falls back to the
    crude-but-consistent len/4 estimate. The SAME counter is used for
    both modes, so the ratios are robust to the choice.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("o200k_base")

        def count(text: str) -> int:
            return len(enc.encode(text, disallowed_special=()))

        return count, "tiktoken/o200k_base"
    except Exception:
        return (lambda text: max(1, len(text) // 4)), "len/4"


# ── Grep baseline ───────────────────────────────────────────────────────

# Mirrors Claude Code's bash-output truncation: an agent never consumes
# more than ~30k characters of one grep call's output.
GREP_OUTPUT_CHAR_CAP = 30_000

_SKIP_DIRS = {
    ".git", ".aleph", "__pycache__", ".venv", "venv", "node_modules",
    ".claude", "dist", "build", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "bench",  # bench/ excluded: harness artifacts would
    # contaminate greps over the aleph corpus itself
}
_TEXT_SUFFIXES = {
    ".py", ".pyi", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".c",
    ".h", ".cpp", ".hpp", ".java", ".rb", ".md", ".rst", ".txt",
    ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh",
}

_STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "for", "and", "or", "that",
    "which", "with", "into", "from", "before", "after", "each",
    "using", "used", "use", "does", "doing", "done", "where", "when",
    "what", "how", "who", "why", "is", "are", "was", "were", "be",
    "been", "being", "on", "by", "at", "as", "it", "its", "this",
    "these", "those", "then", "than", "them", "their", "they", "all",
    "any", "some", "can", "could", "will", "would", "should", "has",
    "have", "had", "not", "but", "also", "about", "between", "over",
    "under", "only", "very", "via", "per", "code", "function", "file",
    "does",
}


def extract_keywords(query: str, max_keywords: int = 6) -> list[str]:
    """Content keywords a baseline agent would grep for.

    Same query string aleph search gets; stopwords and short words are
    dropped, order preserved, capped at max_keywords.
    """
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query)
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _STOPWORDS or lw in out:
            continue
        out.append(lw)
        if len(out) >= max_keywords:
            break
    return out


class GrepRunner:
    """Pure-Python emulation of `grep -rn` over a corpus + file reads.

    A single deterministic implementation is used everywhere (no
    ripgrep binary on the bench machine's subprocess PATH, and CI must
    not depend on one). Output format matches `grep -rn`:
    ``path:line:text``. Wall time therefore reflects a Python scan —
    slower than native ripgrep — so wall time is reported with that
    caveat; tokens and tool calls are the headline metrics.
    """

    WHOLE_FILE_MAX_LINES = 400
    WINDOW_LINES = 120
    MAX_FILES_READ = 3
    MAX_WINDOWS_PER_FILE = 3

    def __init__(self, root: str | Path, counter):
        self.root = Path(root)
        self.count = counter
        self._files: list[Path] | None = None

    def _corpus_files(self) -> list[Path]:
        if self._files is None:
            files: list[Path] = []
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames[:] = sorted(
                    d for d in dirnames if d not in _SKIP_DIRS
                )
                for name in sorted(filenames):
                    if Path(name).suffix.lower() in _TEXT_SUFFIXES:
                        files.append(Path(dirpath) / name)
            self._files = files
        return self._files

    def grep(self, pattern: str, ignore_case: bool = False):
        """One simulated `grep -rnE <pattern>` call.

        Returns (hits, output, tokens) where hits = [(relpath, lineno,
        line_text)] and output is the grep-formatted text the agent
        consumes (capped at GREP_OUTPUT_CHAR_CAP chars, mirroring
        Claude Code's tool-output truncation).
        """
        rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        hits: list[tuple[str, int, str]] = []
        for path in self._corpus_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append((rel, i, line))
        output = "\n".join(f"{f}:{n}:{t}" for f, n, t in hits)
        if len(output) > GREP_OUTPUT_CHAR_CAP:
            output = output[:GREP_OUTPUT_CHAR_CAP] + "\n[output truncated]"
        return hits, output, self.count(output)

    def read_file_region(self, relpath: str, hit_lines: list[int]):
        """Simulate the agent Reading a candidate file.

        Whole file if < WHOLE_FILE_MAX_LINES lines; otherwise merged
        WINDOW_LINES-line windows centered on each hit line (capped at
        MAX_WINDOWS_PER_FILE). Returns (region_text, tokens).
        """
        path = self.root / relpath
        try:
            lines = path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
        except OSError:
            return "", 0
        if len(lines) < self.WHOLE_FILE_MAX_LINES or not hit_lines:
            region = "\n".join(lines)
            return region, self.count(region)
        half = self.WINDOW_LINES // 2
        spans: list[list[int]] = []
        for ln in sorted(hit_lines)[: self.MAX_WINDOWS_PER_FILE]:
            lo = max(1, ln - half)
            hi = min(len(lines), ln + half)
            if spans and lo <= spans[-1][1] + 1:
                spans[-1][1] = max(spans[-1][1], hi)
            else:
                spans.append([lo, hi])
        chunks = ["\n".join(lines[lo - 1: hi]) for lo, hi in spans]
        region = "\n...\n".join(chunks)
        return region, self.count(region)

    # ── enclosing-scope resolution (for callers answers) ──

    def enclosing_scope(self, relpath: str, lineno: int) -> str:
        """Best-effort 'Class::method' for a 1-based source line.

        Walks upward to the nearest def at lower indentation, then the
        nearest class above that. Python-shaped; good enough for the
        two Python corpora.
        """
        path = self.root / relpath
        try:
            lines = path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
        except OSError:
            return ""
        if lineno > len(lines):
            return ""
        line = lines[lineno - 1]
        indent = len(line) - len(line.lstrip())
        func = ""
        func_indent = indent
        for i in range(lineno - 1, 0, -1):
            m = re.match(r"(\s*)(?:async\s+)?def\s+(\w+)", lines[i - 1])
            if m and len(m.group(1)) < func_indent:
                func = m.group(2)
                func_indent = len(m.group(1))
                break
        if not func:
            return ""
        for i in range(lineno - 1, 0, -1):
            m = re.match(r"(\s*)class\s+(\w+)", lines[i - 1])
            if m and len(m.group(1)) < func_indent:
                return f"{m.group(2)}::{func}"
        return func


# ── Aleph mode ──────────────────────────────────────────────────────────


def fmt_search(results: list[SearchResult], limit: int = 10) -> str:
    """Mirror of AlephHandlers.handle_search text output."""
    if not results:
        return "No matches."
    lines = [f"Matches: {len(results)}"]
    for r in results[:limit]:
        lines.append(
            f"  {r.symbol_id} {r.qualified_name} ({r.kind}) "
            f"score={r.score:.3f}"
        )
    if len(results) > limit:
        lines.append(f"  [{len(results) - limit} more matches]")
    return "\n".join(lines)


def fmt_resolve(r) -> str:
    """handle_resolve text plus the source span the engine carries.

    NOTE: the live aleph_resolve MCP rendering currently omits the
    Lines row even though the engine returns the span — a gap this
    benchmark surfaced (flagged for Phase A2). Token cost difference
    is one short line.
    """
    return (
        f"ID:        {r.symbol_id}\n"
        f"Name:      {r.name}\n"
        f"Qualified: {r.qualified_name}\n"
        f"Kind:      {r.kind}\n"
        f"Scope:     {r.scope}\n"
        f"File:      {r.file}\n"
        f"Lines:     {r.start_line}-{r.end_line}\n"
        f"Sig hash:  {r.signature_hash}"
    )


def fmt_callers(symbol_id: str, callers: list) -> str:
    """Mirror of AlephHandlers.handle_callers text output (cap 50)."""
    if not callers:
        return f"No callers found for {symbol_id}."
    lines = [f"Callers of {symbol_id}: {len(callers)}"]
    for c in callers[:50]:
        lines.append(f"  {c.caller_id} {c.caller_name} ({c.caller_file})")
    if len(callers) > 50:
        lines.append(f"  [TRUNCATED: showing 50 of {len(callers)} callers]")
    return "\n".join(lines)


class AlephRunner:
    """Drives QueryEngine the way an MCP agent drives the aleph tools."""

    def __init__(self, root: str | Path, counter, warm: bool = True):
        self.engine = QueryEngine(str(root))
        self.count = counter
        if warm:
            # An agent talks to an already-running MCP server: index +
            # embedding-model load time is server startup, not query
            # cost. Excluded from per-task wall time (documented).
            self.engine.search("warm up lazy indexes please")
            self.engine.callers("f_000000")

    @staticmethod
    def pick(results: list[SearchResult], query: str) -> SearchResult | None:
        """The pick an agent makes from a search listing: exact-name
        match when one exists, else the top-ranked result."""
        if not results:
            return None
        ql = query.strip().lower()
        for r in results[:10]:
            last = r.qualified_name.split("::")[-1].lower()
            if r.match in ("exact", "exact-id") or last == ql:
                return r
        return results[0]


# ── Task execution ──────────────────────────────────────────────────────


def _facts_present(facts: list[str], evidence: str) -> tuple[bool, list[str]]:
    ev = evidence.lower()
    missing = [f for f in facts if f.lower() not in ev]
    return not missing, missing


def _callers_present(expected: list[str], answer_text: str):
    """Each expected entry is a regex alternation; all must match."""
    missing = [
        pat for pat in expected
        if not re.search(pat, answer_text)
    ]
    return not missing, missing


def run_task_aleph(task: dict, runner: AlephRunner) -> dict:
    count = runner.count
    t0 = time.perf_counter()
    tokens = 0
    calls = 0
    correct = False
    answer: dict = {}
    expect = task["expect"]
    ttype = task["type"]

    results = runner.engine.search(task["query"])
    calls += 1
    tokens += count(fmt_search(results))

    if ttype == "find":
        top5 = results[:5]
        sym = expect.get("symbol", "")
        for rank, r in enumerate(top5, 1):
            last = r.qualified_name.split("::")[-1]
            if (sym and last == sym) or r.file == expect.get("file"):
                correct = True
                answer = {"file": r.file, "symbol": r.qualified_name,
                          "rank": rank}
                break
        if results:  # agent resolves its top pick to get the file
            res = runner.engine.resolve(results[0].symbol_id)
            calls += 1
            if res:
                tokens += count(fmt_resolve(res))
        if not answer and results:
            answer = {"file": results[0].file,
                      "symbol": results[0].qualified_name, "rank": 1}
    else:
        best = runner.pick(results, task["query"])
        if best is not None:
            if ttype == "resolve":
                res = runner.engine.resolve(best.symbol_id)
                calls += 1
                if res:
                    tokens += count(fmt_resolve(res))
                    answer = {"file": res.file,
                              "symbol": res.qualified_name,
                              "line": res.start_line,
                              "end_line": res.end_line}
                    # Symbol-identity scoring: right FILE + right SYMBOL
                    # (qualified name's last segment). Line numbers are
                    # reported but NOT scored — line-pinned ground truth
                    # rots with every unrelated edit (it silently broke
                    # two resolve tasks before this change).
                    sym = task.get("symbol") or task["query"]
                    correct = (
                        res.file == expect["file"]
                        and res.qualified_name.split("::")[-1] == sym
                    )
            elif ttype == "callers":
                callers = runner.engine.callers(best.symbol_id)
                calls += 1
                text = fmt_callers(best.symbol_id, callers)
                tokens += count(text)
                names = [c.caller_name for c in callers]
                answer = {"callers": names}
                correct, missing = _callers_present(
                    expect["callers"], "\n".join(names))
                if missing:
                    answer["missing"] = missing
            elif ttype == "explain":
                body = runner.engine.expand(best.symbol_id)
                calls += 1
                if body:
                    tokens += count(body)
                    correct, missing = _facts_present(expect["facts"], body)
                    answer = {"symbol": best.qualified_name,
                              "facts_missing": missing}
            else:
                raise ValueError(f"unknown task type {ttype!r}")

    return {
        "tokens": tokens,
        "calls": calls,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        "correct": bool(correct),
        "answer": answer,
    }


def _def_pattern(symbol: str) -> str:
    return rf"(def|class)\s+{re.escape(symbol)}\b"


def run_task_grep(task: dict, runner: GrepRunner) -> dict:
    count = runner.count
    t0 = time.perf_counter()
    tokens = 0
    calls = 0
    correct = False
    answer: dict = {}
    expect = task["expect"]
    ttype = task["type"]

    if ttype in ("resolve", "explain"):
        symbol = task.get("symbol") or task["query"]
        hits, _out, tk = runner.grep(_def_pattern(symbol))
        calls += 1
        tokens += tk
        # Prefer a non-test definition hit, like an agent scanning the
        # grep listing would.
        def_hits = [h for h in hits if "test" not in h[0].lower()] or hits
        if def_hits:
            f, ln, _ = def_hits[0]
            region, tk = runner.read_file_region(f, [ln])
            calls += 1
            tokens += tk
            if ttype == "resolve":
                answer = {"file": f, "symbol": symbol, "line": ln}
                # Symbol identity is pinned by the grep pattern itself
                # ((def|class) <symbol>); scoring checks the FILE only.
                # Line numbers are reported but not scored (see aleph
                # mode for rationale).
                correct = f == expect["file"]
            else:  # explain — evidence is the region the agent read
                correct, missing = _facts_present(expect["facts"], region)
                answer = {"file": f, "facts_missing": missing}

    elif ttype == "callers":
        symbol = task.get("symbol") or task["query"]
        hits, _out, tk = runner.grep(rf"{re.escape(symbol)}\s*\(")
        calls += 1
        tokens += tk
        def_rx = re.compile(_def_pattern(symbol))
        call_sites = [h for h in hits if not def_rx.search(h[2])]
        by_file: dict[str, list[int]] = {}
        for f, ln, _ in call_sites:
            by_file.setdefault(f, []).append(ln)
        ranked = sorted(by_file.items(), key=lambda kv: -len(kv[1]))
        scopes: list[str] = []
        for f, lns in ranked[: runner.MAX_FILES_READ]:
            _region, tk = runner.read_file_region(f, lns)
            calls += 1
            tokens += tk
            for ln in lns:
                scope = runner.enclosing_scope(f, ln)
                if scope and scope not in scopes:
                    scopes.append(scope)
        answer = {"callers": scopes,
                  "files_with_call_sites": [f for f, _ in ranked]}
        correct, missing = _callers_present(
            expect["callers"], "\n".join(scopes))
        if missing:
            answer["missing"] = missing

    elif ttype == "find":
        keywords = extract_keywords(task["query"])
        pattern = "|".join(re.escape(k) for k in keywords)
        hits, _out, tk = runner.grep(pattern, ignore_case=True)
        calls += 1
        tokens += tk
        # Rank files by distinct keywords matched, then total hits —
        # how an agent triages a multi-keyword grep listing.
        per_file: dict[str, dict] = {}
        for f, ln, text in hits:
            d = per_file.setdefault(f, {"kw": set(), "lines": []})
            tl = text.lower()
            d["kw"].update(k for k in keywords if k in tl)
            d["lines"].append(ln)
        ranked = sorted(
            per_file.items(),
            key=lambda kv: (-len(kv[1]["kw"]), -len(kv[1]["lines"])),
        )
        top5 = [f for f, _ in ranked[:5]]
        for f, d in ranked[: runner.MAX_FILES_READ]:
            _region, tk = runner.read_file_region(f, d["lines"])
            calls += 1
            tokens += tk
        correct = expect["file"] in top5
        if correct:
            answer = {"file": expect["file"],
                      "rank": top5.index(expect["file"]) + 1}
        else:
            answer = {"top_files": top5}
    else:
        raise ValueError(f"unknown task type {ttype!r}")

    return {
        "tokens": tokens,
        "calls": calls,
        "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
        "correct": bool(correct),
        "answer": answer,
    }


# ── Orchestration ───────────────────────────────────────────────────────


def load_tasks(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith((".yaml", ".yml")):
        import yaml

        return yaml.safe_load(text)
    return json.loads(text)


def run_benchmark(corpora: dict[str, str], tasks: list[dict],
                  warm: bool = True) -> dict:
    """Run every task in both modes. Returns the results structure."""
    counter, method = make_token_counter()
    aleph_runners = {
        name: AlephRunner(root, counter, warm=warm)
        for name, root in corpora.items()
    }
    grep_runners = {
        name: GrepRunner(root, counter) for name, root in corpora.items()
    }
    rows = []
    for task in tasks:
        corpus = task["corpus"]
        rows.append({
            "id": task["id"],
            "corpus": corpus,
            "type": task["type"],
            "query": task["query"],
            "aleph": run_task_aleph(task, aleph_runners[corpus]),
            "grep": run_task_grep(task, grep_runners[corpus]),
        })
    return {
        "meta": {
            "date": time.strftime("%Y-%m-%d"),
            "token_counter": method,
            "corpora": {n: str(Path(r).resolve())
                        for n, r in corpora.items()},
            "n_tasks": len(rows),
        },
        "tasks": rows,
        "summary": summarize(rows),
    }


def _median(xs: list[float]) -> float:
    return round(statistics.median(xs), 1) if xs else 0.0


def summarize(rows: list[dict]) -> dict:
    def agg(subset: list[dict]) -> dict:
        out: dict = {"n": len(subset)}
        for mode in ("aleph", "grep"):
            out[mode] = {
                "median_tokens": _median([r[mode]["tokens"] for r in subset]),
                "total_tokens": sum(r[mode]["tokens"] for r in subset),
                "median_calls": _median([r[mode]["calls"] for r in subset]),
                "median_wall_ms": _median(
                    [r[mode]["wall_ms"] for r in subset]),
                "accuracy": round(
                    sum(r[mode]["correct"] for r in subset) / len(subset), 3
                ) if subset else 0.0,
            }
        a, g = out["aleph"]["median_tokens"], out["grep"]["median_tokens"]
        out["grep_to_aleph_token_ratio"] = round(g / a, 2) if a else None
        return out

    by_type = {}
    for t in sorted({r["type"] for r in rows}):
        by_type[t] = agg([r for r in rows if r["type"] == t])
    return {"overall": agg(rows), "by_type": by_type}


# ── Reporting ───────────────────────────────────────────────────────────


def render_markdown(results: dict) -> str:
    meta = results["meta"]
    s = results["summary"]
    lines = [
        "# Aleph vs grep+read — navigation benchmark",
        "",
        f"*Generated by `bench/run.py` on {meta['date']} — do not edit "
        f"by hand. {meta['n_tasks']} tasks, token counter: "
        f"`{meta['token_counter']}`.*",
        "",
        "Corpora:",
        "",
    ]
    for name, root in meta["corpora"].items():
        lines.append(f"- **{name}** — `{root}`")
    lines += [
        "",
        "## Results by task type",
        "",
        "| task type | n | aleph med. tokens | grep med. tokens | "
        "token ratio (grep/aleph) | aleph acc. | grep acc. | "
        "aleph med. calls | grep med. calls |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    order = ["resolve", "callers", "explain", "find"]
    types = [t for t in order if t in s["by_type"]] + [
        t for t in sorted(s["by_type"]) if t not in order]
    for t in types:
        d = s["by_type"][t]
        ratio = d["grep_to_aleph_token_ratio"]
        lines.append(
            f"| {t} | {d['n']} | {d['aleph']['median_tokens']:.0f} | "
            f"{d['grep']['median_tokens']:.0f} | "
            f"{ratio if ratio is not None else '—'}× | "
            f"{d['aleph']['accuracy']:.0%} | {d['grep']['accuracy']:.0%} | "
            f"{d['aleph']['median_calls']:.0f} | "
            f"{d['grep']['median_calls']:.0f} |"
        )
    o = s["overall"]
    ratio = o["grep_to_aleph_token_ratio"]
    total_ratio = (
        round(o["grep"]["total_tokens"] / o["aleph"]["total_tokens"], 2)
        if o["aleph"]["total_tokens"] else None
    )
    lines += [
        f"| **overall** | {o['n']} | {o['aleph']['median_tokens']:.0f} | "
        f"{o['grep']['median_tokens']:.0f} | "
        f"{ratio if ratio is not None else '—'}× | "
        f"{o['aleph']['accuracy']:.0%} | {o['grep']['accuracy']:.0%} | "
        f"{o['aleph']['median_calls']:.0f} | "
        f"{o['grep']['median_calls']:.0f} |",
        "",
        "## Summary",
        "",
        f"- **Median per-task token ratio (grep/aleph): {ratio}×** — "
        f"total-token ratio across all tasks: {total_ratio}×.",
        f"- Accuracy: aleph {o['aleph']['accuracy']:.0%}, grep "
        f"{o['grep']['accuracy']:.0%} (n={o['n']}).",
        f"- Median tool calls per task: aleph "
        f"{o['aleph']['median_calls']:.0f}, grep "
        f"{o['grep']['median_calls']:.0f}.",
        f"- Median wall time per task: aleph "
        f"{o['aleph']['median_wall_ms']:.0f} ms, grep "
        f"{o['grep']['median_wall_ms']:.0f} ms (see caveat below — the "
        f"baseline grep is a Python emulation, native ripgrep is "
        f"faster; treat wall time as indicative only).",
        "",
        "### Where grep wins",
        "",
    ]
    grep_wins = [
        t for t in types
        if s["by_type"][t]["grep"]["accuracy"]
        > s["by_type"][t]["aleph"]["accuracy"]
        or (s["by_type"][t]["grep_to_aleph_token_ratio"] or 99) < 1.0
    ]
    if grep_wins:
        for t in grep_wins:
            d = s["by_type"][t]
            lines.append(
                f"- **{t}**: grep accuracy {d['grep']['accuracy']:.0%} vs "
                f"aleph {d['aleph']['accuracy']:.0%}, token ratio "
                f"{d['grep_to_aleph_token_ratio']}×. Use the cheaper/"
                f"more reliable mode for this task shape."
            )
    else:
        lines.append(
            "- No task type in this suite had grep beating aleph on both "
            "accuracy and tokens; per-task results in `results.json` "
            "show individual tasks where grep was cheaper."
        )
    lines += [
        "",
        "## Per-task results",
        "",
        "| task | type | corpus | aleph tok / calls / ok | "
        "grep tok / calls / ok |",
        "|---|---|---|---|---|",
    ]
    for r in results["tasks"]:
        a, g = r["aleph"], r["grep"]
        lines.append(
            f"| {r['id']} | {r['type']} | {r['corpus']} | "
            f"{a['tokens']} / {a['calls']} / "
            f"{'✓' if a['correct'] else '✗'} | "
            f"{g['tokens']} / {g['calls']} / "
            f"{'✓' if g['correct'] else '✗'} |"
        )
    lines += [
        "",
        "## Methodology (read before quoting numbers)",
        "",
        "- **Token estimate**: " + results["meta"]["token_counter"] +
        " applied identically to both modes' tool outputs. Tokens count "
        "what the agent *consumes* (tool results), not what it types.",
        "- **Aleph mode** drives `QueryEngine` directly with the call "
        "sequence an agent makes: `search` → exact-match pick → "
        "`resolve`/`callers`/`expand` as the task requires. Output text "
        "mirrors the MCP handlers' formatting. One deviation: the "
        "benchmark's resolve rendering includes the `Lines:` span the "
        "engine returns; the live `aleph_resolve` MCP text currently "
        "omits it (gap found by this benchmark, to fix in Phase A2). "
        "Engine warm-up (index + embedding-model load) is excluded from "
        "task wall time — an agent talks to an already-running server.",
        "- **Grep mode is a modeled baseline, not a strawman**: it gets "
        "the same query keywords aleph gets (stopword-filtered for the "
        "natural-language `find` tasks), runs one `grep -rn`-equivalent "
        "scan, then Reads candidate regions — the whole file when "
        "< 400 lines, else 120-line windows around hits (max 3 windows/"
        "file), max 3 files. Grep output is token-capped at 30k chars "
        "per call, mirroring Claude Code's tool-output truncation "
        "(this *favors* grep). Grep is a pure-Python emulation of "
        "`grep -rn` with standard ignores (.git, .aleph, caches, and "
        "`bench/` itself so harness artifacts can't contaminate the "
        "aleph corpus); wall times are therefore pessimistic vs native "
        "ripgrep and not headline numbers.",
        "- **Correctness**: `resolve` — symbol-identity match: the "
        "answer names the right FILE + SYMBOL (qualified name; for "
        "grep the symbol is pinned by the `(def|class) <name>` pattern "
        "itself, so the file is what's scored). Line numbers are "
        "reported in `results.json` but NOT scored — line-pinned "
        "ground truth rots with every unrelated commit and silently "
        "broke two resolve tasks before this was changed; `callers` — "
        "every "
        "expected caller pattern appears in the answered caller list "
        "(subset match: extra callers, e.g. tests, don't penalize; "
        "patterns include alternates like the wrapper closure grep "
        "can see where aleph reports the registering function); "
        "`explain` — every key fact substring appears in the evidence "
        "the mode retrieved (aleph: expanded body; grep: the read "
        "region); `find` — the expected file/symbol appears in the "
        "top-5 ranked results (aleph: search ranking; grep: files "
        "ranked by distinct keywords matched, then hit count).",
        "- **Ground truth** (file, symbol, caller sets, key facts) was "
        "verified against the current source of both corpora when the "
        "tasks were authored — see `bench/tasks.yaml`. `line:` values "
        "there are an informational reference, not scored.",
        "- **What this does NOT measure**: model reasoning quality, "
        "multi-turn recovery from bad picks, or corpora without an "
        "up-to-date `.aleph` index (index build cost is not amortized "
        "into these numbers).",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tasks", default=str(BENCH_DIR / "tasks.yaml"))
    ap.add_argument("--out-dir", default=str(BENCH_DIR))
    args = ap.parse_args(argv)

    spec = load_tasks(args.tasks)
    corpora = {
        name: (root if os.path.isabs(root) else str(REPO_ROOT / root))
        for name, root in spec["corpora"].items()
    }
    for name, root in corpora.items():
        if not os.path.isdir(os.path.join(root, ".aleph")):
            print(f"error: corpus '{name}' has no .aleph index at {root}; "
                  f"run: python -m aleph.cli build {root} --semantic",
                  file=sys.stderr)
            return 1

    results = run_benchmark(corpora, spec["tasks"])

    out_dir = Path(args.out_dir)
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8")
    (out_dir / "BENCHMARK.md").write_text(
        render_markdown(results), encoding="utf-8")

    o = results["summary"]["overall"]
    print(f"{results['meta']['n_tasks']} tasks | "
          f"median token ratio (grep/aleph): "
          f"{o['grep_to_aleph_token_ratio']}x | "
          f"accuracy aleph {o['aleph']['accuracy']:.0%} / "
          f"grep {o['grep']['accuracy']:.0%}")
    print(f"wrote {out_dir / 'results.json'} and {out_dir / 'BENCHMARK.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
