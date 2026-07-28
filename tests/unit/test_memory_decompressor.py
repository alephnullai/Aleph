"""Tests for aleph.memory.decompressor — memory decompression and resume."""

from __future__ import annotations

import json
import os

from aleph.memory.compressor import (
    CompressedMemory,
    MemoryEntry,
    compress_transcript,
    serialize_memory,
)
from aleph.memory.decompressor import (
    ResumeContext,
    decompress_memory,
    decompress_from_text,
    parse_memory_text,
    _expand_symbols,
    _strip_metadata,
)

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "conversations"
)


def _load_fixture(name: str) -> list[dict[str, str]]:
    path = os.path.join(FIXTURES_DIR, name)
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict) and "messages" in data:
        return data["messages"]
    return data


# ── Symbol expansion ──


class TestExpandSymbols:
    def test_expands_symbols(self):
        text = "Updated s_abc123 to use new API"
        result = _expand_symbols(text, {"s_abc123": "validate_schema"})
        assert result == "Updated validate_schema to use new API"

    def test_no_symbols(self):
        text = "No symbols here"
        assert _expand_symbols(text, {}) == text

    def test_multiple_symbols(self):
        text = "s_aaa called s_bbb"
        result = _expand_symbols(text, {"s_aaa": "foo", "s_bbb": "bar"})
        assert result == "foo called bar"


class TestStripMetadata:
    def test_strips_confidence(self):
        assert _strip_metadata("Use Redis [0.9]") == "Use Redis"

    def test_strips_refs(self):
        assert _strip_metadata("Use Redis refs=s_abc") == "Use Redis"

    def test_strips_both(self):
        assert _strip_metadata("Use Redis [0.9] refs=s_abc") == "Use Redis"

    def test_no_metadata(self):
        assert _strip_metadata("Plain text") == "Plain text"


# ── Decompression from object ──


class TestDecompressMemory:
    def test_empty_memory(self):
        memory = CompressedMemory()
        ctx = decompress_memory(memory)
        assert ctx.decisions == []
        assert ctx.conclusions == []

    def test_preserves_decisions(self):
        memory = CompressedMemory(
            entries=[
                MemoryEntry(category="decision", content="Use Redis for caching"),
                MemoryEntry(category="decision", content="Pin validation-lib version"),
            ]
        )
        ctx = decompress_memory(memory)
        assert len(ctx.decisions) == 2
        assert "Redis" in ctx.decisions[0]

    def test_preserves_all_categories(self):
        memory = CompressedMemory(
            entries=[
                MemoryEntry(category="decision", content="Decided X"),
                MemoryEntry(category="conclusion", content="Learned Y"),
                MemoryEntry(category="open_question", content="What about Z?"),
                MemoryEntry(category="code_change", content="Changed A"),
                MemoryEntry(category="error", content="Error in B"),
            ]
        )
        ctx = decompress_memory(memory)
        assert len(ctx.decisions) == 1
        assert len(ctx.conclusions) == 1
        assert len(ctx.open_questions) == 1
        assert len(ctx.code_changes) == 1
        assert len(ctx.errors) == 1

    def test_expands_symbols_in_entries(self):
        memory = CompressedMemory(
            entries=[
                MemoryEntry(
                    category="decision",
                    content="Use s_abc123 for caching",
                    symbol_refs=["s_abc123"],
                ),
            ],
            symbol_dict={"s_abc123": "Redis"},
        )
        ctx = decompress_memory(memory)
        assert "Redis" in ctx.decisions[0]
        assert "s_abc123" not in ctx.decisions[0]

    def test_context_summary_preserved(self):
        memory = CompressedMemory(context_summary="Task: fix the pipeline")
        ctx = decompress_memory(memory)
        assert ctx.context_summary == "Task: fix the pipeline"


# ── Resume prompt generation ──


class TestResumePrompt:
    def test_generates_markdown(self):
        ctx = ResumeContext(
            context_summary="Fix validation library update",
            decisions=["Use validation-lib==2.0.3"],
            conclusions=["Root cause was breaking API change"],
            open_questions=["Should we add retry logic?"],
            code_changes=["Updated transform_records function"],
            errors=["TypeError in validate_schema"],
        )
        prompt = ctx.to_prompt()

        assert "## Prior Session State" in prompt
        assert "### Decisions Made" in prompt
        assert "### Key Learnings" in prompt
        assert "### Open Questions" in prompt
        assert "### Code Changes" in prompt
        assert "### Errors Encountered" in prompt
        assert "validation-lib==2.0.3" in prompt

    def test_empty_sections_omitted(self):
        ctx = ResumeContext(
            decisions=["Only decisions"],
        )
        prompt = ctx.to_prompt()
        assert "### Decisions Made" in prompt
        assert "### Key Learnings" not in prompt
        assert "### Errors" not in prompt


# ── Text format roundtrip ──


class TestParseMemoryText:
    def test_roundtrip(self):
        original = CompressedMemory(
            entries=[
                MemoryEntry(category="decision", content="Use Redis", confidence=0.9),
                MemoryEntry(category="error", content="Pipeline failed", confidence=0.85),
            ],
            symbol_dict={"s_abc123": "validate_schema"},
            context_summary="Debugging pipeline error",
            original_token_estimate=1000,
            compressed_token_estimate=300,
            message_count=5,
        )
        text = serialize_memory(original)
        parsed = parse_memory_text(text)

        assert parsed.message_count == 5
        assert parsed.original_token_estimate == 1000
        assert parsed.compressed_token_estimate == 300
        assert parsed.context_summary == "Debugging pipeline error"
        assert "s_abc123" in parsed.symbol_dict
        assert parsed.symbol_dict["s_abc123"] == "validate_schema"

        decisions = [e for e in parsed.entries if e.category == "decision"]
        assert len(decisions) == 1
        assert "Use Redis" in decisions[0].content

        errors = [e for e in parsed.entries if e.category == "error"]
        assert len(errors) == 1

    def test_decompress_from_text(self):
        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)
        text = serialize_memory(memory)

        ctx = decompress_from_text(text)
        # Should have some content in the resume
        total_items = (
            len(ctx.decisions)
            + len(ctx.conclusions)
            + len(ctx.open_questions)
            + len(ctx.code_changes)
            + len(ctx.errors)
        )
        assert total_items > 0

    def test_full_roundtrip_fidelity(self):
        """Compress -> serialize -> parse -> decompress preserves key info."""
        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)

        # Direct decompress
        ctx_direct = decompress_memory(memory)

        # Roundtrip through text
        text = serialize_memory(memory)
        ctx_roundtrip = decompress_from_text(text)

        # Both should have decisions and errors (counts may differ slightly
        # due to text format splitting multi-line entries)
        assert len(ctx_roundtrip.decisions) > 0
        assert len(ctx_direct.decisions) > 0
        assert len(ctx_roundtrip.errors) > 0 or len(ctx_direct.errors) > 0

    def test_feature_session_roundtrip(self):
        messages = _load_fixture("feature_session.json")
        memory = compress_transcript(messages)
        text = serialize_memory(memory)
        ctx = decompress_from_text(text)

        prompt = ctx.to_prompt()
        assert "## Prior Session State" in prompt
        assert len(prompt) > 50
