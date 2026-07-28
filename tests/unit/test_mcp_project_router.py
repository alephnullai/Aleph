"""Per-tool-call project resolution (P0 adoption fix #1).

A single globally-configured `aleph serve .` is pinned to one launch dir.
These tests prove the server now FOLLOWS the agent across indexed repos:
a tool call resolves to the index of the repo named by the client's
workspace roots / the call's path target, lazily loaded and cached, while
single-project and workspace/degraded behaviour stay byte-for-byte the same.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

import aleph.mcp.server as server_mod
from aleph.mcp.project_router import (
    HandlerCache,
    _root_to_path,
    find_index_root,
    has_index,
    no_index_message,
    resolve_project_dir,
)
from aleph.mcp.server import create_server


# ── fixtures ──


def _make_indexed_repo(root, name: str, symbol: str) -> str:
    """Create a tiny built Aleph repo under ``root`` and return its path."""
    repo = root / name
    aleph_dir = repo / ".aleph"
    aleph_dir.mkdir(parents=True)
    (aleph_dir / "project.aleph.dict").write_text(
        "[ALEPH:DICT:1.0]\n"
        f"[ROOT:{repo}]\n"
        "[SYMBOLS]\n"
        f"f_{symbol}={symbol} file=main.py kind=f scope=module\n"
        "[/SYMBOLS]\n"
    )
    (aleph_dir / "project.aleph.map").write_text(
        "[ALEPH:MAP:1.0]\n"
        f"[ROOT:{repo}]\n"
        "[FILES]\n"
        "main.py hash=abc lang=python syms=1 calls=0 tokens=10->5 reduction=50.0%\n"
        "[/FILES]\n"
    )
    # A real source file so a path target resolves under this repo.
    (repo / "main.py").write_text(f"def {symbol}():\n    return 1\n")
    return str(repo)


@pytest.fixture
def two_repos(tmp_path):
    """A root containing two independently-indexed repos."""
    root = tmp_path / "Repos"
    root.mkdir()
    repo_a = _make_indexed_repo(root, "alpha", "alpha_fn")
    repo_b = _make_indexed_repo(root, "beta", "beta_fn")
    return str(root), repo_a, repo_b


class _FakeRoot:
    def __init__(self, path: str):
        # Emit what a conforming MCP client actually sends. `"file://" + path`
        # is a POSIX-only accident: on Windows it yields `file://C:\...`, where
        # the drive lands in the URI *host* and the path is empty. Path.as_uri()
        # gives the real thing (`file:///C:/...`, percent-encoded).
        self.uri = Path(path).as_uri()


class _FakeSession:
    """Minimal MCP session stub: advertises roots and answers list_roots."""

    def __init__(self, roots: list[str] | None, supports: bool = True):
        self._roots = roots
        self._supports = supports

    def check_client_capability(self, capability) -> bool:
        return self._supports

    async def list_roots(self):
        class R:
            roots = [_FakeRoot(p) for p in (self._roots or [])]
        if self._roots is None:
            raise RuntimeError("client does not implement roots")
        return R()


class _FakeCtx:
    def __init__(self, session):
        self.session = session


# ── unit: file:// root URI → local path ──


class TestRootUriToPath:
    """A client root arrives as a `file://` URI; it must survive the trip.

    Regression: on Windows the old parser returned urlparse().path verbatim
    (`/C:/Users/x`), which os.path.abspath then mangled into `<cwd>:\\C:\\...`.
    Every client root failed has_index() and was dropped, so the server always
    fell back to the served root — per-call routing was dead on Windows and the
    fail-soft design hid it. These assert the round-trip on the live platform.
    """

    def test_roundtrips_a_real_client_uri(self, tmp_path):
        # Exactly how a conforming client names a directory.
        assert _root_to_path(tmp_path.as_uri()) == str(tmp_path)

    def test_roundtrips_path_with_spaces(self, tmp_path):
        spaced = tmp_path / "my repo"
        spaced.mkdir()
        # Percent-encoded in the URI; must come back decoded.
        assert "%20" in spaced.as_uri()
        assert _root_to_path(spaced.as_uri()) == str(spaced)

    def test_result_is_usable_as_a_path(self, two_repos):
        """The parsed root must actually locate the index (the bug's real bite)."""
        _, repo_a, _ = two_repos
        assert has_index(_root_to_path(Path(repo_a).as_uri())) is True

    def test_accepts_bare_absolute_path(self, tmp_path):
        assert _root_to_path(str(tmp_path)) == str(tmp_path)

    def test_rejects_empty_and_relative(self):
        assert _root_to_path("") is None
        assert _root_to_path("relative/dir") is None

    @pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
    def test_windows_drive_as_host_is_tolerated(self):
        # Sloppy clients emit `file://C:/x` (drive parsed as URI host).
        assert _root_to_path("file://C:/Users/x") == r"C:\Users\x"

    @pytest.mark.skipif(os.name != "nt", reason="UNC is a Windows concept")
    def test_windows_unc_share(self):
        assert _root_to_path("file://server/share/x") == r"\\server\share\x"


# ── unit: pure resolution helpers ──


class TestResolutionHelpers:
    def test_has_index_true_with_dict(self, two_repos):
        _, repo_a, _ = two_repos
        assert has_index(repo_a) is True

    def test_has_index_false_without_artifacts(self, tmp_path):
        assert has_index(str(tmp_path)) is False

    def test_find_index_root_walks_up(self, two_repos):
        _, repo_a, _ = two_repos
        nested = os.path.join(repo_a, "src", "deep")
        os.makedirs(nested, exist_ok=True)
        assert find_index_root(nested) == os.path.abspath(repo_a)

    def test_find_index_root_none_when_unindexed(self, tmp_path):
        assert find_index_root(str(tmp_path)) is None

    def test_resolve_prefers_client_root_repo(self, two_repos):
        root, repo_a, repo_b = two_repos
        # Served at the parent (no index); client is working in beta.
        chosen = resolve_project_dir(root, [repo_b], target_path=None)
        assert chosen == os.path.abspath(repo_b)

    def test_resolve_target_path_wins(self, two_repos):
        root, repo_a, repo_b = two_repos
        # Client root says alpha, but the call names a file in beta.
        chosen = resolve_project_dir(
            root, [repo_a], target_path=os.path.join(repo_b, "main.py")
        )
        assert chosen == os.path.abspath(repo_b)

    def test_resolve_falls_back_to_served_root(self, two_repos):
        root, repo_a, _ = two_repos
        # No client roots, no target → served root unchanged.
        assert resolve_project_dir(repo_a, [], None) == os.path.abspath(repo_a)

    def test_bare_nonexistent_relative_target_ignored(self, two_repos):
        root, repo_a, _ = two_repos
        # A relative name that exists nowhere must NOT resolve against CWD;
        # it falls through to the served root.
        chosen = resolve_project_dir(repo_a, [], target_path="does_not_exist.py")
        assert chosen == os.path.abspath(repo_a)


# ── unit: bounded LRU handler cache ──


class TestHandlerCache:
    def test_lazy_and_cached(self, two_repos):
        _, repo_a, repo_b = two_repos
        cache = HandlerCache()
        assert len(cache) == 0
        h1 = cache.get(repo_a)
        assert len(cache) == 1
        # Same repo → same instance (no rebuild on switch-back).
        assert cache.get(repo_a) is h1
        h2 = cache.get(repo_b)
        assert h2 is not h1
        assert len(cache) == 2
        assert cache.get(repo_a) is h1  # still cached

    def test_bounded_eviction(self, tmp_path):
        cache = HandlerCache(cap=2)
        a = str(tmp_path / "a"); b = str(tmp_path / "b"); c = str(tmp_path / "c")
        cache.get(a); cache.get(b); cache.get(c)
        assert len(cache) == 2
        assert a not in cache  # LRU evicted
        assert b in cache and c in cache


# ── no-index error is actionable ──


class TestActionableError:
    def test_names_repo_and_build_command(self, two_repos):
        root, repo_a, _ = two_repos
        missing = os.path.join(root, "gamma")
        msg = no_index_message(missing, served_root=root)
        assert "gamma" in msg
        assert "aleph build" in msg
        # Not the old dead-end string.
        assert "no .aleph artifacts" not in msg.lower()


# ── integration: a real registered tool resolves per cwd/target ──


def _call(tool_fn, *, ctx, **kwargs):
    return asyncio.run(tool_fn(ctx=ctx, **kwargs))


class TestPerCallResolution:
    def test_tool_resolves_to_client_root_repo(self, two_repos):
        root, repo_a, repo_b = two_repos
        # Serve the PARENT (multi-repo) but force a normal server so tools run.
        server = create_server(root)
        search = server._tool_manager._tools["aleph_search"].fn

        # Client working in alpha → alpha's index answers.
        out_a = _call(search, ctx=_FakeCtx(_FakeSession([repo_a])), term="alpha")
        assert "alpha_fn" in out_a
        assert "beta_fn" not in out_a

        # Same server, client now in beta → beta's index answers (no restart).
        out_b = _call(search, ctx=_FakeCtx(_FakeSession([repo_b])), term="beta")
        assert "beta_fn" in out_b
        assert "alpha_fn" not in out_b

    def test_tool_caches_per_project(self, two_repos):
        root, repo_a, repo_b = two_repos
        fn = create_server(root)._tool_manager._tools["aleph_search"].fn
        server_mod._handler_cache._cache.clear()
        _call(fn, ctx=_FakeCtx(_FakeSession([repo_a])), term="alpha")
        h_first = server_mod._handler_cache.get(repo_a)
        _call(fn, ctx=_FakeCtx(_FakeSession([repo_a])), term="alpha")
        # Two calls into the same repo → same cached handler set, no rebuild.
        assert os.path.abspath(repo_a) in server_mod._handler_cache
        assert server_mod._handler_cache.get(repo_a) is h_first

    def test_unindexed_client_root_gives_actionable_error(self, two_repos, tmp_path):
        root, repo_a, _ = two_repos
        fn = create_server(repo_a)._tool_manager._tools["aleph_struct"].fn
        # An unindexed repo with its own .git so the walk-up stops there (a
        # real repo the agent is in, just not built yet) and a file target.
        unindexed = tmp_path / "Repos" / "delta"
        (unindexed / ".git").mkdir(parents=True)
        target = unindexed / "thing.py"
        target.write_text("x = 1\n")
        out = _call(
            fn,
            ctx=_FakeCtx(_FakeSession([str(unindexed)])),
            file=str(target),
        )
        assert "delta" in out
        assert "aleph build" in out

    def test_no_roots_falls_back_to_served_root(self, two_repos):
        root, repo_a, repo_b = two_repos
        create_server(repo_a)  # single-project served at alpha
        fn = create_server(repo_a)._tool_manager._tools["aleph_search"].fn
        # Client without roots capability → unchanged single-project behaviour.
        out = _call(fn, ctx=_FakeCtx(_FakeSession(None, supports=False)), term="alpha")
        assert "alpha_fn" in out

    def test_ctx_none_uses_served_root(self, two_repos):
        root, repo_a, _ = two_repos
        fn = create_server(repo_a)._tool_manager._tools["aleph_search"].fn
        out = _call(fn, ctx=None, term="alpha")
        assert "alpha_fn" in out

    def test_workspace_tools_ignore_per_call_resolution(self, two_repos):
        """Workspace tools stay scoped to the served root, never a sub-project.

        Even when the client root resolves to one indexed sub-repo, a
        workspace tool must use the served-root handlers (its
        .aleph-workspace.json spans all projects).
        """
        root, repo_a, repo_b = two_repos
        fn = create_server(root)._tool_manager._tools["aleph_workspace_status"].fn
        # Client is in alpha; without the served-root pin this would try to
        # use alpha's (non-workspace) handlers. It must use the served root.
        out = _call(fn, ctx=_FakeCtx(_FakeSession([repo_a])))
        # Served root has no workspace config → the no-workspace message,
        # NOT alpha's data — proving resolution was bypassed.
        assert "workspace" in out.lower()
