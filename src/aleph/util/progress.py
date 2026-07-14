"""Build progress reporting for long-running operations.

All output goes to stderr so stdout stays clean for ``--json`` consumers.
Dependency-free by design (no tqdm).

Gating rules:
  * ``quiet=True`` suppresses everything (CLI ``--quiet``), including
    warnings.
  * ``ALEPH_PROGRESS=1`` forces progress ON (even when stderr is piped).
  * ``ALEPH_PROGRESS=0`` forces progress OFF.
  * Otherwise progress is ON exactly when the stream is a TTY.

Output shape:
  * One phase banner line per phase (every enabled mode) — so a piped /
    backgrounded build emits at most one line per phase but liveness is
    still provable.
  * Periodic in-phase tick lines (TTY only): every ``tick_items``
    processed or every ``tick_secs`` seconds, whichever comes first.
  * Non-TTY heartbeat (when progress is enabled): one tick line every
    ``ALEPH_PROGRESS_HEARTBEAT`` seconds (default 60; 0 disables), so a
    multi-hour phase in piped logs / CI is distinguishable from a hang.
  * Warnings are emitted unless ``quiet`` (even when progress itself is
    disabled) — degraded-build conditions must be visible in logs.
  * A final summary line via :meth:`summary`.
"""

from __future__ import annotations

import os
import sys
import time

# Non-TTY liveness heartbeat interval (seconds); ALEPH_PROGRESS_HEARTBEAT
# overrides, 0 disables.
_DEFAULT_HEARTBEAT_SECS = 60.0


def human_size(num_bytes: int) -> str:
    """Human-readable byte size (e.g. '4.2 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


class ProgressReporter:
    """Phase/tick/summary progress lines for ``aleph build``.

    Safe to call unconditionally from build code: when disabled every
    method is a cheap no-op (warnings excepted, see module docstring).
    """

    def __init__(
        self,
        stream=None,
        quiet: bool = False,
        tick_items: int = 200,
        tick_secs: float = 5.0,
        heartbeat_secs: float | None = None,
    ) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.quiet = quiet
        self.tick_items = tick_items
        self.tick_secs = tick_secs
        if heartbeat_secs is None:
            try:
                heartbeat_secs = float(
                    os.environ.get("ALEPH_PROGRESS_HEARTBEAT", ""))
            except ValueError:
                heartbeat_secs = _DEFAULT_HEARTBEAT_SECS
        self.heartbeat_secs = heartbeat_secs
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())

        env = os.environ.get("ALEPH_PROGRESS", "").strip().lower()
        if quiet:
            self.enabled = False
        elif env in ("1", "true", "yes", "on"):
            self.enabled = True
        elif env in ("0", "false", "no", "off"):
            self.enabled = False
        else:
            self.enabled = self.tty

        self._start = time.monotonic()
        self._phase_name: str | None = None
        self._phase_start = self._start
        # Per-key (phase/subtask name) rate-limiter state: (last_count, last_time)
        self._last: dict[str, tuple[int, float]] = {}

    # ── internals ──

    def _elapsed(self) -> float:
        return time.monotonic() - self._start

    def _write(self, line: str) -> None:
        try:
            self.stream.write(line + "\n")
            self.stream.flush()
        except (OSError, ValueError):
            pass  # progress must never break a build

    def _should_tick(self, key: str, done: int, total: int | None) -> bool:
        now = time.monotonic()
        last_done, last_time = self._last.get(key, (0, self._phase_start))
        fire = (
            done - last_done >= self.tick_items
            or now - last_time >= self.tick_secs
            or (total is not None and done >= total)
        )
        if fire:
            self._last[key] = (done, now)
        return fire

    def _heartbeat_due(self, key: str, now: float) -> bool:
        """Non-TTY liveness: has a heartbeat interval elapsed for *key*?"""
        if self.heartbeat_secs <= 0:
            return False
        _, last_time = self._last.get(key, (0, self._phase_start))
        return now - last_time >= self.heartbeat_secs

    # ── public API ──

    def phase(self, name: str, total: int | None = None, unit: str = "") -> None:
        """Start a new phase: one banner line with total elapsed time."""
        self._phase_name = name
        self._phase_start = time.monotonic()
        self._last.pop(name, None)
        if not self.enabled:
            return
        detail = f" ({total} {unit})" if total is not None and unit else ""
        self._write(f"[aleph] [{self._elapsed():6.1f}s] {name}...{detail}")

    def tick(self, done: int, total: int | None = None, unit: str = "items") -> None:
        """Periodic within-phase progress (rate-limited).

        TTY: every ``tick_items`` processed or ``tick_secs`` seconds.
        Non-TTY (when enabled): a heartbeat line every
        ``heartbeat_secs`` seconds, so a long phase stays provably
        alive in piped logs without flooding them.
        """
        if not self.enabled:
            return
        key = self._phase_name or "?"
        if not self.tty:
            now = time.monotonic()
            if not self._heartbeat_due(key, now):
                return
            self._last[key] = (done, now)
            total_part = f"/{total}" if total is not None else ""
            self._write(
                f"[aleph]   {key}: {done}{total_part} {unit} "
                f"({now - self._phase_start:.1f}s)")
            return
        if not self._should_tick(key, done, total):
            return
        phase_dur = time.monotonic() - self._phase_start
        total_part = f"/{total}" if total is not None else ""
        self._write(f"[aleph]   {key}: {done}{total_part} {unit} ({phase_dur:.1f}s)")

    def subtask(self, name: str, done: int, total: int | None = None,
                unit: str = "items") -> None:
        """Progress for work nested inside a phase (e.g. the git history scan).

        TTY: rate-limited tick lines. Non-TTY (when enabled): a single
        announcement line the first time the subtask reports, then a
        heartbeat line every ``heartbeat_secs`` seconds — piped logs
        stay bounded while long subtasks stay provably alive.
        """
        if not self.enabled:
            return
        if not self.tty:
            now = time.monotonic()
            if name not in self._last:
                self._last[name] = (done, now)
                total_part = f" ~{total}" if total else ""
                self._write(f"[aleph]   {name}: scanning{total_part} {unit}...")
                return
            if self._heartbeat_due(name, now):
                self._last[name] = (done, now)
                total_part = f"/{total}" if total else ""
                self._write(f"[aleph]   {name}: {done}{total_part} {unit}")
            return
        if not self._should_tick(name, done, total):
            return
        total_part = f"/{total}" if total else ""
        self._write(f"[aleph]   {name}: {done}{total_part} {unit}")

    def warn(self, message: str) -> None:
        """Warning line — emitted unless --quiet, even if progress is off."""
        if self.quiet:
            return
        self._write(f"[aleph] warning: {message}")

    def summary(self, message: str) -> None:
        """Final one-line summary with total elapsed time."""
        if not self.enabled:
            return
        self._write(f"[aleph] [{self._elapsed():6.1f}s] {message}")
