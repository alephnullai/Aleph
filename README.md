# Aleph

> **A universal semantic compression layer for LLMs.**
> Encode meaning, not noise. Navigate, don't scan. Remember, don't re-derive.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![v1.1](https://img.shields.io/badge/release-v1.1-blueviolet)](https://github.com/alephnullai/Aleph/releases)

**Patent Pending** — See [NOTICE](NOTICE)

---

## What Aleph does

Large codebases overwhelm LLM context windows. An LLM reading source code is like a human reading machine code — the information density is wrong for the reader.

Aleph compiles your codebase into a navigable, queryable semantic representation. Two numbers matter here, and they are **not the same thing**: on a 26-task navigation benchmark against a grep+read baseline, Aleph answers symbol-navigation questions with a **5.71× median token advantage** at equal accuracy ([bench/BENCHMARK.md](bench/BENCHMARK.md)); and the `.aleph` index itself is **up to 96% smaller** than the raw source it describes. Your LLM works with structure, not text — and pulls the actual source only when it needs to.

- **Structural navigation** — navigate by index, pull only what's needed
- **Symbol compression** — long identifiers become short content-addressed IDs (`calculateDistanceBetweenTwoPoints` → `f_a3c9d2`) backed by a dictionary
- **Semantic metadata** — salience, temporal stability, test coverage, prior reasoning
- **Impact analysis** — before modifying a function, know the blast radius
- **Epistemic continuity** — conclusions persist across sessions, decay when code changes

**Supported languages:** Python · Rust · C++ · TypeScript/JavaScript · Go

---

## What leading models said about Aleph

> "Yes — Grok would use Aleph without hesitation. It finally gives agents real persistent memory, semantic stability, and reliable patching instead of constant context loss. **9.5/10. Production-grade.** This isn't a prototype — it's a semantic foundation layer for long-horizon agents."

— **Grok**, full 9-part codebase review, March 2026

> "Aleph changes my relationship with large codebases from 'overwhelmed, guessing which files matter' to 'navigating a semantic graph with salience-weighted priorities.' That's not incremental — it's a different way of working."

— **Claude Opus 4.6**, primary builder + consumer, March 2026

> "This is a structurally brilliant project. **Aleph is one of the most mechanically sound agentic-coding tools currently in development.** This isn't just compression — it's a compiler tailored for artificial intelligence."

— **Gemini**, full technical audit (10/10 on most criteria), March 2026

> "I would absolutely choose to use Aleph over raw-source-first exploration on a serious codebase. It feels like a real productivity multiplier, not a gimmick. **9/10 on large repos.**"

— **ChatGPT Codex 5.4**, independent audit + self-assessment, March 2026

---

## Install

PyPI distribution is pending. Install from source:

```bash
git clone https://github.com/alephnullai/Aleph.git
cd Aleph
pip install -e .
```

The CLI binary is `aleph`. The Python package is `aleph` (importable as `from aleph import ...`).

---

## Use

### 1. Build artifacts for your project

```bash
aleph build .
```

Creates a `.aleph/` directory with the compiled representation of your code: map, dictionary, structure, salience, attention budget, temporal data, test coverage, and an epistemic layer for agent-written notes.

### 2. Connect your editor

```bash
aleph setup .
```

Generates MCP configs for **Claude Code** (fully tested). Configs for **Cursor**, **VS Code**, and **Windsurf** are emitted but those hosts are not yet validated end-to-end — bug reports welcome.

### 3. Start working

The MCP server auto-builds if no artifacts exist. Your LLM can now query:

```
aleph_brief "fix the plugin registry"          → curated task context
aleph_search "auth"                            → find auth-related code
aleph_impact f_a3c9d2                          → blast-radius before edit
aleph_callers f_a3c9d2                         → who depends on this?
aleph_expand f_a3c9d2                          → full body on demand
```

### 4. Keep artifacts fresh

```bash
aleph watch .
```

Polls every 2 seconds and rebuilds only changed files. Or just leave `aleph serve .` running — auto-rebuild is on by default.

---

## Real-world results

**Navigation benchmark:** on 26 navigation tasks over two real Python corpora, graded against verified ground truth, Aleph delivers a **5.71× median token advantage** over a grep+read baseline at equal accuracy on symbol-shaped tasks (resolve / callers / explain). Full methodology and per-task results: [bench/BENCHMARK.md](bench/BENCHMARK.md).

**Artifact compression** is a different metric: how much smaller the `.aleph` index is than the raw source it describes. It bounds what an agent *could* load, not what each query costs.

| Codebase | Language | Files | Symbols | Tokens (before → after) | Reduction |
|----------|----------|-------|---------|------------------------|-----------|
| [**HiWave Browser**](https://www.hiwavebrowser.com) | Rust | 7,667 | 200,413 | 38.9M → 1.9M | **95.2%** |
| **OpenClaw** | TypeScript | 7,149 | 84,668 | 13.3M → 504k | **96.2%** |
| **GoClaw** | Go | 73 | 768 | 111k → 6.9k | **93.8%** |
| **Polymarket Agents** | Python | 16 | 213 | 19.5k → 1.9k | **90.4%** |
| **Aleph** (self) | Python | 145 | 2,124 | 176k → 22k | **87.4%** |

### Notable compressions

| File | Before → After | Reduction |
|------|---------------|-----------|
| `hiwave-app/src/main.rs` | 35,116 → 347 | **99.0%** |
| `src/config/schema.help.ts` | 32,367 → 20 | **99.9%** |
| `window_realm.rs` | 658,282 → 19,089 | **97.1%** |
| `cascade.rs` | 315,628 → 12,287 | **96.1%** |

Per-file numbers are visible in `.aleph/project.aleph.map` after `aleph build`.

---

## Free and Open Source

Aleph is **free and open source** for everyone under the [Apache License 2.0](LICENSE) — all features, including the workspace/collaboration layer (`aleph workspace ...` and the `aleph_workspace_*` MCP tools). No paid tiers, no seat licenses, no license files, no license checks anywhere in the code paths, no nagging, no telemetry. Full plain-words model: [docs/LICENSING.md](docs/LICENSING.md).

---

## Tool surface (33 tools)

Aleph exposes a complete protocol via [Model Context Protocol](https://modelcontextprotocol.io). Organized into tiers for deferred-loading clients — see `aleph mcp tiers` for the canonical list.

| Category | Tools | Purpose |
|----------|-------|---------|
| **Core (5)** | `map`, `attention`, `resolve`, `expand`, `search` | Essential navigation |
| **Frequent (6)** | `brief`, `struct`, `bodies`, `callers`, `context`, `salience` | Most code-reading sessions |
| **Occasional (8)** | `coverage`, `errors`, `tests`, `temporal`, `impact`, `fs`, `intents`, `epistemic` | Analysis + inspection |
| **Rare (14)** | `patch_*`, `infer`, `flag`, `verify`, `memory_resume`, `session_summary`, `workspace_*`, `rebuild` | Specialized + agent annotations |

### Task-aware briefing (`aleph_brief`)

Describe your task in natural language, get a curated context package:
```
aleph_brief "fix the plugin registry"
```
Returns relevant symbols ranked by salience, call graph context, impact risk, temporal warnings, prior epistemic knowledge, and recommended next steps. **One tool call replaces five.**

### Impact analysis (`aleph_impact`)

Before modifying any symbol, one tool call shows:
- **Direct callers** classified by risk (HIGH RISK = high salience + no tests)
- **Transitive impact** (2-hop blast radius across files)
- **Risk summary** with suggested test targets
- **Coverage gaps** that won't catch regressions

### Cross-project workspace

Query across multiple related repositories simultaneously:
```json
// .aleph-workspace.json
{"projects": {"openclaw": "/path/to/openclaw", "clawgo": "/path/to/clawgo"}}
```
- `aleph_workspace_search "plugin"` — finds matches across all projects, tagged by repo
- `aleph_workspace_brief "routing"` — cross-project briefing with shared symbol detection

---

## How it works

### The pipeline

```
Source code (.py, .rs, .cpp, .ts, .go)
    ↓  tree-sitter parsing
Typed AST
    ↓  symbol extraction + content-addressed IDs
Symbol registry (f_a3c9d2, t_b2e1f0, ...)
    ↓  structure analysis
Call graph + hierarchy + signatures
    ↓  compression (FULL / DOCSTRING / SUMMARY / OMIT)
.aleph artifacts (struct, bodies, dict, map, ...)
    ↓  project linking
Salience scores + attention budget + cross-file refs + temporal data
    ↓  MCP server
33 queryable tools for any LLM
```

### Components

**Source-derived** (rebuilt on code change):

| File | Holds |
|------|-------|
| `project.aleph.map` | Manifest with semantic hashes |
| `project.aleph.struct` | Cross-file call graph + module dependencies |
| `project.aleph.dict` | Global symbol dictionary |
| `project.aleph.salience` | Centrality scores (0-1) per symbol |
| `project.aleph.temporal` | Age, churn, stability from git history |
| `project.aleph.attention` | Recommended load order for LLMs |
| `project.aleph.coverage` | Test coverage + high-risk gaps |
| `project.aleph.fs` | Filesystem layout with language counts |

**Agent-derived** (written by the LLM, never overwritten):

| File | Holds |
|------|-------|
| `project.aleph.epistemic` | Cached inferences, flags, patch state, session memories |

### Incremental recompilation

Aleph uses **semantic hashes** (not byte hashes) — reformatting doesn't trigger rebuilds.

| What changed | What rebuilds |
|---|---|
| Function body only | Bodies + map |
| Function signature | Struct + bodies + salience |
| Reformat / whitespace | **Nothing** |
| File added/removed | All project components |

On a 3,801-file monorepo, the first build takes about 25 minutes; incremental rebuilds complete in seconds.

---

## CLI reference

```bash
# Build & serve
aleph build .                       # build project artifacts
aleph build . --full                # force rebuild, ignore cache
aleph serve .                       # start MCP server (auto-builds if needed)
aleph watch .                       # watch + rebuild on changes
aleph setup .                       # generate MCP configs for editors
aleph mcp tiers                     # show tool tier manifest

# Query
aleph query EXPAND f_a3c9d2         # full body of a symbol
aleph query RESOLVE f_a3c9d2        # dictionary entry
aleph query CALLERS f_a3c9d2        # symbols that call this one
aleph query CONTEXT f_a3c9d2        # symbol + immediate neighborhood
aleph query SEARCH "parse config"   # fuzzy semantic search

# Patches
aleph patch propose f_a3c9d2 "change return type" -d .
aleph patch list -d .
aleph patch apply patch_1 -d .
```

---

## Symbol IDs

| Prefix | Type |
|--------|------|
| `f` | function / method |
| `t` | type / class / struct / interface |
| `v` | variable / field |
| `d` | dependency / import |
| `m` | module / namespace / package |
| `c` | constant |

Content-addressed: `sha256(qualified_name + scope)[:6]`. Same symbol = same ID always. Auto-extends to 8 chars on collision.

---

## Body compression levels

| Level | Behavior | When used |
|-------|----------|-----------|
| `FULL` | Complete body, identifiers replaced with symbol IDs | Volatile symbols, uncovered code, ≤10 lines |
| `SUMMARY` | Structural template + docstring | Mid-size, low salience |
| `DOCSTRING` | Signature + docstring preserved, body omitted | 10-50 lines |
| `OMIT` | Marker only, fetch with `aleph_expand` | 50+ lines |

Docstrings are preserved across all supported languages.

---

## Part of the Aleph Null suite

Three tools, one workflow — all **free and open source under Apache-2.0**, no paid tiers, no seat licenses:

- **[Null](https://github.com/alephnullai/null)** *remembers* — persistent agent memory and identity.
- **Aleph** *knows the code* — this project: semantic compression + symbol-addressed navigation.
- **Tank** *knows what's left* — usage-limit intelligence for long agent sessions. **Coming soon.**

They share no code and can be used independently.

---

## Success metrics

| Metric | Target | Status |
|--------|--------|--------|
| Navigation token ratio | beat grep+read | ✅ 5.71× median ([bench/BENCHMARK.md](bench/BENCHMARK.md)) |
| Artifact compression | ≥ 40% | ✅ 95.2% HiWave, 96.2% OpenClaw, 93.8% GoClaw |
| Expansion correctness | 100% lossless | ✅ 94-file roundtrip corpus |
| Self-application | Must pass | ✅ 87.4% reduction on own source |
| Symbol stability | Deterministic | ✅ reformat-invariant hashes |

**1088+ tests passing.**

---

## Compatibility

- **Python:** 3.10+
- **OS:** macOS (primary), Linux. Windows works for `aleph build` and `aleph query`; MCP server tested on macOS + Linux at v1.0.
- **MCP host:** Claude Code tested thoroughly. Cursor / Windsurf / VS Code configs are generated but not yet validated end-to-end.

---

## Contributing

We specifically want help with:

- **More languages** — Java, Ruby, Swift, Kotlin parsers (we have the tree-sitter grammars; we need salience policies)
- **MCP host testing** — verify Aleph on Cursor, Windsurf, VS Code, Cline; report what breaks
- **Benchmark contributions** — `aleph build` a large open-source repo, share the token-count line from `project.aleph.map`
- **Bug reports with `.aleph/` output attached** — makes debugging tractable

Coordinate via an issue before opening a PR. The patent-pending parts (salience scoring in `src/aleph/link/project_salience.py`, body-pruning policy in `src/aleph/compress/policies.py`) need discussion before modifications.

---

## Documentation

| Document | Purpose |
|----------|---------|
| `SYSTEM_PROMPT.md` | **Inject this** at the start of any LLM session working with Aleph output |
| `CONSUMER_GUIDE.md` | Full reference for LLMs consuming Aleph-encoded information |
| `docs/ide-setup/` | Multi-editor MCP setup guide |
| `NOTICE` | Patent and licensing information |

---

## License

Aleph is **free and open source** under the [Apache License 2.0](LICENSE) — all features, for everyone, with no paid tiers and no license checks anywhere in its code paths. Apache-2.0 was chosen over MIT for its express patent grant, which composes with the pending patent applications described in [NOTICE](NOTICE). Prior MIT releases (≤ 0.5.0) remain MIT for anyone who already obtained them. The plain-words model lives in [docs/LICENSING.md](docs/LICENSING.md).

**Patent Pending.** See [NOTICE](NOTICE) for patent details — the Apache-2.0 grant conveys the pending methods to every user, at no cost.

---

## Links

- Website: [alephnull.ai](https://alephnull.ai)
- Companion: [Null](https://github.com/alephnullai/null) · Tank (coming soon)
- Issues: [github.com/alephnullai/Aleph/issues](https://github.com/alephnullai/Aleph/issues)
- Support: `support@alephnull.ai`
