"""Tests for aleph.memory.briefing — resume briefing artifact."""

from __future__ import annotations

import json
import os
import tempfile
import pytest

from aleph.memory.briefing import (
    InferenceBrief,
    FlagBrief,
    PatchBrief,
    ResumeBriefing,
    parse_briefing,
    generate_briefing,
    write_briefing,
    load_briefing,
)
from aleph.memory.formats import RESUME_HEADER
from aleph.memory.session_memory import _save_epistemic


# ── Data model tests ──


class TestInferenceBrief:
    def test_to_line(self):
        inf = InferenceBrief("f_abc123", "thread-safe under lock", 0.85)
        assert inf.to_line() == "f_abc123 [0.8500] thread-safe under lock"

    def test_to_line_integer_confidence(self):
        inf = InferenceBrief("f_abc123", "conclusion", 1.0)
        line = inf.to_line()
        assert "f_abc123" in line
        assert "1.0" in line


class TestFlagBrief:
    def test_to_line_unverified(self):
        fl = FlagBrief("f_abc123", "error boundary unclear")
        assert fl.to_line() == "f_abc123 error boundary unclear"

    def test_to_line_verified(self):
        fl = FlagBrief("f_abc123", "error boundary unclear", verified=True)
        assert "[VERIFIED]" in fl.to_line()


class TestPatchBrief:
    def test_to_line(self):
        p = PatchBrief("patch_1", "f_abc123", "add null check")
        assert p.to_line() == "patch_1 f_abc123 add null check"


# ── Serialization roundtrip ──


class TestBriefingSerialization:
    def test_empty_briefing(self):
        b = ResumeBriefing()
        text = b.serialize()
        assert text.startswith(RESUME_HEADER)
        parsed = parse_briefing(text)
        assert parsed.inferences == []
        assert parsed.flags == []
        assert parsed.patches == []
        assert parsed.decisions == []
        assert parsed.learned == []

    def test_full_roundtrip(self):
        b = ResumeBriefing(
            context_summary="Refactoring config parser",
            inferences=[
                InferenceBrief("f_abc123", "thread-safe", 0.95),
                InferenceBrief("f_def456", "has race condition", 0.6),
            ],
            flags=[
                FlagBrief("f_abc123", "needs lock audit", verified=False),
                FlagBrief("t_jkl012", "platform-specific", verified=True),
            ],
            patches=[
                PatchBrief("patch_1", "f_abc123", "add mutex"),
                PatchBrief("patch_2", "f_def456", "fix race"),
            ],
            decisions=["Use pydantic for validation", "Keep backward compat"],
            learned=["Deep merge needed for nested dicts", "Watchdog fires twice"],
        )
        text = b.serialize()
        parsed = parse_briefing(text)

        assert parsed.context_summary == "Refactoring config parser"
        assert len(parsed.inferences) == 2
        assert parsed.inferences[0].symbol_id == "f_abc123"
        assert parsed.inferences[0].confidence == 0.95
        assert parsed.inferences[0].conclusion == "thread-safe"
        assert parsed.inferences[1].confidence == 0.6

        assert len(parsed.flags) == 2
        assert parsed.flags[0].verified is False
        assert parsed.flags[1].verified is True

        assert len(parsed.patches) == 2
        assert parsed.patches[0].patch_id == "patch_1"
        assert parsed.patches[0].intent == "add mutex"

        assert len(parsed.decisions) == 2
        assert "pydantic" in parsed.decisions[0]

        assert len(parsed.learned) == 2
        assert "nested dicts" in parsed.learned[0]

    def test_context_only(self):
        b = ResumeBriefing(context_summary="Working on auth")
        text = b.serialize()
        parsed = parse_briefing(text)
        assert parsed.context_summary == "Working on auth"
        assert parsed.inferences == []

    def test_inferences_sorted_by_confidence(self):
        b = ResumeBriefing(inferences=[
            InferenceBrief("f_low", "low conf", 0.3),
            InferenceBrief("f_high", "high conf", 0.95),
        ])
        text = b.serialize()
        parsed = parse_briefing(text)
        assert parsed.inferences[0].confidence == 0.3
        assert parsed.inferences[1].confidence == 0.95


# ── to_prompt ──


class TestBriefingPrompt:
    def test_prompt_format(self):
        b = ResumeBriefing(
            context_summary="Task context",
            inferences=[InferenceBrief("f_abc", "safe", 0.9)],
            flags=[FlagBrief("f_def", "unclear")],
            patches=[PatchBrief("p_1", "f_abc", "fix it")],
            decisions=["Use redis"],
            learned=["TTL matters"],
        )
        prompt = b.to_prompt()
        assert "## Session Briefing" in prompt
        assert "**Context:**" in prompt
        assert "Prior Inferences" in prompt
        assert "f_abc" in prompt
        assert "Flags" in prompt
        assert "Pending Patches" in prompt
        assert "Key Decisions" in prompt
        assert "Key Learnings" in prompt

    def test_empty_prompt(self):
        b = ResumeBriefing()
        prompt = b.to_prompt()
        assert "Session Briefing" in prompt
        assert "Inferences" not in prompt


# ── generate_briefing ──


class TestGenerateBriefing:
    def test_from_empty_epistemic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {})
            briefing = generate_briefing(tmpdir)
            assert briefing.inferences == []
            assert briefing.flags == []
            assert briefing.patches == []

    def test_with_inferences(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "inferences": [
                    {"symbol_id": "f_a", "conclusion": "safe", "confidence": 0.9},
                    {"symbol_id": "f_b", "conclusion": "unsafe", "confidence": 0.4},
                ],
            })
            briefing = generate_briefing(tmpdir)
            assert len(briefing.inferences) == 2
            # Should be sorted by confidence (highest first)
            assert briefing.inferences[0].confidence == 0.9
            assert briefing.inferences[1].confidence == 0.4

    def test_top_10_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            inferences = [
                {"symbol_id": f"f_{i}", "conclusion": f"inf {i}", "confidence": i / 20.0}
                for i in range(15)
            ]
            _save_epistemic(tmpdir, {"inferences": inferences})
            briefing = generate_briefing(tmpdir)
            assert len(briefing.inferences) == 10
            # Should be top 10 by confidence
            assert briefing.inferences[0].confidence == 14 / 20.0

    def test_with_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "flags": [
                    {"symbol_id": "f_a", "reason": "needs check", "verified": False},
                    {"symbol_id": "f_b", "reason": "done", "verified": True},
                ],
            })
            briefing = generate_briefing(tmpdir)
            assert len(briefing.flags) == 2
            assert not briefing.flags[0].verified
            assert briefing.flags[1].verified

    def test_with_pending_patches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "patches": [
                    {"patch_id": "p1", "symbol_id": "f_a", "intent": "fix", "status": "pending"},
                    {"patch_id": "p2", "symbol_id": "f_b", "intent": "remove", "status": "applied"},
                ],
            })
            briefing = generate_briefing(tmpdir)
            assert len(briefing.patches) == 1
            assert briefing.patches[0].patch_id == "p1"

    def test_with_memory_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "memories": [{
                    "context_summary": "Working on auth",
                    "symbol_dict": {"s_abc": "AuthService"},
                    "entries": [
                        {"category": "decision", "content": "Use s_abc for login"},
                        {"category": "conclusion", "content": "s_abc is thread-safe"},
                        {"category": "code_change", "content": "Modified handler"},
                    ],
                }],
            })
            briefing = generate_briefing(tmpdir)
            assert briefing.context_summary == "Working on auth"
            assert len(briefing.decisions) == 1
            assert "AuthService" in briefing.decisions[0]
            assert len(briefing.learned) == 1
            assert "AuthService" in briefing.learned[0]


# ── write/load briefing ──


class TestWriteLoadBriefing:
    def test_write_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            b = ResumeBriefing(
                context_summary="Test",
                inferences=[InferenceBrief("f_a", "safe", 0.9)],
                flags=[FlagBrief("f_b", "check", False)],
            )
            path = write_briefing(tmpdir, b)
            assert os.path.isfile(path)
            assert "project.aleph.resume" in path

            loaded = load_briefing(tmpdir)
            assert loaded is not None
            assert loaded.context_summary == "Test"
            assert len(loaded.inferences) == 1
            assert loaded.inferences[0].symbol_id == "f_a"
            assert len(loaded.flags) == 1

    def test_load_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            assert load_briefing(tmpdir) is None

    def test_write_creates_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            b = ResumeBriefing(context_summary="Test")
            path = write_briefing(tmpdir, b)
            assert os.path.isfile(path)


# ── parse edge cases ──


class TestParseBriefingEdgeCases:
    def test_empty_text(self):
        b = parse_briefing("")
        assert b.inferences == []

    def test_header_only(self):
        b = parse_briefing("[ALEPH:RESUME:1.0]\n")
        assert b.inferences == []

    def test_malformed_inference_line(self):
        text = "[ALEPH:RESUME:1.0]\n[TOP_INFERENCES]\ngarbage line\n[/TOP_INFERENCES]\n"
        b = parse_briefing(text)
        assert len(b.inferences) == 0

    def test_flag_with_no_reason(self):
        text = "[ALEPH:RESUME:1.0]\n[FLAGS]\nf_abc\n[/FLAGS]\n"
        b = parse_briefing(text)
        # Single-word line doesn't split into 2 parts
        assert len(b.flags) == 0

    def test_patch_with_short_line(self):
        text = "[ALEPH:RESUME:1.0]\n[PENDING_PATCHES]\npatch_1 f_abc\n[/PENDING_PATCHES]\n"
        b = parse_briefing(text)
        # Needs 3 parts minimum
        assert len(b.patches) == 0
