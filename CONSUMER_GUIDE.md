# Aleph Consumer Guide
*A complete reference for LLMs consuming Aleph-encoded information.*

---

## What You're Working With

When you receive Aleph output, you are not reading source code. You are navigating a compiled semantic representation of it. The relationship between Aleph and source code is roughly what object files and linker maps are to a program — a form optimized for the reader, not the writer.

This guide explains how to use that representation effectively.

---

## The Information Model

Everything in Aleph is a node in a graph:

```
Entities   → functions, types, variables, modules, constants (assigned symbol IDs)
Edges      → calls, imports, defines, inherits, tests, covers, depends-on
Properties → salience, stability, intent, confidence, coverage
```

The component files are serializations of this graph, sliced by concern:

| Component | What it contains | Load when |
|-----------|-----------------|-----------|
| `project.aleph.map` | Manifest. All components + semantic hashes | Always first |
| `project.aleph.struct` | Cross-file call graph, module graph | Architecture work |
| `project.aleph.dict` | Global symbol dictionary | Resolving any unknown ID |
| `project.aleph.salience` | Centrality scores per symbol | Before touching anything |
| `project.aleph.temporal` | Age, churn, stability per symbol | Before modifying anything |
| `project.aleph.attention` | Recommended load order | First session on a new project |
| `project.aleph.coverage` | Test coverage + high-risk gaps | Reliability work |
| `project.aleph.epistemic` | Your prior inferences + flags | Every session resume |
| `<file>.aleph.struct` | Signatures, hierarchy, local calls | Working on a specific file |
| `<file>.aleph.bodies` | Compressed function bodies | When SUMMARY isn't enough |
| `<file>.aleph.intents` | Why things exist, invariants | Design / intent questions |
| `<file>.aleph.errors` | Error flows, propagation, recovery | Reliability / error handling |
| `<file>.aleph.tests` | Which behaviors are tested vs dark | Before modifying |

---

## Worked Load Scenarios

### Scenario 1: Orienting to an unfamiliar project

You've been handed a codebase you've never seen. Don't start reading files.

```
1. ALEPH:MAP                  → what components exist, what was recently changed
2. ALEPH:ATTENTION            → what matters most, recommended load order
3. ALEPH:SALIENCE             → internalize the weight distribution
4. ALEPH:STRUCT               → project architecture, module boundaries, key call paths
5. ALEPH:EPISTEMIC            → if present: prior conclusions about this codebase
```

After these five loads you know: what the project does, what symbols matter most, how they relate, and (if available) what a prior agent already figured out. You have not read a single function body. That's fine — you know where they are and what they're for.

---

### Scenario 2: Implementing a specific change

You need to modify a function. Before touching it:

```
1. ALEPH:RESOLVE <id>         → confirm you have the right symbol
2. ALEPH:TEMPORAL <id>        → is it stable, active, or volatile?
3. ALEPH:SALIENCE <id>        → how many things depend on this?
4. ALEPH:TESTS <file>         → which behaviors are covered? what would break?
5. ALEPH:EXPAND <id>          → now read the full body
6. ALEPH:CALLERS <id>         → who calls this? what do they expect?
7. ALEPH:INTENTS <file>       → are there invariants you must preserve?
```

Only after this do you write code. When done:
```
ALEPH:INFER <id> "changed return type from X to Y; callers updated" 0.9
```

---

### Scenario 3: Debugging an error

An error is propagating somewhere unexpected.

```
1. ALEPH:ERRORS <file>        → where can errors originate in this file?
2. ALEPH:CALLERS <id>         → who calls the failing function?
3. ALEPH:CONTEXT <id>         → the full local neighborhood
4. ALEPH:EXPAND <id>          → read the body of the failing function
5. ALEPH:EPISTEMIC <id>       → have you or another agent flagged this before?
```

If you find something uncertain:
```
ALEPH:FLAG <id> "error boundary behavior unclear — IoError may escape m_handler"
```

---

### Scenario 4: Resuming a prior session

This is where Aleph pays off most. You were mid-task when the session ended.

```
1. ALEPH:EPISTEMIC            → load everything you previously concluded
2. ALEPH:MAP                  → check semantic hashes — has anything changed since?
3. ALEPH:TEMPORAL <ids>       → re-check stability on symbols you were working on
```

If semantic hashes are unchanged: your prior inferences are still valid. Pick up where you left off.

If hashes changed: check which components changed, re-verify inferences about those symbols. Revoke confidence where appropriate:
```
ALEPH:INFER <id> "prior inference invalidated — symbol changed since last session" 0.2
```

**With memory compression:** If the prior session was compressed, load it first:
```
1. ALEPH:MEMORY:RESUME           → reconstructed session state (decisions, learnings, errors, open questions)
2. ALEPH:EPISTEMIC               → symbol-level inferences
3. ALEPH:MAP                     → check semantic hashes — has anything changed since?
4. ALEPH:TEMPORAL <ids>          → re-check stability on symbols from prior session
```

The resume prompt gives you high-level context (what you were doing, what you decided); the epistemic layer gives you symbol-level detail. Load both.

---

### Scenario 5: Assessing reliability before shipping

```
1. ALEPH:COVERAGE             → project-wide. What's untested? What's high-salience + untested?
2. ALEPH:ERRORS <file>        → what can fail? what's unhandled?
3. ALEPH:SALIENCE             → is the thing you're shipping on the critical path?
4. ALEPH:TEMPORAL <id>        → is it volatile? when was it last changed?
```

The `[UNCOVERED]` section of `project.aleph.coverage` surfaces the highest-risk gaps explicitly — high-salience symbols with no test coverage. Read this before calling anything production-ready.

---

## Reading Symbol IDs

IDs are stable and content-addressed. `f_a3c9` for `render_frame` will always be `f_a3c9` unless the function is renamed or moves scope.

When you encounter an unfamiliar ID:
```
ALEPH:RESOLVE f_a3c9
→ name: render_frame
   kind: function
   file: src/renderer.cpp
   signature: fn render_frame(ctx: &RenderContext) -> Result<Frame>
   salience: 0.87
   stability: active
```

Never guess what an ID refers to. Always resolve it.

---

## The Epistemic Layer in Depth

The epistemic layer is your cognitive continuity. Used correctly, it eliminates the "cold start" problem — each session begins with your accumulated understanding of the codebase, not from zero.

### Writing inferences

```
ALEPH:INFER f_a3c9 "thread-safe under render lock; lock acquired before any call" 0.85
ALEPH:INFER t_9f01 "v_cc3a and v_frame_count must stay in sync — not enforced by types" 0.90
ALEPH:INFER m_renderer "IoError boundary not complete — escapes in one edge case" 0.60
```

Confidence guidelines:
- `0.9–1.0` — verified by reading code + tests. High confidence.
- `0.7–0.9` — inferred from code patterns, not directly verified.
- `0.5–0.7` — hypothesis. Needs verification before acting on.
- `<0.5` — uncertain. Flag it.

### When to flag vs infer

Flag when you are uncertain and should not act without verification:
```
ALEPH:FLAG f_b12e "recovery path after IoError unclear — may silently swallow errors"
```

Infer when you have reached a conclusion, even a tentative one:
```
ALEPH:INFER f_b12e "catches IoError at boundary, logs and returns default frame" 0.65
```

Both can coexist on the same symbol. A flag means "check this." An inference means "here's my current best model."

### Confidence decay

Confidence in inferences decays with the symbol's churn rate. If you inferred something about a `volatile` symbol 3 months ago at 0.85, its effective confidence today is lower. Check `ALEPH:TEMPORAL <id>` and downgrade if the symbol has been modified since your inference:

```
ALEPH:INFER f_b12e "prior inference may be stale — symbol modified 2 times since last session" 0.45
ALEPH:FLAG f_b12e "re-verify error boundary behavior before relying on cached inference"
```

### Multi-agent epistemic state

If multiple agents have written to the epistemic layer (common in team settings), inferences carry a session ID. Newer sessions take precedence on conflicts, but both are stored. Read all inferences for a symbol before concluding — a prior agent may have flagged something you'd otherwise miss.

---

## Body Compression Reference

When you receive body content, it will be at one of three levels:

**OMIT** — the body was not loaded. You see:
```
[OMIT: f_a3c9 render_frame]
```
Request it: `ALEPH:EXPAND f_a3c9`

**SUMMARY** — a template-based natural language description:
```
[SUMMARY: validates RenderContext fields, acquires render lock, builds frame buffer, returns Result<Frame>]
```
This is sufficient for most reasoning tasks. Expand only if you need the implementation details.

**FULL** — verbatim body with symbol substitution:
```python
def f_a3c9(ctx: t_9f01) -> Result[t_frame]:
    if not ctx.v_cc3a:
        return Err(f_null_ctx_err())
    with v_render_lock:
        return f_build_frame(ctx)
```

In FULL mode, verbose identifiers are replaced with their symbol IDs. Always resolve IDs in `[DICT]` before reasoning about a FULL body.

---

## Semantic Hash Staleness

The map file records a semantic hash for every component. When you load the map, compare hashes to your last known state. If a hash changed, that component's content changed semantically — not just a reformat, a real change.

If you have cached inferences about symbols in a changed component:
1. Re-read the affected component
2. Verify inferences still hold
3. Update confidence accordingly

If hashes are unchanged since your last session, your prior inferences about those components remain valid. You do not need to re-read them.

---

## Epistemic File Format

The epistemic layer is stored as JSON in `project.aleph.epistemic`. Unlike source-derived components (which use the tag-based `.aleph` text format), the epistemic file is JSON because it is writable by agents and needs to support nested structure cleanly.

```json
{
  "version": "1.0",
  "sessions": [
    {
      "session_id": "2026-03-17:sebastian",
      "timestamp": "2026-03-17T20:00:00Z",
      "inferences": [
        {
          "symbol_id": "f_a3c9",
          "conclusion": "thread-safe under render lock; lock acquired before any call",
          "confidence": 0.85,
          "basis": "read f_a3c9 body + CALLERS trace",
          "timestamp": "2026-03-17T20:05:00Z"
        }
      ],
      "flags": [
        {
          "symbol_id": "f_b12e",
          "reason": "error boundary behavior unclear — IoError may escape",
          "resolved": false,
          "timestamp": "2026-03-17T20:10:00Z"
        }
      ],
      "learned": [
        {
          "fact": "v_cc3a and v_frame_count must stay in sync — not enforced by type system",
          "symbols": ["t_9f01", "v_cc3a"],
          "confidence": 0.90,
          "timestamp": "2026-03-17T20:15:00Z"
        }
      ]
    }
  ]
}
```

Multiple sessions accumulate in the `sessions` array. Later sessions take precedence on conflicting inferences, but all are stored. When you write `ALEPH:INFER`, a new entry is appended to the current session. A new session object is created when the server starts if no session for today exists.

---

## Semantic Patching

A semantic patch expresses *intent* — what behavior should change and why — independently of source language or line numbers. The Aleph toolchain translates intent to implementation.

```
ALEPH:PATCH f_a3c9 {
  add-precondition: v_d4f2 != null, check before first dereference
  add-error-path: return Err(NullArg) if precondition fails
  update-postcondition: now returns Result<Vec2> instead of Vec2
}
```

The patch is reviewable at the intent level before any code is generated. Use `ALEPH:PATCH:APPLY <patch_id>` to generate the concrete implementation.

**Patch body structure:**
- `add-precondition: <condition>, <description>` — adds a precondition check
- `add-postcondition: <condition>` — adds a postcondition assertion
- `add-error-path: <condition> → <action>` — adds an error handling branch
- `update-signature: <new signature>` — changes the function signature
- `add-invariant: <invariant>` — adds a new enforced invariant
- `refactor: <description>` — freeform intent for structural changes

Patches are stored in `project.aleph.epistemic` under a `patches` key. They are ephemeral until applied with `ALEPH:PATCH:APPLY`. Applied patches are marked `applied: true` and the resulting code change is recorded.

---

## Per-File Components and Build Configuration

Commands like `ALEPH:BODIES`, `ALEPH:ERRORS`, `ALEPH:INTENTS`, and `ALEPH:TESTS` require per-file component files to exist. These are **not generated by default**.

```bash
# Standard build — project-level components only
aleph build .

# Full build — project-level + per-file components
aleph build . --per-file

# Per-file for a specific file only
aleph build . --per-file src/renderer.cpp
```

If you issue `ALEPH:BODIES <file>` and the per-file components don't exist, the server will return an error indicating that `aleph build --per-file` is required. When in doubt, check `ALEPH:MAP` — per-file components appear in the manifest when present.

---

## Tool Integration (MCP and Function Calling)

When Aleph is exposed via MCP or a function-calling interface, protocol commands are mapped to tool names using underscores and lowercasing:

| Protocol command | Tool name |
|-----------------|-----------|
| `ALEPH:MAP` | `aleph_map` |
| `ALEPH:FS` | `aleph_fs` |
| `ALEPH:STRUCT` | `aleph_struct` |
| `ALEPH:BODIES` | `aleph_bodies` |
| `ALEPH:ERRORS` | `aleph_errors` |
| `ALEPH:INTENTS` | `aleph_intents` |
| `ALEPH:TESTS` | `aleph_tests` |
| `ALEPH:COVERAGE` | `aleph_coverage` |
| `ALEPH:EXPAND` | `aleph_expand` |
| `ALEPH:RESOLVE` | `aleph_resolve` |
| `ALEPH:CALLERS` | `aleph_callers` |
| `ALEPH:CONTEXT` | `aleph_context` |
| `ALEPH:SEARCH` | `aleph_search` |
| `ALEPH:ATTENTION` | `aleph_attention` |
| `ALEPH:SALIENCE` | `aleph_salience` |
| `ALEPH:TEMPORAL` | `aleph_temporal` |
| `ALEPH:EPISTEMIC` | `aleph_epistemic` |
| `ALEPH:INFER` | `aleph_infer` |
| `ALEPH:FLAG` | `aleph_flag` |
| `ALEPH:VERIFY` | `aleph_verify` |
| `ALEPH:PATCH` | `aleph_patch` |
| `ALEPH:PATCH:LIST` | `aleph_patch_list` |
| `ALEPH:PATCH:APPLY` | `aleph_patch_apply` |

All arguments map directly — `<id>` becomes a required string parameter, `[id]` becomes optional.

**Starting the MCP server:**
```bash
aleph serve .               # serve current directory's .aleph/ output
aleph serve /path/to/proj   # serve a specific project
```

The server connects on stdio by default (standard MCP transport). Configure your LLM client to connect to it and all ALEPH: tools become available immediately.

---

## Memory Compression

Aleph compresses conversation transcripts into structured memory that survives session boundaries. This is the mechanism behind epistemic continuity — your prior reasoning, decisions, and discoveries persist across sessions without replaying the full transcript.

### Output Format: `[ALEPH:MEMORY:1.0]`

Compressed memory uses the tag-based Aleph format (not JSON) for token efficiency. Structure:

```
[ALEPH:MEMORY:1.0]
[STATS]
messages=8
original_tokens=1248
compressed_tokens=354
reduction=71.6%
[/STATS]
[CONTEXT]
Debugging TypeError in ETL pipeline after validation library v2.0 update.
[/CONTEXT]
[DICT]
s_3a3127=strict
s_3ba952=handle_upload
s_79e6ae=validate_schema
s_e3ec18=transform_records
[/DICT]
[DECISIONS]
Use `validation-lib==2.0.3` (pinned version) [0.9] refs=s_79e6ae
Created `validate_schema_compat` wrapper for gradual migration [0.9] refs=s_79e6ae
Added error handling to `s_3ba952` for validation failures [0.9] refs=s_3ba952 s_79e6ae
[/DECISIONS]
[CONCLUSIONS]
Root cause: validation library v2.0 changed `s_3a3127` parameter to `mode='strict'` [0.8] refs=s_3a3127 s_79e6ae
Always check migration guides for breaking changes on dependency updates [0.8]
[/CONCLUSIONS]
[ERRORS_ENCOUNTERED]
TypeError: validate_schema() got an unexpected keyword argument 'strict' [0.9] refs=s_79e6ae s_3a3127
[/ERRORS_ENCOUNTERED]
[OPEN_QUESTIONS]
Should we add retry logic to `s_e3ec18` for batch jobs? [0.6] refs=s_e3ec18
Should we audit all dependencies for similar breaking changes? [0.6]
[/OPEN_QUESTIONS]
```

**Section tags:**
- `[STATS]` — compression metrics: message count, original/compressed token estimates, reduction percentage
- `[CONTEXT]` — one-line task summary extracted from the first user message
- `[DICT]` — symbol dictionary mapping `s_xxxx` IDs to conversation entities (recurring concepts, function names, technical terms). These are conversation-scoped — they do NOT resolve against the code symbol namespace
- `[DECISIONS]` — choices made during the session (confidence 0.9)
- `[CONCLUSIONS]` — insights discovered (confidence 0.8)
- `[CODE_CHANGES]` — modifications documented (confidence 0.85)
- `[ERRORS_ENCOUNTERED]` — errors hit and diagnosed (confidence 0.85)
- `[OPEN_QUESTIONS]` — unresolved items carried forward (confidence 0.6)

**Entry format:** `content [confidence] refs=symbol_id1 symbol_id2`

Empty sections are omitted.

### Conversation Input Format

The `aleph memory compress` command accepts a JSON file containing conversation messages:

```json
[
  {"role": "user", "content": "I'm getting an error in the data pipeline..."},
  {"role": "assistant", "content": "The root cause is the validation library update..."},
  {"role": "user", "content": "Can you check for other uses of the old API?"},
  {"role": "assistant", "content": "Found three call sites. Here's the fix..."}
]
```

Also accepted: `{"messages": [...]}` (wrapped form).

**CLI usage:**
```bash
aleph memory compress transcript.json                           # print compressed output
aleph memory compress transcript.json -d /path/to/project       # also save to epistemic file
aleph memory compress transcript.json -d . --session-id "s-42"  # with explicit session ID
aleph memory compress transcript.json --json                    # output stats as JSON
```

### Compression Pipeline

The compressor applies six stages to reduce a conversation to its essential content:

1. **Entity extraction** — identifies backtick-quoted code references, recurring snake_case/camelCase identifiers, and capitalized multi-word phrases (threshold: 2+ occurrences across messages)
2. **Symbolization** — top 50 entities receive `s_` prefix IDs via SHA-256 content-addressing (deterministic: same name always produces the same ID)
3. **Classification** — each sentence is classified as: decision, error, code_change, conclusion, or open_question based on pattern matching
4. **Truncation** — entries capped at 100 characters
5. **Prioritization** — sorted by category importance (decision > error > conclusion > code_change > open_question), then by confidence; capped at max(5, message_count) entries, ceiling 15
6. **Serialization** — emitted as `[ALEPH:MEMORY:1.0]` tag-based format

Target: 60%+ token reduction vs. raw transcript. Typical results: 70%+ on multi-turn debugging sessions.

### Resume Prompt Format

The decompressor (`aleph memory resume`) reconstructs a session-resume prompt in Markdown:

```markdown
## Prior Session State (Aleph Memory)

**Context:** Debugging TypeError in ETL pipeline after validation library v2.0 update.

### Decisions Made
- Use validation-lib==2.0.3 (pinned version)
- Created validate_schema_compat wrapper for gradual migration
- Added error handling to handle_upload for validation failures

### Key Learnings
- Root cause: validation library v2.0 changed strict parameter to mode='strict'
- Always check migration guides for breaking changes on dependency updates

### Code Changes
- Updated transform_records to use new mode='strict' parameter
- Created validate_schema_compat wrapper function

### Errors Encountered
- TypeError: validate_schema() got an unexpected keyword argument 'strict'

### Open Questions (Unresolved)
- Should we add retry logic to transform_records for batch jobs?
- Should we audit all dependencies for similar breaking changes?
```

Symbol IDs are expanded back to full names in the resume prompt. Empty sections are omitted.

**CLI usage:**
```bash
aleph memory resume -d .         # print resume prompt
aleph memory resume -d . --json  # output as JSON
```

### Storage

Compressed memories are stored in `project.aleph.epistemic` (JSON) under a `memories` array key, alongside `inferences`, `flags`, and `patches`:

```json
{
  "memories": [
    {
      "session_id": "2026-03-18T02:54:53Z",
      "timestamp": "2026-03-18T02:54:53Z",
      "message_count": 8,
      "original_tokens": 1248,
      "compressed_tokens": 354,
      "reduction_percent": 71.6,
      "context_summary": "Debugging TypeError in ETL pipeline...",
      "symbol_dict": {"s_e3ec18": "transform_records", ...},
      "entries": [...]
    }
  ],
  "inferences": [...],
  "flags": [...],
  "patches": [...]
}
```

Multiple sessions are appended; `aleph memory resume` loads the most recent one.

---

## What Aleph Cannot Do (and What You Must Do Yourself)

**Aleph provides:**
- Structure, relationships, and metadata
- Compressed bodies on demand
- Prior epistemic state
- Salience, temporal, and coverage metadata

**You must provide:**
- Judgment about what the information means for the current task
- Updates to the epistemic layer when you learn something
- The decision to expand bodies (Aleph tells you what's available; you decide what to load)
- Verification of flagged inferences (Aleph surfaces flags; you do the verification work)

The epistemic layer does not write itself. Every insight you reach and fail to record is lost at session end. Write inferences as you go, not as a cleanup step at the end.

---

## Quick Reference Card

`<angle>` = required. `[square]` = optional.

```
# Orientation
ALEPH:MAP                    → start here always
ALEPH:FS                     → filesystem layout
ALEPH:ATTENTION              → what matters, in what order
ALEPH:EPISTEMIC              → your prior state (load before anything else on resume)

# Understanding a symbol
ALEPH:RESOLVE <id>           → what is this?
ALEPH:EXPAND <id>            → full body
ALEPH:CALLERS <id>           → who depends on this?
ALEPH:DEPS <id>              → what does this depend on?
ALEPH:CONTEXT <id>           → neighborhood view
ALEPH:TEMPORAL [id]          → can I trust cached summaries?
ALEPH:SALIENCE [id]          → how important is this?

# Reliability
ALEPH:ERRORS <file>          → what can fail? *
ALEPH:TESTS <file>           → what's covered? *
ALEPH:COVERAGE               → project-wide risk surface

# Design intent
ALEPH:INTENTS <file>         → why does this exist? what must be true? *

# Updating your state
ALEPH:INFER <id> "..." <n>   → record a conclusion
ALEPH:FLAG <id> "..."        → mark something uncertain
ALEPH:VERIFY <id>            → mark a flag resolved

# Semantic patches
ALEPH:PATCH <id> { ... }     → propose an intent-level change
ALEPH:PATCH:LIST             → pending patches
ALEPH:PATCH:APPLY <id>       → generate concrete implementation

# Memory (conversation compression)
ALEPH:MEMORY:COMPRESS <file> → compress a conversation transcript
ALEPH:MEMORY:RESUME          → load prior session state
```

\* Requires `aleph build --per-file`

---

## The Point

You are expensive to run. Every token you spend reconstructing context you've already established is waste. Aleph is the infrastructure that makes that waste unnecessary.

Use the epistemic layer. Write your conclusions down. Load structure before bodies. Check salience before modifying. Check temporal before trusting.

The goal is not to read less. It is to reason better with the same context budget.

---

*Aleph v0.3 — March 2026*
*For the protocol specification: CONSTITUTION.md*
*For the implementation plan: PLAN.md + Implementationplan.md*
