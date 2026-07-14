"""Integration tests for aleph bench resume — the session resume benchmarking harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import pytest

from aleph.memory.bench import (
    run_bench_resume,
    ScoreResult,
    _build_test_transcript,
    _build_test_inferences,
    _build_test_flags,
    _build_test_patches,
    _expected_facts,
    _score_briefing,
)
from aleph.memory.compressor import compress_transcript, serialize_memory
from aleph.memory.session_memory import save_memory, _save_epistemic
from aleph.memory.briefing import generate_briefing, write_briefing, load_briefing, ResumeBriefing


class TestBenchResumeEndToEnd:
    """Full end-to-end bench resume tests."""

    def test_bench_passes_90_percent(self):
        """The benchmark must achieve >= 90% fidelity."""
        result = run_bench_resume()
        assert result.passed, f"Fidelity {result.fidelity:.1%} < 90%"
        assert result.fidelity >= 0.9

    def test_bench_has_all_categories(self):
        result = run_bench_resume()
        expected_cats = {"inference", "flag", "patch", "decision", "conclusion"}
        assert set(result.category_scores.keys()) == expected_cats

    def test_bench_summary_format(self):
        result = run_bench_resume()
        summary = result.summary()
        assert "Resume Fidelity:" in summary
        assert "PASS" in summary or "FAIL" in summary
        assert "inference:" in summary
        assert "flag:" in summary

    def test_bench_verbose(self):
        """Verbose mode should not crash."""
        result = run_bench_resume(verbose=True)
        assert isinstance(result, ScoreResult)


class TestCompressResumePipeline:
    """Test the full compress → save → briefing → score pipeline."""

    def test_compress_generates_briefing_artifact(self):
        """save_memory should also write project.aleph.resume."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "inferences": _build_test_inferences(),
                "flags": _build_test_flags(),
                "patches": _build_test_patches(),
                "memories": [],
            })
            messages = _build_test_transcript()
            memory = compress_transcript(messages)
            save_memory(tmpdir, memory, session_id="test")

            # The briefing artifact should exist
            resume_path = os.path.join(tmpdir, ".aleph", "project.aleph.resume")
            assert os.path.isfile(resume_path)

            briefing = load_briefing(tmpdir)
            assert briefing is not None
            assert len(briefing.inferences) > 0

    def test_briefing_preserves_inferences(self):
        """All test inferences should appear in the briefing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            inferences = _build_test_inferences()
            _save_epistemic(tmpdir, {"inferences": inferences})
            briefing = generate_briefing(tmpdir)
            assert len(briefing.inferences) == len(inferences)
            for inf in inferences:
                found = any(
                    b.symbol_id == inf["symbol_id"] for b in briefing.inferences
                )
                assert found, f"Missing inference for {inf['symbol_id']}"

    def test_briefing_preserves_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            flags = _build_test_flags()
            _save_epistemic(tmpdir, {"flags": flags})
            briefing = generate_briefing(tmpdir)
            assert len(briefing.flags) == len(flags)

    def test_briefing_filters_pending_patches_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            patches = _build_test_patches()
            # Add an applied patch that should be excluded
            patches.append({
                "patch_id": "patch_applied",
                "symbol_id": "f_xxx",
                "intent": "old change",
                "status": "applied",
            })
            _save_epistemic(tmpdir, {"patches": patches})
            briefing = generate_briefing(tmpdir)
            assert len(briefing.patches) == 2  # Only pending ones

    def test_briefing_serialization_roundtrip(self):
        """Write briefing to file and load it back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "inferences": _build_test_inferences()[:3],
                "flags": _build_test_flags()[:1],
                "patches": _build_test_patches()[:1],
            })
            briefing = generate_briefing(tmpdir)
            path = write_briefing(tmpdir, briefing)

            loaded = load_briefing(tmpdir)
            assert loaded is not None
            assert len(loaded.inferences) == len(briefing.inferences)
            assert len(loaded.flags) == len(briefing.flags)
            assert len(loaded.patches) == len(briefing.patches)


class TestBenchCLI:
    """Test the bench resume CLI command."""

    def test_bench_resume_cli(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "aleph.cli",
                "bench",
                "resume",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # May not have the CLI installed, so check if we can import
        if result.returncode != 0 and "No module" in result.stderr:
            pytest.skip("aleph CLI not installed")
        assert "Resume Fidelity:" in result.stdout or result.returncode == 0

    def test_bench_resume_cli_json(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "aleph.cli",
                "bench",
                "resume",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and "No module" in result.stderr:
            pytest.skip("aleph CLI not installed")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert "fidelity" in data
            assert "passed" in data


class TestMemoryResumeCLI:
    """Test aleph memory resume outputs briefing format."""

    def test_resume_outputs_briefing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".aleph"))
            _save_epistemic(tmpdir, {
                "inferences": [{"symbol_id": "f_a", "conclusion": "safe", "confidence": 0.9}],
                "flags": [{"symbol_id": "f_b", "reason": "check", "verified": False}],
            })
            # Need to also write the briefing artifact
            briefing = generate_briefing(tmpdir)
            write_briefing(tmpdir, briefing)

            from aleph.memory.session_memory import resume_session_briefing
            result = resume_session_briefing(tmpdir)
            assert result is not None
            assert len(result.inferences) == 1
            assert result.inferences[0].symbol_id == "f_a"
