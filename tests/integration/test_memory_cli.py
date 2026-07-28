"""Integration tests for aleph memory CLI commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "conversations"
)


def _run_aleph(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "aleph.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestMemoryCompress:
    def test_compress_debug_session(self):
        fixture = os.path.join(FIXTURES_DIR, "debug_session.json")
        result = _run_aleph("memory", "compress", fixture)
        assert result.returncode == 0
        assert "[ALEPH:MEMORY:1.0]" in result.stdout

    def test_compress_json_output(self):
        fixture = os.path.join(FIXTURES_DIR, "debug_session.json")
        result = _run_aleph("memory", "compress", fixture, "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "message_count" in data
        assert "reduction_percent" in data
        assert data["message_count"] == 8

    def test_compress_feature_session(self):
        fixture = os.path.join(FIXTURES_DIR, "feature_session.json")
        result = _run_aleph("memory", "compress", fixture)
        assert result.returncode == 0
        assert "[ALEPH:MEMORY:1.0]" in result.stdout

    def test_compress_with_save(self, tmp_path):
        fixture = os.path.join(FIXTURES_DIR, "debug_session.json")
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        (aleph_dir / "project.aleph.dict").write_text("")  # Required for path resolution

        result = _run_aleph(
            "memory", "compress", fixture,
            "-d", str(tmp_path),
            "--session-id", "test-cli",
        )
        assert result.returncode == 0

        epistemic_path = aleph_dir / "project.aleph.epistemic"
        assert epistemic_path.exists()
        with open(epistemic_path) as f:
            data = json.load(f)
        assert "memories" in data
        assert len(data["memories"]) == 1

    def test_compress_missing_file(self):
        result = _run_aleph("memory", "compress", "/nonexistent/file.json")
        assert result.returncode != 0

    def test_compress_reduction_target(self):
        """CLI compress of real conversation should hit 60% reduction."""
        fixture = os.path.join(FIXTURES_DIR, "debug_session.json")
        result = _run_aleph("memory", "compress", fixture, "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["reduction_percent"] >= 60.0


class TestMemoryResume:
    def test_resume_after_compress(self, tmp_path):
        fixture = os.path.join(FIXTURES_DIR, "debug_session.json")
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        # Compress and save
        _run_aleph(
            "memory", "compress", fixture,
            "-d", str(tmp_path),
            "--session-id", "test",
        )

        # Resume
        result = _run_aleph("memory", "resume", "-d", str(tmp_path))
        assert result.returncode == 0
        assert "Session Briefing" in result.stdout

    def test_resume_json(self, tmp_path):
        fixture = os.path.join(FIXTURES_DIR, "feature_session.json")
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()

        _run_aleph(
            "memory", "compress", fixture,
            "-d", str(tmp_path),
        )

        result = _run_aleph("memory", "resume", "-d", str(tmp_path), "--json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "decisions" in data
        assert "learned" in data

    def test_resume_no_memory(self, tmp_path):
        result = _run_aleph("memory", "resume", "-d", str(tmp_path))
        assert result.returncode != 0
        assert "No prior session memory" in result.stderr
