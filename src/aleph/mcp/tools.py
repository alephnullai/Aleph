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
            "Start here always. Returns all files, semantic hashes, "
            "and token statistics."
        ),
    },
    {
        "name": "aleph_struct",
        "description": (
            "ALEPH:STRUCT — Call graph, signatures, hierarchy. "
            "Returns project-level cross-file structure, or file-level "
            "structure if a file path is given."
        ),
        "parameters": {
            "file": {
                "type": "string",
                "description": "Optional file path to get file-level struct instead of project-level.",
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
            "Surfaces high-salience symbols with no test coverage."
        ),
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
                "description": "Symbol ID to expand (e.g. f_a3c9).",
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
                "description": "Symbol ID to resolve (e.g. f_a3c9).",
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
                "description": "Symbol ID to find callers for.",
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
                "description": "Symbol ID to get context for.",
            },
        },
        "required": ["symbol_id"],
    },
    {
        "name": "aleph_search",
        "description": (
            "ALEPH:SEARCH — Symbols matching a search term. "
            "Performs semantic matching against symbol names and qualified names."
        ),
        "parameters": {
            "term": {
                "type": "string",
                "description": "Search term (name, intent, or partial match).",
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
            "symbols ranked by importance."
        ),
    },
    {
        "name": "aleph_salience",
        "description": (
            "ALEPH:SALIENCE — How load-bearing a symbol is (0-1). "
            "Returns salience scores. If a symbol_id is given, returns only that symbol's score."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Optional symbol ID to get salience for a specific symbol.",
            },
        },
    },
    {
        "name": "aleph_temporal",
        "description": (
            "ALEPH:TEMPORAL — Age, churn rate, stability class. "
            "Returns temporal metadata. If a symbol_id is given, returns only that symbol."
        ),
        "parameters": {
            "symbol_id": {
                "type": "string",
                "description": "Optional symbol ID to get temporal data for a specific symbol.",
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
            "and the symbol's semantic hash at creation time."
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
            "Validates that the target symbol hash has not changed since "
            "patch creation. If hash changed, use force=true to apply anyway. "
            "Phase 3.4: renders intent as a TODO comment block."
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
            "Shows all source files with language, symbol count, and directory structure."
        ),
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
                "description": "Symbol ID to analyze impact for.",
            },
        },
        "required": ["symbol_id"],
    },
    # ── Context ──
    {
        "name": "aleph_brief",
        "description": (
            "ALEPH:BRIEF — Task-aware context optimizer. Describe your task "
            "in natural language and get a curated briefing."
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
            "ALEPH:WORKSPACE:SEARCH — Search across all projects in the workspace."
        ),
        "parameters": {
            "term": {
                "type": "string",
                "description": "Search term.",
            },
        },
        "required": ["term"],
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
