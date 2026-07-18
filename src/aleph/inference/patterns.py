"""Language-specific AST pattern definitions for intent and error inference."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IntentPattern:
    """A pattern for inferring intent from AST nodes."""
    node_type: str          # tree-sitter node type to match
    tag_type: str           # INTENT | PRECONDITION | POSTCONDITION | INVARIANT
    description: str        # human-readable description
    confidence: str         # inferred:high | inferred:medium
    text_match: str = ""    # optional text content to match within the node


# ── C++ Patterns ──

CPP_INTENT_PATTERNS = [
    IntentPattern(
        node_type="call_expression",
        tag_type="PRECONDITION",
        description="assert() call",
        confidence="inferred:high",
        text_match="assert",
    ),
    IntentPattern(
        node_type="throw_statement",
        tag_type="PRECONDITION",
        description="throw in function body",
        confidence="inferred:high",
    ),
    IntentPattern(
        node_type="try_statement",
        tag_type="INTENT",
        description="error-boundary",
        confidence="inferred:medium",
    ),
]

CPP_QUALIFIER_PATTERNS = [
    # const/constexpr → INVARIANT:immutable
    IntentPattern(
        node_type="type_qualifier",
        tag_type="INVARIANT",
        description="immutable",
        confidence="inferred:high",
        text_match="const",
    ),
]

# ── Rust Patterns ──

RUST_INTENT_PATTERNS = [
    IntentPattern(
        node_type="macro_invocation",
        tag_type="PRECONDITION",
        description="assert macro",
        confidence="inferred:high",
        text_match="assert",
    ),
    IntentPattern(
        node_type="macro_invocation",
        tag_type="PRECONDITION",
        description="debug_assert macro",
        confidence="inferred:high",
        text_match="debug_assert",
    ),
    IntentPattern(
        node_type="unsafe_block",
        tag_type="INTENT",
        description="unsafe:reason-unknown",
        confidence="inferred:medium",
    ),
    IntentPattern(
        node_type="attribute_item",
        tag_type="INTENT",
        description="perf-critical",
        confidence="inferred:medium",
        text_match="bench",
    ),
]

# ── Python Patterns ──

PYTHON_INTENT_PATTERNS = [
    IntentPattern(
        node_type="assert_statement",
        tag_type="PRECONDITION",
        description="assert statement",
        confidence="inferred:high",
    ),
    IntentPattern(
        node_type="try_statement",
        tag_type="INTENT",
        description="error-boundary",
        confidence="inferred:medium",
    ),
]

PYTHON_DECORATOR_PATTERNS = [
    IntentPattern(
        node_type="decorator",
        tag_type="INTENT",
        description="accessor",
        confidence="inferred:medium",
        text_match="property",
    ),
    IntentPattern(
        node_type="decorator",
        tag_type="INTENT",
        description="static",
        confidence="inferred:medium",
        text_match="staticmethod",
    ),
]

# Language → patterns mapping
INTENT_PATTERNS: dict[str, list[IntentPattern]] = {
    "cpp": CPP_INTENT_PATTERNS + CPP_QUALIFIER_PATTERNS,
    "rust": RUST_INTENT_PATTERNS,
    "python": PYTHON_INTENT_PATTERNS + PYTHON_DECORATOR_PATTERNS,
}

# Error-raising node types per language
ERROR_RAISE_TYPES: dict[str, list[str]] = {
    "cpp": ["throw_statement"],
    "rust": ["macro_invocation"],  # panic!, bail! etc.; also ? operator via try_expression
    "python": ["raise_statement"],
}

ERROR_BOUNDARY_TYPES: dict[str, list[str]] = {
    "cpp": ["try_statement"],
    "rust": ["match_expression"],  # matching on Result/Option
    "python": ["try_statement"],
}

# Test function detection patterns
TEST_PATTERNS: dict[str, dict] = {
    "cpp": {
        "node_type": "call_expression",
        "text_match": ["TEST", "TEST_F", "TEST_P"],
    },
    "rust": {
        "attribute": "test",
    },
    "python": {
        "name_prefix": "test_",
        "file_prefix": "test_",
    },
}
