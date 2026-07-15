"""Atomic, locked store for the project.aleph.epistemic JSON file.

Single shared implementation for every reader/writer of the epistemic
layer (MCP handlers, patch manager, session memory). Guarantees:

- Atomic writes: data is written to a temp file in the same directory
  and moved into place with os.replace (never a partial file).
- Backup: the previous good file is copied to <path>.bak before each save.
- Corruption recovery: a file that fails to parse is renamed to
  <path>.corrupt.<timestamp> and the .bak is loaded instead — agent
  state is never silently reset to {}.
- Concurrency: a lock file serializes read-modify-write cycles across
  threads and processes. Use::

      with store.transaction() as data:
          data.setdefault("inferences", []).append(entry)

No new dependencies — fcntl (darwin/linux) or msvcrt (Windows) provides
the cross-process lock; a per-path threading.Lock serializes threads in
this process (required on Windows, where msvcrt.locking does not block a
second thread the way flock on a fresh fd does).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover — non-POSIX platforms
    fcntl = None

try:
    import msvcrt
except ImportError:  # not Windows
    msvcrt = None


# Per-lock-file-path threading locks: intra-process serialization that does
# not depend on OS file-lock semantics (fcntl.flock serializes threads
# holding separate fds; msvcrt.locking raises instead of blocking).
_PROC_LOCKS: dict[str, threading.Lock] = {}
_PROC_LOCKS_GUARD = threading.Lock()


def _proc_lock_for(path: str) -> threading.Lock:
    with _PROC_LOCKS_GUARD:
        lock = _PROC_LOCKS.get(path)
        if lock is None:
            lock = _PROC_LOCKS.setdefault(path, threading.Lock())
        return lock


def _os_lock(fd) -> None:
    """Acquire an exclusive cross-process lock on *fd* (best available)."""
    if fcntl is not None:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
    elif msvcrt is not None:  # pragma: no cover — Windows only
        fd.seek(0)
        while True:
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)


def _os_unlock(fd) -> None:
    if fcntl is not None:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    elif msvcrt is not None:  # pragma: no cover — Windows only
        fd.seek(0)
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


class EpistemicStore:
    """Atomic, locked accessor for an epistemic JSON file."""

    def __init__(self, path: str) -> None:
        self.path = path

    @property
    def bak_path(self) -> str:
        return self.path + ".bak"

    @property
    def lock_path(self) -> str:
        return self.path + ".lock"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Hold an exclusive lock on the sidecar lock file.

        Two layers: a per-path threading.Lock serializes threads in this
        process (the only protection on platforms without fcntl), and an
        OS file lock (flock / msvcrt.locking) serializes processes.
        """
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with _proc_lock_for(os.path.abspath(self.lock_path)):
            fd = open(self.lock_path, "a+")
            try:
                _os_lock(fd)
                yield
            finally:
                _os_unlock(fd)
                fd.close()

    def load(self) -> dict[str, Any]:
        """Load the epistemic data, recovering from corruption if needed."""
        with self._lock():
            return self._load_unlocked()

    def save(self, data: dict[str, Any]) -> None:
        """Atomically save the epistemic data (previous good file -> .bak)."""
        with self._lock():
            self._save_unlocked(data)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        """Read-modify-write under one exclusive lock — no lost updates.

        Yields the loaded data dict; mutations are saved atomically on
        normal exit. If the body raises, nothing is written.
        """
        with self._lock():
            data = self._load_unlocked()
            yield data
            self._save_unlocked(data)

    # ── Internals (must be called with the lock held) ──

    def _load_unlocked(self) -> dict[str, Any]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self._recover_corrupt()

    def _recover_corrupt(self) -> dict[str, Any]:
        """Quarantine a corrupt file and fall back to the .bak copy."""
        corrupt_path = f"{self.path}.corrupt.{int(time.time())}"
        try:
            os.replace(self.path, corrupt_path)
        except OSError:
            corrupt_path = self.path  # rename failed — leave in place
        print(
            f"[aleph] WARNING: {self.path} failed to parse — "
            f"preserved as {corrupt_path}",
            file=sys.stderr,
        )
        if os.path.isfile(self.bak_path):
            try:
                with open(self.bak_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(
                    f"[aleph] Recovered epistemic state from {self.bak_path}",
                    file=sys.stderr,
                )
                return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                print(
                    f"[aleph] WARNING: backup {self.bak_path} is also unreadable",
                    file=sys.stderr,
                )
        print(
            "[aleph] WARNING: no usable backup — starting from empty epistemic state",
            file=sys.stderr,
        )
        return {}

    def _save_unlocked(self, data: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        # Keep a copy of the previous good file before replacing it.
        if self._current_file_is_valid():
            try:
                shutil.copy2(self.path, self.bak_path)
            except OSError:
                pass  # Backup is best-effort; the atomic write still holds
        tmp_path = f"{self.path}.tmp.{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        finally:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _current_file_is_valid(self) -> bool:
        """True if the on-disk file exists and parses — only then back it up."""
        if not os.path.isfile(self.path):
            return False
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                json.load(f)
            return True
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return False
