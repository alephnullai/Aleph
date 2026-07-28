"""Tests for aleph.memory.compressor — conversation transcript compression."""

from __future__ import annotations

import json
import os
import pytest

from aleph.memory.compressor import (
    CompressedMemory,
    MemoryEntry,
    compress_transcript,
    serialize_memory,
    _extract_entities,
    _classify_message_content,
    _make_symbol_id,
    _estimate_tokens,
    _deduplicate_entries,
    _remove_resolved_questions,
    _build_context_summary,
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


# ── Symbol ID generation ──


class TestSymbolIDs:
    def test_stable_ids(self):
        """Same input produces same ID."""
        assert _make_symbol_id("validate_schema") == _make_symbol_id("validate_schema")

    def test_different_inputs_different_ids(self):
        assert _make_symbol_id("foo") != _make_symbol_id("bar")

    def test_format(self):
        sid = _make_symbol_id("test_concept")
        assert sid.startswith("s_")
        assert len(sid) == 8  # s_ + 6 hex chars


# ── Token estimation ──


class TestTokenEstimation:
    def test_empty(self):
        assert _estimate_tokens("") == 1

    def test_short_text(self):
        tokens = _estimate_tokens("Hello world, this is a test.")
        assert tokens > 0
        assert tokens < 20

    def test_scales_with_length(self):
        short = _estimate_tokens("short")
        long = _estimate_tokens("a" * 400)
        assert long > short


# ── Entity extraction ──


class TestEntityExtraction:
    def test_extracts_backtick_entities(self):
        messages = [
            {"content": "The `validate_schema` function is broken"},
            {"content": "I fixed `validate_schema` to use the new API"},
        ]
        entities = _extract_entities(messages)
        assert "validate_schema" in entities
        assert entities["validate_schema"] >= 2

    def test_extracts_snake_case_terms(self):
        messages = [
            {"content": "The transform_records function failed again"},
            {"content": "I updated transform_records to handle errors"},
        ]
        entities = _extract_entities(messages)
        assert "transform_records" in entities

    def test_ignores_single_occurrence(self):
        messages = [{"content": "The unique_concept only appears once"}]
        entities = _extract_entities(messages)
        assert "unique_concept" not in entities

    def test_extracts_camel_case(self):
        messages = [
            {"content": "The getUserProfile endpoint is slow"},
            {"content": "Fixed getUserProfile to use caching"},
        ]
        entities = _extract_entities(messages)
        assert "getUserProfile" in entities

    def test_extracts_file_names(self):
        messages = [
            {"content": "Check the config.yaml file for settings"},
            {"content": "Updated config.yaml with new values"},
        ]
        entities = _extract_entities(messages)
        assert "config.yaml" in entities

    def test_backtick_single_occurrence(self):
        """Backtick-quoted entities should be kept even at 1 occurrence."""
        messages = [{"content": "The `validate_schema` function is important"}]
        entities = _extract_entities(messages)
        assert "validate_schema" in entities

    def test_bare_single_occurrence_still_excluded(self):
        """Non-backtick entities at 1 occurrence should still be excluded."""
        messages = [{"content": "The unique_concept_here only appears once"}]
        entities = _extract_entities(messages)
        assert "unique_concept_here" not in entities

    def test_file_names_kept_at_single_occurrence(self):
        """File names should be kept even at 1 occurrence for completeness."""
        messages = [{"content": "Updated CONSUMER_GUIDE.md with the new format spec"}]
        entities = _extract_entities(messages)
        assert "CONSUMER_GUIDE.md" in entities


# ── Message classification ──


class TestMessageClassification:
    def test_detects_decisions(self):
        entries = _classify_message_content(
            "We decided to use Redis for caching. It's the best option.", "assistant", 0
        )
        decisions = [e for e in entries if e.category == "decision"]
        assert len(decisions) >= 1
        assert any("Redis" in d.content for d in decisions)

    def test_detects_errors(self):
        entries = _classify_message_content(
            "Error: the pipeline failed with a TypeError.", "user", 0
        )
        errors = [e for e in entries if e.category == "error"]
        assert len(errors) >= 1

    def test_detects_code_changes(self):
        entries = _classify_message_content(
            "I added a new `cache_service` module for Redis integration.", "assistant", 0
        )
        changes = [e for e in entries if e.category == "code_change"]
        assert len(changes) >= 1

    def test_detects_open_questions(self):
        entries = _classify_message_content(
            "Should we add retry logic to the transform function?", "user", 0
        )
        questions = [e for e in entries if e.category == "open_question"]
        assert len(questions) >= 1

    def test_detects_conclusions(self):
        entries = _classify_message_content(
            "The root cause was the validation library breaking change.", "assistant", 0
        )
        conclusions = [e for e in entries if e.category == "conclusion"]
        assert len(conclusions) >= 1

    def test_confidence_scores(self):
        entries = _classify_message_content(
            "We decided to pin the version. Not sure about retry logic?", "assistant", 0
        )
        decisions = [e for e in entries if e.category == "decision"]
        questions = [e for e in entries if e.category == "open_question"]
        if decisions and questions:
            assert decisions[0].confidence > questions[0].confidence

    def test_user_question_not_decision(self):
        """User questions containing 'we should' must not be classified as decisions."""
        entries = _classify_message_content(
            "Our past run may have improvements we should address first?", "user", 0
        )
        decisions = [e for e in entries if e.category == "decision"]
        questions = [e for e in entries if e.category == "open_question"]
        assert len(decisions) == 0
        assert len(questions) >= 1

    def test_assistant_we_should_is_decision(self):
        """'We should' from assistant in a statement is a decision."""
        entries = _classify_message_content(
            "We should add error handling to the upload endpoint.", "assistant", 0
        )
        decisions = [e for e in entries if e.category == "decision"]
        assert len(decisions) >= 1

    def test_detects_doc_code_changes(self):
        """Documentation changes should be classified as code_change."""
        entries = _classify_message_content(
            "Updated the API documentation to reflect the new schema.", "assistant", 0
        )
        changes = [e for e in entries if e.category == "code_change"]
        assert len(changes) >= 1

    def test_question_not_code_change(self):
        """User question about updates should not be classified as code_change."""
        entries = _classify_message_content(
            "Have we updated the files and noted discrepancies?", "user", 0
        )
        changes = [e for e in entries if e.category == "code_change"]
        assert len(changes) == 0

    def test_recommendation_is_decision(self):
        """Assistant recommendations should be classified as decisions."""
        entries = _classify_message_content(
            "Recommendation: address the feedback log first, then Phase 3.3.", "assistant", 0
        )
        decisions = [e for e in entries if e.category == "decision"]
        assert len(decisions) >= 1


# ── Deduplication ──


class TestDeduplication:
    def test_removes_duplicates(self):
        entries = [
            MemoryEntry(category="decision", content="Use Redis for caching", confidence=0.8),
            MemoryEntry(category="decision", content="Use Redis for caching.", confidence=0.9),
        ]
        result = _deduplicate_entries(entries)
        assert len(result) == 1
        assert result[0].confidence == 0.9

    def test_keeps_distinct_entries(self):
        entries = [
            MemoryEntry(category="decision", content="Use Redis"),
            MemoryEntry(category="decision", content="Pin the library version"),
        ]
        result = _deduplicate_entries(entries)
        assert len(result) == 2


# ── Full compression pipeline ──


class TestCompressTranscript:
    def test_empty_messages(self):
        memory = compress_transcript([])
        assert memory.message_count == 0
        assert memory.entries == []

    def test_single_message(self):
        messages = [{"role": "user", "content": "Hello, I need help with a bug."}]
        memory = compress_transcript(messages)
        assert memory.message_count == 1
        assert memory.original_token_estimate > 0

    def test_debug_session_fixture(self):
        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)

        assert memory.message_count == len(messages)
        assert len(memory.entries) > 0
        assert memory.original_token_estimate > 0
        assert memory.compressed_token_estimate > 0
        assert memory.context_summary != ""

        # Should have extracted some decisions
        decisions = [e for e in memory.entries if e.category == "decision"]
        assert len(decisions) >= 1

        # Should have extracted some errors
        errors = [e for e in memory.entries if e.category == "error"]
        assert len(errors) >= 1

    def test_feature_session_fixture(self):
        messages = _load_fixture("feature_session.json")
        memory = compress_transcript(messages)

        assert memory.message_count == len(messages)
        assert len(memory.entries) > 0

        # Should identify recurring concepts
        assert len(memory.symbol_dict) > 0

    def test_compression_target_debug(self):
        """Compression should achieve 60%+ reduction on a real conversation."""
        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)
        assert memory.reduction_percent >= 60.0, (
            f"Expected 60%+ reduction, got {memory.reduction_percent:.1f}%"
        )

    def test_compression_target_feature(self):
        """Compression should achieve 60%+ reduction on a real conversation."""
        messages = _load_fixture("feature_session.json")
        memory = compress_transcript(messages)
        assert memory.reduction_percent >= 60.0, (
            f"Expected 60%+ reduction, got {memory.reduction_percent:.1f}%"
        )

    def test_symbol_dict_populated(self):
        messages = [
            {"role": "user", "content": "The validate_schema function is broken"},
            {"role": "assistant", "content": "I found that validate_schema needs updating"},
            {"role": "user", "content": "Also check validate_schema in the tests"},
        ]
        memory = compress_transcript(messages)
        # validate_schema appears 3x, should be symbolized
        assert any("validate_schema" in v for v in memory.symbol_dict.values())


# ── Serialization ──


class TestSerializeMemory:
    def test_roundtrip_header(self):
        memory = CompressedMemory(message_count=3)
        text = serialize_memory(memory)
        assert text.startswith("[ALEPH:MEMORY:1.0]")

    def test_includes_stats(self):
        memory = CompressedMemory(
            message_count=5,
            original_token_estimate=1000,
            compressed_token_estimate=300,
        )
        text = serialize_memory(memory)
        assert "messages=5" in text
        assert "original_tokens=1000" in text

    def test_includes_entries(self):
        memory = CompressedMemory(
            entries=[
                MemoryEntry(category="decision", content="Use Redis", confidence=0.9),
                MemoryEntry(category="error", content="Pipeline failed", confidence=0.85),
            ]
        )
        text = serialize_memory(memory)
        assert "[DECISIONS]" in text
        assert "Use Redis" in text
        assert "[ERRORS_ENCOUNTERED]" in text
        assert "Pipeline failed" in text

    def test_includes_dict(self):
        memory = CompressedMemory(
            symbol_dict={"s_abc123": "validate_schema"},
        )
        text = serialize_memory(memory)
        assert "[DICT]" in text
        assert "s_abc123=validate_schema" in text

    def test_full_fixture_serialization(self):
        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)
        text = serialize_memory(memory)

        assert "[ALEPH:MEMORY:1.0]" in text
        assert "[STATS]" in text
        assert "[/STATS]" in text
        assert "[CONTEXT]" in text


# ── Context extraction ──


class TestContextExtraction:
    def test_context_short_first_message(self):
        """Short first message should pull context from subsequent messages."""
        messages = [
            {"role": "user", "content": "resume"},
            {"role": "assistant", "content": "The project is at Phase 3.2 with 728 tests."},
            {"role": "user", "content": "Which phase makes sense to tackle next for improving code quality?"},
        ]
        summary = _build_context_summary(messages)
        assert "resume" not in summary.lower() or len(summary) > 30
        # Should have pulled from a later, more substantive message
        assert len(summary) > 15

    def test_context_normal_first_message(self):
        """Long first user message should be used directly."""
        messages = [
            {"role": "user", "content": "I need to add a caching layer to our user profile API. The endpoint is too slow."},
            {"role": "assistant", "content": "I'll implement Redis caching."},
        ]
        summary = _build_context_summary(messages)
        assert "caching" in summary.lower()

    def test_context_from_assistant_when_user_ambiguous(self):
        """Falls back to assistant task description when user messages are short."""
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "I can see the issue with the authentication middleware."},
            {"role": "user", "content": "yes fix it"},
        ]
        summary = _build_context_summary(messages)
        assert len(summary) > 15


# ── Resolved question detection ──


class TestResolvedQuestions:
    def test_resolved_questions_removed(self):
        """Questions answered by later decisions should be removed."""
        entries = [
            MemoryEntry(category="open_question", content="Should we use Redis for caching?",
                       confidence=0.6, source_turn=0),
            MemoryEntry(category="decision", content="Decided to use Redis for caching layer.",
                       confidence=0.9, source_turn=2),
        ]
        result = _remove_resolved_questions(entries)
        questions = [e for e in result if e.category == "open_question"]
        assert len(questions) == 0

    def test_unresolved_questions_kept(self):
        """Questions with no corresponding answer should survive."""
        entries = [
            MemoryEntry(category="open_question", content="Should we add retry logic for batch jobs?",
                       confidence=0.6, source_turn=3),
            MemoryEntry(category="decision", content="Decided to pin the version.",
                       confidence=0.9, source_turn=1),
        ]
        result = _remove_resolved_questions(entries)
        questions = [e for e in result if e.category == "open_question"]
        assert len(questions) == 1

    def test_earlier_answer_does_not_resolve(self):
        """An answer from BEFORE the question should not resolve it."""
        entries = [
            MemoryEntry(category="decision", content="We use Redis for session storage.",
                       confidence=0.9, source_turn=0),
            MemoryEntry(category="open_question", content="Should we also use Redis for caching?",
                       confidence=0.6, source_turn=3),
        ]
        result = _remove_resolved_questions(entries)
        questions = [e for e in result if e.category == "open_question"]
        assert len(questions) == 1


# ── End-to-end quality ──


class TestCompressionQuality:
    def test_real_session_quality(self):
        """Compress our test session and verify quality improvements."""
        messages = [
            {"role": "user", "content": "resume"},
            {"role": "assistant", "content": "The project is at Phase 3.2 with 728 tests passing. "
             "Next items: 3.3 IDE plugin, 3.4 Semantic patching, 3.5 Session resume."},
            {"role": "user", "content": "Which phase should we tackle next? Check our past logs."},
            {"role": "assistant", "content": "The docs/guide-feedback.md captured gaps. Recommendation: "
             "address feedback log first, then Phase 3.3 Claude Code integration."},
            {"role": "user", "content": "Plan the improvements."},
            {"role": "assistant", "content": "Plan created. Added memory compression format spec to "
             "CONSUMER_GUIDE.md. Created .mcp.json and CLAUDE.md for Phase 3.3."},
        ]
        memory = compress_transcript(messages)

        # Context should NOT be just "Task: resume"
        assert memory.context_summary != "Task: resume"
        assert len(memory.context_summary) > 15

        # Should have decisions (from assistant messages)
        decisions = [e for e in memory.entries if e.category == "decision"]
        # User questions should NOT appear as decisions
        for d in decisions:
            assert "?" not in d.content or d.content.count("?") == 0
