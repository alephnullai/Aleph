"""Tests for aleph.memory.session_memory — epistemic integration."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from aleph.memory.compressor import CompressedMemory, MemoryEntry, compress_transcript
from aleph.memory.session_memory import (
    save_memory,
    load_latest_memory,
    load_all_memories,
    resume_session,
    _memory_to_dict,
    _dict_to_memory,
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


def _make_test_memory() -> CompressedMemory:
    return CompressedMemory(
        entries=[
            MemoryEntry(category="decision", content="Use Redis", confidence=0.9),
            MemoryEntry(category="error", content="Pipeline broke", confidence=0.85),
        ],
        symbol_dict={"s_abc123": "validate_schema"},
        context_summary="Debugging session",
        original_token_estimate=500,
        compressed_token_estimate=150,
        message_count=4,
    )


# ── Dict serialization ──


class TestDictConversion:
    def test_roundtrip(self):
        memory = _make_test_memory()
        d = _memory_to_dict(memory, "test-session")
        restored = _dict_to_memory(d)

        assert restored.message_count == memory.message_count
        assert restored.context_summary == memory.context_summary
        assert len(restored.entries) == len(memory.entries)
        assert restored.symbol_dict == memory.symbol_dict

    def test_session_id_preserved(self):
        memory = _make_test_memory()
        d = _memory_to_dict(memory, "session-42")
        assert d["session_id"] == "session-42"

    def test_entries_preserved(self):
        memory = _make_test_memory()
        d = _memory_to_dict(memory, "test")
        assert len(d["entries"]) == 2
        assert d["entries"][0]["category"] == "decision"


# ── File I/O ──


class TestSaveLoadMemory:
    def test_save_and_load(self, tmp_path):
        memory = _make_test_memory()
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        save_memory(str(tmp_path), memory, session_id="test-1")
        loaded = load_latest_memory(str(tmp_path))

        assert loaded is not None
        assert loaded.message_count == memory.message_count
        assert loaded.context_summary == memory.context_summary
        assert len(loaded.entries) == len(memory.entries)

    def test_multiple_saves(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        m1 = CompressedMemory(
            entries=[MemoryEntry(category="decision", content="First")],
            message_count=1,
        )
        m2 = CompressedMemory(
            entries=[MemoryEntry(category="decision", content="Second")],
            message_count=2,
        )

        save_memory(str(tmp_path), m1, session_id="s1")
        save_memory(str(tmp_path), m2, session_id="s2")

        latest = load_latest_memory(str(tmp_path))
        assert latest is not None
        assert latest.message_count == 2

        all_mems = load_all_memories(str(tmp_path))
        assert len(all_mems) == 2

    def test_no_memories_returns_none(self, tmp_path):
        assert load_latest_memory(str(tmp_path)) is None

    def test_preserves_existing_epistemic(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text("")  # Required for path resolution
        epistemic_path = aleph_dir / "project.aleph.epistemic"

        # Pre-populate with existing epistemic data
        existing = {
            "inferences": [{"symbol_id": "f_abc", "conclusion": "test", "confidence": 0.9}],
            "flags": [],
        }
        with open(epistemic_path, "w") as f:
            json.dump(existing, f)

        memory = _make_test_memory()
        save_memory(str(tmp_path), memory, session_id="test")

        # Verify existing data preserved
        with open(epistemic_path) as f:
            data = json.load(f)
        assert "inferences" in data
        assert len(data["inferences"]) == 1
        assert "memories" in data
        assert len(data["memories"]) == 1

    def test_creates_aleph_dir_if_missing(self, tmp_path):
        # No .aleph directory exists
        memory = _make_test_memory()
        path = save_memory(str(tmp_path), memory, session_id="test")
        assert os.path.isfile(path)

    def test_handles_corrupt_epistemic(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        epistemic_path = aleph_dir / "project.aleph.epistemic"
        with open(epistemic_path, "w") as f:
            f.write("not json at all {{{")

        memory = _make_test_memory()
        save_memory(str(tmp_path), memory, session_id="test")

        loaded = load_latest_memory(str(tmp_path))
        assert loaded is not None


# ── Session resume ──


class TestResumeSession:
    def test_resume_produces_context(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        memory = _make_test_memory()
        save_memory(str(tmp_path), memory, session_id="test")

        ctx = resume_session(str(tmp_path))
        assert ctx is not None
        assert len(ctx.decisions) > 0

    def test_resume_no_memory(self, tmp_path):
        ctx = resume_session(str(tmp_path))
        assert ctx is None

    def test_resume_with_real_fixture(self, tmp_path):
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        messages = _load_fixture("debug_session.json")
        memory = compress_transcript(messages)
        save_memory(str(tmp_path), memory, session_id="debug")

        ctx = resume_session(str(tmp_path))
        assert ctx is not None
        prompt = ctx.to_prompt()
        assert "## Prior Session State" in prompt

        # The prompt should contain useful information
        total_items = (
            len(ctx.decisions)
            + len(ctx.conclusions)
            + len(ctx.open_questions)
            + len(ctx.code_changes)
            + len(ctx.errors)
        )
        assert total_items > 0
