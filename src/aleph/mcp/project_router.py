"""Per-tool-call project resolution for the Aleph MCP server.

THE PROBLEM (P0 adoption fix #1)
--------------------------------
The Aleph MCP server is configured once, globally, as ``aleph serve .`` in
``~/.claude.json`` — launched from a single fixed cwd. The server's
``_handlers`` are therefore pinned to ONE directory's index. When the agent
works in a DIFFERENT indexed repo, every tool errors "no .aleph artifacts"
even though that repo *has* an index — the tool simply can't reach it.
grep always reaches the current files; Aleph didn't. If the tool can't see
the repo you're in, no instruction can fix adoption.

THE FIX (Option A — per-tool-call resolution via MCP roots)
-----------------------------------------------------------
The MCP protocol lets a server ask its client for the client's workspace
roots (``roots/list``). Claude Code advertises the ``roots`` capability and
answers with the directories the user is actually working in. So on each
tool call we:

  1. ask the client for its roots (capability-guarded, timeout-bounded,
     fail-soft — falls back to the served root if roots are unavailable);
  2. resolve the active project: the indexed repo (a dir with a built
     ``.aleph`` index) containing a client root, or the served root;
  3. hand the call a cached :class:`AlephHandlers` for that project,
     lazily constructed and kept in a small bounded LRU so switching back
     and forth never rebuilds.

WHY THIS IS SAFE FOR THE RESPONSIVENESS CONTRACT
------------------------------------------------
All of this runs *inside the tool handler* — i.e. per tool call, AFTER the
handshake. The pre-handshake purity guard (tests/unit/test_handshake_purity)
inspects only the direct bodies of ``serve``/``create_server``/
``_handle_serve`` and explicitly skips nested functions (tool handlers).
Nothing here runs before ``mcp_server.run()``. Roots resolution is a single
small client round-trip with a hard timeout; index loads are lazy and
cached. ``initialize`` is never blocked.

If the SDK/client genuinely cannot tell us the client cwd/roots, every
helper here degrades to the served root, preserving the existing single
project and workspace/degraded behaviour exactly.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import TYPE_CHECKING

from aleph.project.paths import DB_FILENAME, DICT_FILENAME

if TYPE_CHECKING:  # avoid import cost / cycles at module load
    from aleph.mcp.handlers import AlephHandlers


# How far up the tree we walk from a client root looking for an index.
# Bounded so a pathological deep cwd can't cost an unbounded stat walk.
_MAX_WALK_UP = 40

# Bounded LRU of per-project handlers. Switching among a handful of repos
# never rebuilds; an unbounded set of repos can't leak handler/engine state.
_HANDLER_CACHE_CAP = 16


def has_index(directory: str) -> bool:
    """True if ``directory`` holds a built Aleph index (.aleph/dict|db).

    Mirrors :func:`aleph.project.paths.resolve_artifact_dir`'s notion of
    "built" but as a cheap boolean probe (two ``os.path.isfile`` stats).
    """
    aleph_subdir = os.path.join(directory, ".aleph")
    if os.path.isfile(os.path.join(aleph_subdir, DICT_FILENAME)):
        return True
    if os.path.isfile(os.path.join(aleph_subdir, DB_FILENAME)):
        return True
    # Backward-compat: artifacts written directly into the directory.
    if os.path.isfile(os.path.join(directory, DICT_FILENAME)):
        return True
    if os.path.isfile(os.path.join(directory, DB_FILENAME)):
        return True
    return False


def find_index_root(start_dir: str) -> str | None:
    """Walk up from ``start_dir`` to the nearest dir holding a built index.

    Returns the absolute path of the indexed repo, or ``None`` if none is
    found within the bounded walk. Used to map a client root / cwd to the
    repo whose index should answer the call.
    """
    try:
        current = os.path.abspath(start_dir)
    except (OSError, ValueError):
        return None
    seen = 0
    while seen < _MAX_WALK_UP:
        if has_index(current):
            return current
        parent = os.path.dirname(current)
        if parent == current:  # reached filesystem root
            break
        current = parent
        seen += 1
    return None


def find_repo_root(start_dir: str) -> str | None:
    """Walk up from ``start_dir`` to the nearest version-control repo root.

    Returns the dir containing a ``.git`` (the repo the agent is plausibly
    working in), or ``None``. Used to attribute an actionable
    "no index for <repo> — run `aleph build`" message to the RIGHT repo when
    the client is in a real but unbuilt project.
    """
    try:
        current = os.path.abspath(start_dir)
    except (OSError, ValueError):
        return None
    if os.path.isfile(current):
        current = os.path.dirname(current)
    seen = 0
    while seen < _MAX_WALK_UP:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        seen += 1
    return None


def _root_to_path(uri: str) -> str | None:
    """Convert a ``file://`` root URI to a local filesystem path."""
    if not uri:
        return None
    if uri.startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        path = unquote(parsed.path)
        # file://host/path is unusual for local roots; ignore any host.
        return path or None
    # Some clients hand back a bare path. Accept it.
    if os.path.isabs(uri):
        return uri
    return None


async def fetch_client_roots(ctx) -> list[str]:
    """Return the client's workspace root paths, or ``[]`` if unavailable.

    Capability-guarded, timeout-bounded, and fully fail-soft: any client
    that doesn't support ``roots``, errors, or is slow yields ``[]`` and the
    caller falls back to the served root. Never raises.
    """
    if ctx is None:
        return []
    try:
        session = ctx.session
    except Exception:
        return []
    if session is None:
        return []

    # Only ask clients that advertised the roots capability — otherwise the
    # request can hang or error on clients that ignore unknown requests.
    try:
        from mcp import types

        if not session.check_client_capability(
            types.ClientCapabilities(roots=types.RootsCapability())
        ):
            return []
    except Exception:
        return []

    try:
        import asyncio

        result = await asyncio.wait_for(session.list_roots(), timeout=2.0)
    except Exception:
        # TimeoutError, transport errors, unsupported method — all fail-soft.
        return []

    paths: list[str] = []
    for root in getattr(result, "roots", None) or []:
        path = _root_to_path(str(getattr(root, "uri", "")))
        if path:
            paths.append(os.path.abspath(path))
    return paths


def resolve_project_dir(
    served_root: str,
    client_roots: list[str],
    target_path: str | None = None,
) -> str:
    """Pick the project dir whose index should answer this call.

    Resolution order (first match wins):

      1. ``target_path`` — if the call names a path and that path lives
         under an indexed repo, use it (the symbol/file is the authority).
      2. a client root that *is* / lives under an indexed repo.
      3. the served root (the historical single-project behaviour).

    Always returns a path; the caller probes it for an index and emits an
    actionable error if none is reachable.
    """
    served_root = os.path.abspath(served_root)

    # 1. The target the caller asked about, if it names a REAL path: an
    #    absolute path, or a relative path that exists under the served root /
    #    a client root. A bare relative name that exists nowhere (e.g.
    #    "main.py" with no such file) must NOT be resolved against the
    #    server's CWD — that would silently pick whatever repo the server was
    #    launched in.
    if target_path:
        cand: str | None = None
        if os.path.isabs(target_path) and os.path.exists(target_path):
            cand = target_path
        else:
            for base in [served_root, *client_roots]:
                joined = os.path.join(base, target_path)
                if os.path.exists(joined):
                    cand = joined
                    break
        if cand is not None:
            idx = find_index_root(cand)
            if idx is not None:
                return idx
            # The target is in a real repo that just isn't built — attribute
            # the (later) no-index error to THAT repo, not the served root.
            repo = find_repo_root(cand)
            if repo is not None and repo != served_root:
                return repo

    # 2. A client root that maps to an indexed repo (the common case: the
    #    agent is working in one indexed repo among several under a parent).
    for root in client_roots:
        idx = find_index_root(root)
        if idx is not None:
            return idx

    # 3. A client root that is a real (but unbuilt) repo, when the served
    #    root can't answer either — so the agent gets "build <repo>" instead
    #    of a wrong-project or bare error. Skipped when the served root is
    #    itself indexed (single-project servers keep answering from it).
    if not has_index(served_root):
        for root in client_roots:
            repo = find_repo_root(root)
            if repo is not None and repo != served_root:
                return repo

    # 4. Fall back to the served root (single-project / unchanged behaviour).
    return served_root


def no_index_message(project_dir: str, served_root: str) -> str:
    """Actionable 'no reachable index' error, naming the repo.

    Replaces the bare "no .aleph artifacts" dead-end with a next action the
    agent (or user) can actually take, naming the exact repo.
    """
    repo = os.path.basename(os.path.abspath(project_dir).rstrip(os.sep)) or project_dir
    abs_dir = os.path.abspath(project_dir)
    msg = (
        f"No Aleph index for {repo} ({abs_dir}). "
        f"Run `aleph build` in that directory to generate it, then retry."
    )
    if os.path.abspath(served_root) != abs_dir:
        msg += (
            f"\n(The Aleph MCP server was launched in {os.path.abspath(served_root)}; "
            f"it resolved this call to {abs_dir} from your workspace roots, "
            f"but that repo has no built index yet.)"
        )
    return msg


class HandlerCache:
    """Bounded LRU of per-project :class:`AlephHandlers`.

    Lazy: a project's handlers (and its underlying engine/artifacts) are
    constructed on first use and reused thereafter, so switching among
    repos never rebuilds. Bounded so serving many repos can't leak state.
    """

    def __init__(self, agent_id: str = "default", cap: int = _HANDLER_CACHE_CAP) -> None:
        self._agent_id = agent_id
        self._cap = cap
        self._cache: "OrderedDict[str, AlephHandlers]" = OrderedDict()

    def get(self, project_dir: str) -> "AlephHandlers":
        """Return cached handlers for ``project_dir``, constructing if needed."""
        from aleph.mcp.handlers import AlephHandlers

        key = os.path.abspath(project_dir)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        handlers = AlephHandlers(project_dir=key, agent_id=self._agent_id)
        self._cache[key] = handlers
        self._cache.move_to_end(key)
        while len(self._cache) > self._cap:
            self._cache.popitem(last=False)
        return handlers

    def __len__(self) -> int:
        return len(self._cache)

    def __contains__(self, project_dir: str) -> bool:
        return os.path.abspath(project_dir) in self._cache
