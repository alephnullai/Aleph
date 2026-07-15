"""Property tests for session resume fidelity.

Key property: compress → resume → score >= 0.9 for any well-formed epistemic state.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

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
from aleph.memory.session_memory import _save_epistemic
from aleph.memory.bench import run_bench_resume


# ── Strategies ──

symbol_id_st = st.from_regex(r"[ftv]_[a-f0-9]{6}", fullmatch=True)
# Use only alphanumeric + basic punctuation, no trailing/leading whitespace
_text_alphabet = st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_.")
conclusion_st = st.text(alphabet=_text_alphabet, min_size=3, max_size=40).map(str.strip).filter(lambda x: len(x) >= 3)
confidence_st = st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False)
reason_st = st.text(alphabet=_text_alphabet, min_size=3, max_size=40).map(str.strip).filter(lambda x: len(x) >= 3)

inference_st = st.builds(InferenceBrief, symbol_id_st, conclusion_st, confidence_st)
flag_st = st.builds(FlagBrief, symbol_id_st, reason_st, st.booleans())
patch_st = st.builds(
    PatchBrief,
    st.from_regex(r"patch_[0-9]{1,3}", fullmatch=True),
    symbol_id_st,
    reason_st,
)


# ── Briefing serialization roundtrip ──


class TestBriefingRoundtrip:
    @given(
        inferences=st.lists(inference_st, max_size=10),
        flags=st.lists(flag_st, max_size=5),
        patches=st.lists(patch_st, max_size=5),
        decisions=st.lists(conclusion_st, max_size=5),
        learned=st.lists(conclusion_st, max_size=5),
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_serialize_parse_roundtrip(self, inferences, flags, patches, decisions, learned):
        """Serialize → parse preserves all data."""
        original = ResumeBriefing(
            context_summary="test context",
            inferences=inferences,
            flags=flags,
            patches=patches,
            decisions=decisions,
            learned=learned,
        )
        text = original.serialize()
        parsed = parse_briefing(text)

        assert len(parsed.inferences) == len(original.inferences)
        assert len(parsed.flags) == len(original.flags)
        assert len(parsed.patches) == len(original.patches)
        assert len(parsed.decisions) == len(original.decisions)
        assert len(parsed.learned) == len(original.learned)

        for orig, pars in zip(original.inferences, parsed.inferences):
            assert orig.symbol_id == pars.symbol_id
            assert orig.conclusion == pars.conclusion
            assert abs(orig.confidence - pars.confidence) < 0.01

        for orig, pars in zip(original.flags, parsed.flags):
            assert orig.symbol_id == pars.symbol_id
            assert orig.reason == pars.reason
            assert orig.verified == pars.verified

        for orig, pars in zip(original.patches, parsed.patches):
            assert orig.patch_id == pars.patch_id
            assert orig.symbol_id == pars.symbol_id
            assert orig.intent == pars.intent

    @given(
        inferences=st.lists(inference_st, min_size=1, max_size=5),
    )
    @settings(max_examples=15, suppress_health_check=[HealthCheck.too_slow])
    def test_write_load_roundtrip(self, inferences):
        """Write to file → load from file preserves all data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            original = ResumeBriefing(
                context_summary="test",
                inferences=inferences,
            )
            write_briefing(tmpdir, original)
            loaded = load_briefing(tmpdir)

            assert loaded is not None
            assert len(loaded.inferences) == len(original.inferences)
            for orig, load in zip(original.inferences, loaded.inferences):
                assert orig.symbol_id == load.symbol_id


# ── Generate briefing fidelity ──


class TestGenerateBriefingFidelity:
    @given(
        n_inferences=st.integers(min_value=1, max_value=15),
    )
    @settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
    def test_inferences_survive_generate(self, n_inferences):
        """All inferences (up to 10) should survive generate_briefing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            inferences = [
                {
                    "symbol_id": f"f_{i:06x}",
                    "conclusion": f"conclusion {i}",
                    "confidence": (i + 1) / (n_inferences + 1),
                }
                for i in range(n_inferences)
            ]
            _save_epistemic(tmpdir, {"inferences": inferences})
            briefing = generate_briefing(tmpdir)
            expected = min(10, n_inferences)
            assert len(briefing.inferences) == expected

    @given(
        n_flags=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=10, suppress_health_check=[HealthCheck.too_slow])
    def test_flags_survive_generate(self, n_flags):
        """All flags should survive generate_briefing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            flags = [
                {"symbol_id": f"f_{i:06x}", "reason": f"reason {i}", "verified": i % 2 == 0}
                for i in range(n_flags)
            ]
            _save_epistemic(tmpdir, {"flags": flags})
            briefing = generate_briefing(tmpdir)
            assert len(briefing.flags) == n_flags


# ── Full bench property ──


class TestBenchProperty:
    def test_bench_fidelity_at_least_90(self):
        """The bench must achieve >= 90% fidelity every time."""
        result = run_bench_resume()
        assert result.fidelity >= 0.9, (
            f"Resume fidelity {result.fidelity:.1%} is below 90% target.\n"
            f"{result.summary()}"
        )

    def test_bench_deterministic(self):
        """Running bench twice should give the same result."""
        r1 = run_bench_resume()
        r2 = run_bench_resume()
        assert r1.fidelity == r2.fidelity
        assert r1.found == r2.found
        assert r1.total == r2.total
