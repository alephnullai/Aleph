"""Tests for build progress reporting (TTY/env/--quiet gating, stderr-only)."""

from __future__ import annotations

import io
import json

from aleph import cli
from aleph.util.progress import ProgressReporter, human_size


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def _drive(reporter: ProgressReporter) -> None:
    reporter.phase("parse/compress", total=6, unit="files")
    for i in range(1, 7):
        reporter.tick(i, 6, "files")
    reporter.phase("temporal")
    reporter.subtask("temporal: git history", 200, 1000, "commits")
    reporter.subtask("temporal: git history", 400, 1000, "commits")
    reporter.summary("build complete: 6 files, 12 symbols")


# ── Gating: TTY / ALEPH_PROGRESS / quiet ──


def test_disabled_by_default_when_not_a_tty(monkeypatch):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    buf = io.StringIO()
    _drive(ProgressReporter(stream=buf))
    assert buf.getvalue() == ""


def test_enabled_by_default_on_tty(monkeypatch):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    buf = _TTYBuffer()
    _drive(ProgressReporter(stream=buf))
    out = buf.getvalue()
    assert "parse/compress" in out
    assert "build complete" in out


def test_env_forces_on_when_piped(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    buf = io.StringIO()
    _drive(ProgressReporter(stream=buf))
    assert "parse/compress" in buf.getvalue()


def test_env_forces_off_even_on_tty(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "0")
    buf = _TTYBuffer()
    _drive(ProgressReporter(stream=buf))
    assert buf.getvalue() == ""


def test_quiet_overrides_env_and_tty(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    buf = _TTYBuffer()
    reporter = ProgressReporter(stream=buf, quiet=True)
    _drive(reporter)
    reporter.warn("should be suppressed too")
    assert buf.getvalue() == ""


def test_warning_emitted_even_when_progress_disabled(monkeypatch):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    buf = io.StringIO()  # not a TTY → progress off, warnings still on
    reporter = ProgressReporter(stream=buf)
    reporter.warn("temporal: history capped at 5000 commits")
    assert buf.getvalue() == (
        "[aleph] warning: temporal: history capped at 5000 commits\n"
    )


# ── Output shape ──


def test_non_tty_emits_at_most_one_line_per_phase(monkeypatch):
    """Piped/background builds must stay readable: banner only, no ticks."""
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf, tick_items=1, tick_secs=0.0)
    reporter.phase("parse/compress", total=500, unit="files")
    for i in range(1, 501):
        reporter.tick(i, 500, "files")
    lines = [l for l in buf.getvalue().splitlines() if "parse/compress" in l]
    assert len(lines) == 1  # the banner; every tick suppressed off-TTY


def test_tty_ticks_are_rate_limited_by_item_count(monkeypatch):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    buf = _TTYBuffer()
    reporter = ProgressReporter(stream=buf, tick_items=200, tick_secs=9999.0)
    reporter.phase("parse/compress", total=500, unit="files")
    for i in range(1, 501):
        reporter.tick(i, 500, "files")
    out = buf.getvalue()
    tick_lines = [l for l in out.splitlines() if "/500 files" in l]
    # 200, 400, and the final 500 (done == total always fires)
    assert "200/500 files" in out
    assert "400/500 files" in out
    assert "500/500 files" in out
    assert len(tick_lines) == 3


def test_tty_tick_fires_on_elapsed_time(monkeypatch):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    buf = _TTYBuffer()
    reporter = ProgressReporter(stream=buf, tick_items=10_000, tick_secs=0.0)
    reporter.phase("parse/compress", total=100, unit="files")
    reporter.tick(1, 100, "files")  # 0s elapsed >= tick_secs=0 → fires
    assert "1/100 files" in buf.getvalue()


def test_subtask_announced_once_off_tty(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf, tick_items=1, tick_secs=0.0)
    reporter.phase("parse/compress")
    for done in (200, 400, 600):
        reporter.subtask("temporal: git history", done, 1000, "commits")
    lines = [l for l in buf.getvalue().splitlines() if "git history" in l]
    assert len(lines) == 1
    assert "scanning" in lines[0]


def test_phase_banner_includes_elapsed_and_totals(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf)
    reporter.phase("parse/compress", total=42, unit="files")
    line = buf.getvalue().strip()
    assert line.startswith("[aleph] [")
    assert line.endswith("parse/compress... (42 files)")
    assert "s]" in line  # elapsed marker


# ── Non-TTY heartbeat (liveness for piped logs / CI) ──


class _FakeTime:
    """Fake clock substituting the `time` module inside aleph.util.progress."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, secs: float) -> None:
        self.now += secs


def _fake_clock(monkeypatch) -> _FakeTime:
    fake = _FakeTime()
    monkeypatch.setattr("aleph.util.progress.time", fake)
    return fake


def test_non_tty_heartbeat_emits_at_default_interval(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    monkeypatch.delenv("ALEPH_PROGRESS_HEARTBEAT", raising=False)
    fake = _fake_clock(monkeypatch)
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf)
    reporter.phase("parse/compress", total=7470, unit="files")
    reporter.tick(100, 7470, "files")    # 0s into the phase → silent
    fake.advance(59.0)
    reporter.tick(200, 7470, "files")    # 59s < 60s → still silent
    fake.advance(2.0)
    reporter.tick(3210, 7470, "files")   # 61s → heartbeat
    fake.advance(61.0)
    reporter.tick(5000, 7470, "files")   # next interval → heartbeat
    heartbeats = [l for l in buf.getvalue().splitlines() if "/7470 files (" in l]
    assert len(heartbeats) == 2
    assert "parse/compress: 3210/7470 files (61.0s)" in heartbeats[0]
    assert "parse/compress: 5000/7470 files (122.0s)" in heartbeats[1]


def test_heartbeat_interval_configurable_via_env(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    monkeypatch.setenv("ALEPH_PROGRESS_HEARTBEAT", "10")
    fake = _fake_clock(monkeypatch)
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf)
    reporter.phase("parse/compress", total=100, unit="files")
    fake.advance(10.0)
    reporter.tick(7, 100, "files")
    assert "parse/compress: 7/100 files (10.0s)" in buf.getvalue()


def test_heartbeat_env_zero_disables(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    monkeypatch.setenv("ALEPH_PROGRESS_HEARTBEAT", "0")
    fake = _fake_clock(monkeypatch)
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf)
    reporter.phase("parse/compress", total=100, unit="files")
    fake.advance(10_000.0)
    reporter.tick(50, 100, "files")
    fake.advance(10_000.0)
    reporter.tick(100, 100, "files")
    # Only the phase banner — heartbeat disabled
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    assert "parse/compress..." in lines[0]


def test_heartbeat_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    monkeypatch.setenv("ALEPH_PROGRESS_HEARTBEAT", "0")
    fake = _fake_clock(monkeypatch)
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf, heartbeat_secs=5.0)
    reporter.phase("parse/compress", total=100, unit="files")
    fake.advance(5.0)
    reporter.tick(3, 100, "files")
    assert "3/100 files" in buf.getvalue()


def test_tty_tick_behavior_unchanged_by_heartbeat(monkeypatch):
    """On a TTY the item/secs rate-limiter still governs — the heartbeat
    interval neither adds nor suppresses TTY ticks."""
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    fake = _fake_clock(monkeypatch)
    buf = _TTYBuffer()
    reporter = ProgressReporter(
        stream=buf, tick_items=200, tick_secs=9999.0, heartbeat_secs=1.0)
    reporter.phase("parse/compress", total=500, unit="files")
    fake.advance(50.0)  # many heartbeat intervals, but item threshold unmet
    reporter.tick(50, 500, "files")
    assert "50/500" not in buf.getvalue()
    reporter.tick(250, 500, "files")  # 250 - 0 >= 200 → normal TTY tick
    assert "250/500 files" in buf.getvalue()


def test_subtask_heartbeat_off_tty(monkeypatch):
    """Long non-TTY subtasks (e.g. the git history scan) heartbeat too."""
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    fake = _fake_clock(monkeypatch)
    buf = io.StringIO()
    reporter = ProgressReporter(stream=buf, heartbeat_secs=60.0)
    reporter.phase("temporal")
    reporter.subtask("temporal: git history", 200, 1000, "commits")  # announce
    reporter.subtask("temporal: git history", 400, 1000, "commits")  # <60s → silent
    fake.advance(61.0)
    reporter.subtask("temporal: git history", 800, 1000, "commits")  # heartbeat
    lines = [l for l in buf.getvalue().splitlines() if "git history" in l]
    assert len(lines) == 2
    assert "scanning" in lines[0]
    assert "800/1000 commits" in lines[1]


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(4 * 1024) == "4.0 KB"
    assert human_size(int(2.5 * 1024 * 1024)) == "2.5 MB"
    assert human_size(3 * 1024 ** 3) == "3.0 GB"


def test_build_summary_line_includes_blame_stats(tmp_path):
    from unittest.mock import MagicMock
    from aleph.pipeline import _build_summary_line

    result = MagicMock()
    result.stats.total_files = 3
    result.stats.rebuilt_files = 2
    result.stats.reused_files = 1
    result.stats.total_symbols = 9
    git = MagicMock()
    git.blame_calls = 7
    git.blame_seconds = 1.234

    line = _build_summary_line(
        result, str(tmp_path), str(tmp_path / "missing.db"), git)
    assert "temporal blame: 7 calls, 1.2s" in line
    # Without a GitHistory the summary stays unchanged
    line2 = _build_summary_line(
        result, str(tmp_path), str(tmp_path / "missing.db"))
    assert "temporal blame" not in line2


# ── CLI wiring: `aleph build` stderr gating (stdout stays clean) ──


def _run_build(monkeypatch, capsys, tmp_path, *extra):
    (tmp_path / "example.py").write_text("def demo(x):\n    return x + 1\n")
    monkeypatch.setattr(
        "sys.argv", ["aleph", "build", str(tmp_path), "--json", *extra])
    cli.main()
    return capsys.readouterr()


def test_cli_build_progress_on_with_env(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    captured = _run_build(monkeypatch, capsys, tmp_path)
    assert "[aleph]" in captured.err
    assert "parse/compress" in captured.err
    assert "temporal" in captured.err
    assert "build complete" in captured.err
    # Blame instrumentation lands in the final summary
    assert "temporal blame:" in captured.err
    # stdout stays pure JSON for --json consumers
    payload = json.loads(captured.out)
    assert payload["total_files"] == 1


def test_cli_build_progress_off_without_env_or_tty(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("ALEPH_PROGRESS", raising=False)
    captured = _run_build(monkeypatch, capsys, tmp_path)
    assert "parse/compress" not in captured.err
    json.loads(captured.out)


def test_cli_build_quiet_suppresses_progress(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("ALEPH_PROGRESS", "1")
    captured = _run_build(monkeypatch, capsys, tmp_path, "--quiet")
    assert "parse/compress" not in captured.err
    assert "build complete" not in captured.err
    json.loads(captured.out)
