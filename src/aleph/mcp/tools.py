"""MCP tool definitions for the ALEPH: interaction protocol.

Each ALEPH: command becomes an MCP tool with a name, description,
and input schema derived from the protocol specification.
"""

from __future__ import annotations

# Tool definitions: (name, description, parameters)
# Parameters use JSON Schema format compatible with MCP.

TOOL_DEFINITIONS: list[dict] = [
    # ── Navigation ──
    {
        "name": "aleph_map",
        "description": (
            "ALEPH:MAP — Project manifest and component list. "
            "Start here always. Returns files, semantic hashes, "
            "and token statistics. Shows at most `limit` files (default 200) "
            "with a truncation note; projects over 500 files return a "
            "directory-level rollup unless path_prefix is given."
        ),
        "parameters": {
            "path_prefix": {
                "type": "string",
                "description": "Optional path prefix to filter files (e.g. 'src/aleph/mcp').",
            },
            "limit": {
                "type": "integer",
                "description": "Max files to return (default 200; output notes truncation).",
            },
        },
    },
    {
        "name": "aleph_struct",
        "description": (
            "ALEPH:STRUCT — Call graph, signatures, hierarchy. "
            "Returns project-level cross-file structure, or file-level "
            "structure if a file path is given. Truncated at `limit` entries."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Optional file path to get file-level struct instead of project-level.",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    {
        "name": "aleph_bodies",
        "description": (
            "ALEPH:BODIES — Compressed function bodies for a file. "
            "Returns FULL, SUMMARY, or OMIT entries for all symbols in the file."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Source file path to retrieve bodies for.",
            },
        },
        "required": ["file"],
    },
    {
        "name": "aleph_errors",
        "description": (
            "ALEPH:ERRORS — Error flow layer for a file. "
            "Shows error sources, boundaries, propagation, and unhandled errors."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Source file path to retrieve error flows for.",
            },
        },
        "required": ["file"],
    },
    {
        "name": "aleph_intents",
        "description": (
            "ALEPH:INTENTS — Intent and invariant annotations for a file. "
            "Shows why things exist and what must be true."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Source file path to retrieve intents for.",
            },
        },
        "required": ["file"],
    },
    {
        "name": "aleph_tests",
        "description": (
            "ALEPH:TESTS — Test coverage map for a file. "
            "Shows which behaviors are tested and which are dark."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Source file path to retrieve test coverage for.",
            },
        },
        "required": ["file"],
    },
    {
        "name": "aleph_coverage",
        "description": (
            "ALEPH:COVERAGE — Project-wide test coverage and high-risk gaps. "
            "Surfaces high-salience symbols with no test coverage. "
            "Truncated at `limit` entries."
        ),
        "parameters": {
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    # ── Resolution ──
    {
        "name": "aleph_expand",
        "description": (
            "ALEPH:EXPAND — Full body of a symbol. "
            "Retrieves the complete function/method body for a given symbol ID."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID (e.g. f_a3c9) OR a symbol name — a "
                "name is auto-resolved (ambiguous names return candidates).",
            },
        },
        "required": ["symbol_id"],
    },
    {
        "name": "aleph_resolve",
        "description": (
            "ALEPH:RESOLVE — Dictionary entry for a symbol. "
            "Returns name, kind, file, signature for a symbol ID."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID (e.g. f_a3c9) OR a symbol name — a "
                "name is auto-resolved (ambiguous names return candidates).",
            },
        },
        "required": ["symbol_id"],
    },
    {
        "name": "aleph_callers",
        "description": (
            "ALEPH:CALLERS — All symbols that call or reference this one. "
            "Returns a list of caller symbol IDs with names and files."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID OR a symbol name to find callers for "
                "— a name is auto-resolved (ambiguous names return candidates).",
            },
        },
        "required": ["symbol_id"],
    },
    {
        "name": "aleph_context",
        "description": (
            "ALEPH:CONTEXT — Symbol plus immediate call neighborhood. "
            "Returns the symbol info, its callers, and its callees."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID OR a symbol name to get context for "
                "— a name is auto-resolved (ambiguous names return candidates).",
            },
        },
        "required": ["symbol_id"],
    },
    {
        "name": "aleph_search",
        "description": (
            "ALEPH:SEARCH — Lexical identifier search over symbol names, "
            "qualified names, and file path components. Splits camelCase/"
            "snake_case into subtokens and ranks by match quality and token "
            "rarity (exact > prefix > subtoken; not semantic search). "
            "Returns at most `limit` results (default 25) with a note when "
            "more matches exist."
        ),
        "parameters": {
            "term": {
                "type": "string",
                "description": "Search term (name, intent, or partial match).",
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default 25; output notes remaining matches).",
            },
        },
        "required": ["term"],
    },
    # ── Priority ──
    {
        "name": "aleph_attention",
        "description": (
            "ALEPH:ATTENTION — Recommended load order. "
            "Start here on unfamiliar projects. Returns attention budget and "
            "symbols ranked by importance. Truncated at `limit` entries."
        ),
        "parameters": {
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    {
        "name": "aleph_salience",
        "description": (
            "ALEPH:SALIENCE — How load-bearing a symbol is (0-1). "
            "Returns salience scores. If a symbol_id is given, returns only "
            "that symbol's score; otherwise truncated at `limit` entries."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Optional symbol ID to get salience for a specific symbol.",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    {
        "name": "aleph_temporal",
        "description": (
            "ALEPH:TEMPORAL — Age, churn rate, stability class. "
            "Returns temporal metadata. If a symbol_id is given, returns only "
            "that symbol; otherwise truncated at `limit` entries."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Optional symbol ID to get temporal data for a specific symbol.",
            },
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    # ── Epistemic ──
    {
        "name": "aleph_epistemic",
        "description": (
            "ALEPH:EPISTEMIC — Cached inferences and flags. "
            "Returns your prior reasoning state. Load this first when resuming a session."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Optional symbol ID to filter inferences/flags to one symbol.",
            },
        },
    },
    {
        "name": "aleph_infer",
        "description": (
            "ALEPH:INFER — Record a new inference about a symbol. "
            "Writes a conclusion with a confidence score to the epistemic layer."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID the inference is about.",
            },
            "conclusion": {
                "type": "string",
                "description": "The inference conclusion text.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score from 0.0 to 1.0.",
            },
        },
        "required": ["symbol_id", "conclusion", "confidence"],
    },
    {
        "name": "aleph_flag",
        "description": (
            "ALEPH:FLAG — Flag a symbol as uncertain or needing verification. "
            "Use when you are unsure and should not act without checking."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID to flag.",
            },
            "reason": {
                "type": "string",
                "description": "Why this symbol needs verification.",
            },
        },
        "required": ["symbol_id", "reason"],
    },
    {
        "name": "aleph_verify",
        "description": (
            "ALEPH:VERIFY — Mark a flagged inference as verified. "
            "Clears the uncertainty flag on a symbol."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID to mark as verified.",
            },
        },
        "required": ["symbol_id"],
    },
    # ── Memory Resume ──
    {
        "name": "aleph_memory_resume",
        "description": (
            "ALEPH:MEMORY:RESUME — Session briefing: top inferences by confidence, "
            "all flags, pending patches, key decisions and learnings. "
            "Use this to quickly restore prior session state."
        ),
        "parameters": {},
    },
    # ── Patches ──
    {
        "name": "aleph_patch_propose",
        "description": (
            "ALEPH:PATCH — Propose a semantic change to a symbol. "
            "Creates a pending semantic patch with intent, target symbol, "
            "and the symbol's semantic hash at creation time. Propose "
            "records any language, but apply currently supports Python only."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID to patch (e.g. f_a3c9).",
            },
            "intent": {
                "type": "string",
                "description": "Description of the intended semantic change.",
            },
            "file": {
                "type": "string",
                "description": "Optional source file override (resolved automatically if omitted).",
            },
        },
        "required": ["symbol_id", "intent"],
    },
    {
        "name": "aleph_patch",
        "description": (
            "ALEPH:PATCH — Propose a semantic change to a symbol (alias). "
            "Creates a pending semantic patch describing the intended change."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID to patch.",
            },
            "patch_body": {
                "type": "string",
                "description": "Description of the semantic change.",
            },
        },
        "required": ["symbol_id", "patch_body"],
    },
    {
        "name": "aleph_patch_list",
        "description": (
            "ALEPH:PATCH:LIST — List all pending semantic patches. "
            "Shows patch ID, target symbol, intent, semantic hash, and file."
        ),
    },
    {
        "name": "aleph_patch_apply",
        "description": (
            "ALEPH:PATCH:APPLY — Apply a semantic patch to the source file. "
            "Python only. Locates the target via the symbol's recorded span "
            "(file + line range from the dictionary), disambiguates duplicate "
            "names by qualified name, and errors out listing candidates when "
            "still ambiguous. Validates that the target symbol hash has not "
            "changed since patch creation; if it changed, use force=true. "
            "Renders intent as a TODO comment block."
        ),
        "parameters": {
            "patch_id": {
                "type": "string",
                "description": "ID of the patch to apply (e.g. patch_1).",
            },
            "force": {
                "type": "boolean",
                "description": "Force apply even if semantic hash has changed (default: false).",
            },
        },
        "required": ["patch_id"],
    },
    {
        "name": "aleph_patch_reject",
        "description": (
            "ALEPH:PATCH:REJECT — Mark a pending patch as rejected/abandoned. "
            "The patch remains in the epistemic layer for audit trail."
        ),
        "parameters": {
            "patch_id": {
                "type": "string",
                "description": "ID of the patch to reject (e.g. patch_1).",
            },
        },
        "required": ["patch_id"],
    },
    # ── Navigation (additional) ──
    {
        "name": "aleph_fs",
        "description": (
            "ALEPH:FS — Filesystem layout and module boundaries. "
            "Shows source files with language, symbol count, and directory "
            "structure. Truncated at `limit` entries."
        ),
        "parameters": {
            "limit": {
                "type": "integer",
                "description": "Max entries to return (default 100; output notes truncation).",
            },
        },
    },
    # ── Safety ──
    {
        "name": "aleph_impact",
        "description": (
            "ALEPH:IMPACT — Pre-modification change impact analysis. "
            "Shows direct callers, transitive impact (2 hops), risk "
            "assessment, and suggested test targets."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Symbol ID OR a symbol name to analyze impact "
                "for — a name is auto-resolved (ambiguous names return candidates).",
            },
        },
        "required": ["symbol_id"],
    },
    # ── Context ──
    {
        "name": "aleph_brief",
        "description": (
            "ALEPH:BRIEF — Task briefing built from lexical identifier "
            "matching blended with structural salience (call-graph fan-in). "
            "Works best with identifier-like terms; not semantic search."
        ),
        "parameters": {
            "task": {
                "type": "string",
                "description": "Natural language description of your task.",
            },
        },
        "required": ["task"],
    },
    # ── Session ──
    {
        "name": "aleph_session_summary",
        "description": (
            "ALEPH:SESSION — Summarize this session's queries and save "
            "a review trail to the epistemic layer."
        ),
    },
    # ── Workspace ──
    {
        "name": "aleph_workspace_search",
        "description": (
            "ALEPH:WORKSPACE:SEARCH — Search across all projects in the workspace. "
            "Returns at most `limit` results plus per-project warnings for "
            "missing/corrupt artifacts."
        ),
        "parameters": {
            "term": {
                "type": "string",
                "description": "Search term.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return (default 25).",
            },
        },
        "required": ["term"],
    },
    {
        "name": "aleph_workspace_status",
        "description": (
            "ALEPH:WORKSPACE:STATUS — Per-project build/staleness report: "
            "built or not, source file counts, last build time, and how many "
            "sources are newer than the artifacts."
        ),
    },
    {
        "name": "aleph_workspace_brief",
        "description": (
            "ALEPH:WORKSPACE:BRIEF — Task-aware briefing across all workspace projects."
        ),
        "parameters": {
            "task": {
                "type": "string",
                "description": "Natural language description of your task.",
            },
        },
        "required": ["task"],
    },
]
