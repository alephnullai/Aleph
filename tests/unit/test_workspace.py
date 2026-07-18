"""Tests for workspace-first scaling (P2-2).

Covers: `aleph workspace build` / `status`, the serve guard for
multi-repo directories, and per-project warnings for corrupt artifacts.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from aleph import cli
from aleph.mcp.handlers import AlephHandlers
from aleph.query.workspace import (
    WorkspaceEngine,
    find_workspace_file,
    load_workspace_projects,
    workspace_build,
    workspace_status,
)


@pytest.fixture
def workspace(tmp_path):
    """A workspace with two tiny projects and a .aleph-workspace.json."""
    proj_a = tmp_path / "proj_a"
    proj_a.mkdir()
    (proj_a / "alpha.py").write_text(
        "def alpha_one(x):\n    return x + 1\n\n"
        "def alpha_two(x):\n    return alpha_one(x) * 2\n"
    )
    proj_b = tmp_path / "proj_b"
    proj_b.mkdir()
    (proj_b / "beta.py").write_text(
        "def beta_main():\n    return 'beta'\n"
    )
    (tmp_path / ".aleph-workspace.json").write_text(json.dumps({
        "projects": {"proj-a": "proj_a", "proj-b": "proj_b"}
    }))
    return tmp_path


class TestWorkspaceConfig:
    def test_find_workspace_file(self, workspace):
        assert find_workspace_file(str(workspace)) is not None
        assert find_workspace_file(str(workspace / "proj_a")) is None

    def test_load_resolves_relative_paths(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        assert projects["proj-a"] == str(workspace / "proj_a")
        assert projects["proj-b"] == str(workspace / "proj_b")

    def test_load_rejects_invalid_json(self, tmp_path):
        ws = tmp_path / ".aleph-workspace.json"
        ws.write_text("{not json")
        with pytest.raises(ValueError, match="invalid workspace file"):
            load_workspace_projects(str(ws))

    def test_load_rejects_empty_projects(self, tmp_path):
        ws = tmp_path / ".aleph-workspace.json"
        ws.write_text("{}")
        with pytest.raises(ValueError, match="no projects"):
            load_workspace_projects(str(ws))


class TestWorkspaceBuild:
    def test_builds_every_project(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        reports = workspace_build(projects)
        assert len(reports) == 2
        assert all(r["success"] for r in reports)
        assert (workspace / "proj_a" / ".aleph" / "project.aleph.dict").is_file()
        assert (workspace / "proj_b" / ".aleph" / "project.aleph.dict").is_file()
        by_name = {r["name"]: r for r in reports}
        assert by_name["proj-a"]["files"] == 1
        assert by_name["proj-a"]["symbols"] >= 2

    def test_continues_past_missing_project(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        projects["ghost"] = str(workspace / "does_not_exist")
        reports = workspace_build(projects)
        by_name = {r["name"]: r for r in reports}
        assert not by_name["ghost"]["success"]
        assert "not found" in by_name["ghost"]["error"]
        # The real projects still built
        assert by_name["proj-a"]["success"]
        assert by_name["proj-b"]["success"]

    def test_cli_workspace_build(self, workspace, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["aleph", "workspace", "build", str(workspace), "--json"]
        )
        cli.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["succeeded"] == 2
        assert payload["failed"] == 0

    def test_cli_workspace_build_failure_exits_nonzero(self, workspace, monkeypatch, capsys):
        (workspace / ".aleph-workspace.json").write_text(json.dumps({
            "projects": {"proj-a": "proj_a", "ghost": "missing_dir"}
        }))
        monkeypatch.setattr(
            "sys.argv", ["aleph", "workspace", "build", str(workspace), "--json"]
        )
        with pytest.raises(SystemExit):
            cli.main()
        payload = json.loads(capsys.readouterr().out)
        assert payload["succeeded"] == 1
        assert payload["failed"] == 1


class TestWorkspaceStatus:
    def test_unbuilt_projects_report_not_built(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        statuses = workspace_status(projects)
        assert all(not s.built for s in statuses)
        assert all(s.stale for s in statuses)
        assert statuses[0].source_files == 1

    def test_stale_after_modifying_source(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build(projects)

        statuses = {s.name: s for s in workspace_status(projects)}
        assert statuses["proj-a"].built and not statuses["proj-a"].stale
        assert statuses["proj-a"].last_build

        # Modify a source file in proj-a so its content no longer
        # matches the recorded build stamp
        src = workspace / "proj_a" / "alpha.py"
        src.write_text(src.read_text() + "\ndef alpha_extra():\n    return 2\n")

        statuses = {s.name: s for s in workspace_status(projects)}
        assert statuses["proj-a"].stale
        assert statuses["proj-a"].stale_files == 1
        assert not statuses["proj-b"].stale

    def test_touch_without_edit_is_not_stale(self, workspace):
        """SQLite store staleness is content-aware: a touch (mtime bump
        with identical content) must NOT flag the project stale."""
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build(projects)

        src = workspace / "proj_a" / "alpha.py"
        future = time.time() + 60
        os.utime(src, (future, future))

        statuses = {s.name: s for s in workspace_status(projects)}
        assert not statuses["proj-a"].stale

    def test_missing_project_reports_error(self, tmp_path):
        statuses = workspace_status({"ghost": str(tmp_path / "nope")})
        assert statuses[0].error == "project directory not found"

    def test_cli_workspace_status_json(self, workspace, monkeypatch, capsys):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build(projects)
        monkeypatch.setattr(
            "sys.argv", ["aleph", "workspace", "status", str(workspace), "--json"]
        )
        cli.main()
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["projects"]) == 2
        assert all(p["built"] for p in payload["projects"])


class TestServeGuard:
    def _make_repo(self, root, name):
        repo = root / name
        (repo / ".git").mkdir(parents=True)
        (repo / "main.py").write_text("def main():\n    pass\n")
        return repo

    def test_multi_repo_dir_refused(self, tmp_path):
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        reason = cli._workspace_guard_reason(str(tmp_path))
        assert reason is not None
        assert "2 git repositories" in reason

    def test_git_rooted_dir_allowed(self, tmp_path):
        (tmp_path / ".git").mkdir()
        self._make_repo(tmp_path, "vendored_a")
        self._make_repo(tmp_path, "vendored_b")
        assert cli._workspace_guard_reason(str(tmp_path)) is None

    def test_single_project_allowed(self, tmp_path):
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        assert cli._workspace_guard_reason(str(tmp_path)) is None

    def test_huge_dir_without_git_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_SERVE_GUARD_MAX_FILES", 5)
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("x")
        reason = cli._workspace_guard_reason(str(tmp_path))
        assert reason is not None
        assert "no .git at its root" in reason

    def test_serve_refuses_multi_repo_dir_interactive(self, tmp_path, monkeypatch, capsys):
        """Interactive terminal runs (no MCP client) keep refuse-and-hint."""
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        monkeypatch.setattr(cli, "_stdio_client_attached", lambda: False)
        monkeypatch.setattr("sys.argv", ["aleph", "serve", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Refusing to serve" in err
        assert ".aleph-workspace.json" in err
        assert "aleph workspace build" in err

    def test_serve_multi_repo_stdio_starts_degraded_server(
        self, tmp_path, monkeypatch, capsys
    ):
        """MCP stdio mode must never exit mid-handshake on a multi-repo
        parent (issue #1): it serves a degraded-but-alive server whose
        tool calls return setup instructions."""
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        served = {}

        def fake_serve(root, degraded_message=None):
            served["root"] = root
            served["degraded_message"] = degraded_message

        monkeypatch.setattr(cli, "_stdio_client_attached", lambda: True)
        monkeypatch.setattr("aleph.mcp.server.serve", fake_serve)
        monkeypatch.setattr("sys.argv", ["aleph", "serve", str(tmp_path)])
        cli.main()  # must NOT raise SystemExit
        assert served["root"] == str(tmp_path)
        msg = served["degraded_message"]
        assert msg is not None
        assert "2 git repositories" in msg
        assert ".aleph-workspace.json" in msg
        assert "aleph workspace build" in msg
        err = capsys.readouterr().err
        assert "degraded mode" in err
        # No single-project artifacts were built for the multi-repo parent
        assert not (tmp_path / ".aleph" / "project.aleph.dict").exists()

    def test_serve_multi_repo_stdio_autodetects_cwd_project(
        self, tmp_path, monkeypatch
    ):
        """When cwd is inside one nested repo, stdio serve picks that
        project instead of going degraded."""
        repo_a = self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        served = {}

        def fake_serve(root, degraded_message=None, skip_root_build=False):
            served["root"] = root
            served["degraded_message"] = degraded_message
            served["skip_root_build"] = skip_root_build

        monkeypatch.setattr(cli, "_stdio_client_attached", lambda: True)
        monkeypatch.setattr("aleph.mcp.server.serve", fake_serve)
        monkeypatch.chdir(repo_a)
        monkeypatch.setattr("sys.argv", ["aleph", "serve", str(tmp_path)])
        cli.main()
        assert served["root"] == str(repo_a)
        assert served["degraded_message"] is None
        assert served["skip_root_build"] is False  # real project: heal allowed

    def test_serve_single_project_unchanged(self, tmp_path, monkeypatch):
        """Single-project serve still serves normally in stdio mode."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        served = {}
        monkeypatch.setattr(cli, "_stdio_client_attached", lambda: True)
        monkeypatch.setattr(
            "aleph.mcp.server.serve",
            lambda root, skip_root_build=False: served.update(
                root=root, skip_root_build=skip_root_build
            ),
        )
        monkeypatch.setattr("sys.argv", ["aleph", "serve", str(tmp_path)])
        cli.main()
        assert served["root"] == str(tmp_path)
        assert served["skip_root_build"] is False

    def test_degraded_server_tools_return_guard_message(self, tmp_path):
        """A degraded server completes registration and every tool call
        returns the actionable guard message."""
        from aleph.mcp.server import create_server

        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        reason = cli._workspace_guard_reason(str(tmp_path))
        msg = cli._workspace_guard_tool_message(str(tmp_path), reason)
        server = create_server(str(tmp_path), degraded_message=msg)
        tools = server._tool_manager._tools
        assert "aleph_map" in tools and "aleph_search" in tools
        # Tool wrappers are async (per-call project resolution); degraded
        # mode short-circuits to the guard message before any resolution.
        assert asyncio.run(tools["aleph_map"].fn(ctx=None)) == msg
        assert asyncio.run(tools["aleph_search"].fn(term="anything", ctx=None)) == msg
        assert "degraded mode" in msg
        assert "2 git repositories" in msg

    def test_detect_project_for_cwd_outside_repos(self, tmp_path, monkeypatch):
        """cwd at the multi-repo parent itself matches no nested repo."""
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        monkeypatch.chdir(tmp_path)
        assert cli._detect_project_for_cwd(str(tmp_path)) is None

    def test_serve_force_bypasses_guard(self, tmp_path, monkeypatch):
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        served = {}
        monkeypatch.setattr(
            "aleph.mcp.server.serve",
            lambda root, skip_root_build=False: served.update(
                root=root, skip_root_build=skip_root_build
            ),
        )
        monkeypatch.setattr(
            "sys.argv", ["aleph", "serve", str(tmp_path), "--force"]
        )
        cli.main()
        assert served["root"] == str(tmp_path)
        assert served["skip_root_build"] is False  # --force opts in to root build

    def test_serve_with_workspace_config_skips_root_build(self, tmp_path, monkeypatch, capsys):
        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        (tmp_path / ".aleph-workspace.json").write_text(json.dumps({
            "projects": {"a": "repo_a", "b": "repo_b"}
        }))
        served = {}
        monkeypatch.setattr(
            "aleph.mcp.server.serve",
            lambda root, skip_root_build=False: served.update(
                root=root, skip_root_build=skip_root_build
            ),
        )
        monkeypatch.setattr("sys.argv", ["aleph", "serve", str(tmp_path)])
        cli.main()
        assert served["root"] == str(tmp_path)
        assert served["skip_root_build"] is True  # plumbed through to serve()
        err = capsys.readouterr().err
        assert "Workspace detected" in err
        # No single-project artifacts were built for the workspace root
        assert not (tmp_path / ".aleph" / "project.aleph.dict").exists()

    def test_serve_workspace_root_skips_auto_migrate_heal(
        self, tmp_path, monkeypatch, capsys
    ):
        """Regression (PR #6 + workspace): a workspace root with a stale
        single-project .aleph store must NOT run the synchronous
        auto-migrate heal (migrate + full root rebuild) before the MCP
        handshake — that stalls clients past their connect timeout. It
        hints instead, and never starts the root rebuild watcher."""
        from aleph.mcp import server as mcp_server

        self._make_repo(tmp_path, "repo_a")
        self._make_repo(tmp_path, "repo_b")
        (tmp_path / ".aleph-workspace.json").write_text(json.dumps({
            "projects": {"a": "repo_a", "b": "repo_b"}
        }))
        # Stale scheme-v1 single-project artifacts at the workspace root
        # (e.g. the parent dir was once built as one project).
        aleph_dir = tmp_path / ".aleph"
        aleph_dir.mkdir()
        # resolve_artifact_dir only honors .aleph/ once a dict exists.
        (aleph_dir / "project.aleph.dict").write_text(
            f"[ROOT:{tmp_path}]\n[ID_SCHEME:1]\n"
        )
        (aleph_dir / "project.aleph.map").write_text(
            f"[ROOT:{tmp_path}]\n[ID_SCHEME:1]\n"
        )

        calls = {"migrate": 0, "watch": 0}
        monkeypatch.setattr(
            "aleph.symbols.id_migration.auto_migrate_ids",
            lambda *a, **k: calls.__setitem__("migrate", calls["migrate"] + 1),
        )
        monkeypatch.setattr(
            mcp_server, "_start_auto_rebuild",
            lambda *a, **k: calls.__setitem__("watch", calls["watch"] + 1),
        )

        class _FakeMCP:
            def run(self, transport):
                pass

        monkeypatch.setattr(
            mcp_server, "create_server", lambda *a, **k: _FakeMCP()
        )
        mcp_server.serve(str(tmp_path), skip_root_build=True)
        assert calls["migrate"] == 0  # no pre-handshake heal of the root
        assert calls["watch"] == 0  # no single-project rebuild watcher
        err = capsys.readouterr().err
        assert "migrate-ids" in err  # downgraded to the manual hint

        # Sanity: the normal single-project path still heals — on the
        # deferred-startup thread, AFTER the handshake (pre-handshake
        # purity). Join it so the assertions are deterministic.
        mcp_server.serve(str(tmp_path), skip_root_build=False)
        assert mcp_server._startup_thread is not None
        mcp_server._startup_thread.join(timeout=30)
        assert not mcp_server._startup_thread.is_alive()
        assert calls["migrate"] == 1
        assert calls["watch"] == 1


class TestCorruptProjectWarnings:
    """Corrupt project artifacts must surface a warning, not vanish silently."""

    def _corrupt_dict(self, project_dir):
        aleph_dir = project_dir / ".aleph"
        aleph_dir.mkdir(exist_ok=True)
        # Invalid UTF-8 — deserialization raises UnicodeDecodeError
        (aleph_dir / "project.aleph.dict").write_bytes(b"\xff\xfe\xfa garbage")

    def test_engine_records_warning(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build({"proj-a": projects["proj-a"]})
        self._corrupt_dict(workspace / "proj_b")

        engine = WorkspaceEngine(projects)
        results = engine.search("alpha")
        # Good project still returns results
        assert any(r.project == "proj-a" for r in results)
        # Corrupt project produced a warning, not silence
        assert "proj-b" in engine.warnings
        assert "corrupt" in engine.warnings["proj-b"].lower()

    def test_missing_artifacts_warning(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build({"proj-a": projects["proj-a"]})
        engine = WorkspaceEngine(projects)
        engine.search("alpha")
        assert "proj-b" in engine.warnings
        assert "no artifacts" in engine.warnings["proj-b"]

    def test_handler_output_includes_warning(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build({"proj-a": projects["proj-a"]})
        self._corrupt_dict(workspace / "proj_b")

        handlers = AlephHandlers(project_dir=str(workspace))
        out = handlers.handle_workspace_search("alpha")
        assert "[proj-a]" in out
        assert "[WARNING] proj-b:" in out

    def test_workspace_status_handler(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build(projects)
        handlers = AlephHandlers(project_dir=str(workspace))
        out = handlers.handle_workspace_status()
        assert "WORKSPACE STATUS" in out
        assert "[proj-a] fresh" in out
        assert "[proj-b] fresh" in out

    def test_workspace_status_handler_reports_stale(self, workspace):
        projects = load_workspace_projects(str(workspace / ".aleph-workspace.json"))
        workspace_build(projects)
        src = workspace / "proj_a" / "alpha.py"
        src.write_text(src.read_text() + "\ndef alpha_extra():\n    return 2\n")

        handlers = AlephHandlers(project_dir=str(workspace))
        out = handlers.handle_workspace_status()
        assert "[proj-a] STALE" in out
        assert "aleph workspace build" in out
