"""Shared filesystem conventions for Aleph project artifacts.

Single source of truth for resolving where a project's built .aleph
artifacts live. Previously this logic was duplicated in four places
(mcp/handlers.py, query/engine.py, patch/manager.py,
memory/session_memory.py) which risked split-brain reads/writes when
the copies drifted.
"""

from __future__ import annotations

import os

# The artifacts that mark a directory as a built Aleph artifact root.
DICT_FILENAME = "project.aleph.dict"
DB_FILENAME = "aleph.db"


def resolve_artifact_dir(project_dir: str) -> str:
    """Find the directory containing built .aleph artifacts for a project.

    Uses the ``.aleph/`` subdirectory only when it contains
    ``project.aleph.dict`` or the SQLite store ``aleph.db`` (i.e. a
    build has been run); otherwise falls back to ``project_dir`` itself
    (backward compat / explicit artifact path). All readers AND writers
    must share this rule to prevent split-brain writes to different
    epistemic files.
    """
    aleph_subdir = os.path.join(project_dir, ".aleph")
    if os.path.isdir(aleph_subdir) and (
        os.path.isfile(os.path.join(aleph_subdir, DICT_FILENAME))
        or os.path.isfile(os.path.join(aleph_subdir, DB_FILENAME))
    ):
        return aleph_subdir
    return project_dir


def rel_posix(path: str, start: str) -> str:
    """Project-relative path in POSIX form (forward slashes).

    All artifact layers (text + SQLite) store relative paths in this one
    convention so they compare equal across platforms — native-separator
    rel paths on Windows broke text-vs-db parity.
    """
    return os.path.relpath(path, start).replace("\\", "/")
