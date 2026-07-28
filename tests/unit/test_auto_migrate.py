"""Serve-startup self-heal for relocated artifacts (auto migrate-ids).

A machine move (e.g. macOS ~/Repos/Aleph -> Windows P:\\repos\\aleph)
leaves artifacts whose ``[ROOT:...]`` header records the old absolute
root. Historically every CLI/serve only *hinted* ``aleph migrate-ids``
while full-body EXPAND quietly returned "no body found" until someone
migrated and rebuilt by hand. These tests cover the serve-time auto-heal
(:func:`aleph.symbols.id_migration.auto_migrate_ids`):

  * the shared detection condition fires after a simulated relocation,
  * auto-migrate heals at serve startup and EXPAND finds bodies again,
  * ALEPH_AUTO_MIGRATE=0 disables the heal and falls back to the hint,
  * a second startup is a no-op (idempotent),
  * failures degrade to the manual hint — serve never dies.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

import pytest

from aleph.epistemic.store import EpistemicStore
from aleph.symbols import id_migration
from aleph.symbols.id_migration import (
    auto_migrate_ids,
    compute_id_mapping,
    detect_stale_artifacts,
)

# What a cross-OS machine move looks like in the artifact headers.
OLD_ROOT = "/Users/fake/old-project"

PROJECT_FILES = {
    "mod.py": (
        "def foo(x):\n"
        "    return x + 1\n"
        "\n"
        "\n"
        "def bar(y):\n"
        "    return foo(y) * 2\n"
    ),
    os.path.join("pkg", "util.py"): (
        "def helper(a, b):\n"
        "    return a - b\n"
    ),
}

# read_artifact_meta consults these headers (map first, dict fallback).
META_ARTIFACTS = ("project.aleph.map", "project.aleph.dict")


def _rewrite_root(root: str, old_root: str = OLD_ROOT) -> None:
    """Rewrite the [ROOT:...] header lines the way a machine move looks."""
    for name in META_ARTIFACTS:
        path = os.path.join(root, ".aleph", name)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rewritten = [
            f"[ROOT:{old_root}]\n" if line.startswith("[ROOT:") else line
            for line in lines
        ]
        assert rewritten != lines, f"no [ROOT:...] line found in {name}"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(rewritten)


def _strip_id_scheme(root: str) -> None:
    """Drop the [ID_SCHEME:n] line — artifacts then read as scheme v1."""
    for name in META_ARTIFACTS:
        path = os.path.join(root, ".aleph", name)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(l for l in lines if not l.startswith("[ID_SCHEME:"))


@pytest.fixture(scope="module")
def built_template(tmp_path_factory):
    """One real `aleph build` (subprocess, like a user would) per module."""
    root = str(tmp_path_factory.mktemp("template") / "proj")
    for rel, content in PROJECT_FILES.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    proc = subprocess.run(
        [sys.executable, "-m", "aleph.cli", "build", root, "--quiet"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return root


@pytest.fixture
def relocated(built_template, tmp_path):
    """A built project 'moved' to a new location with a foreign old ROOT."""
    root = str(tmp_path / "proj")
    shutil.copytree(built_template, root)
    _rewrite_root(root)
    id_migration._HINTED_DIRS.clear()
    return root


def _engine(root):
    from aleph.query.engine import QueryEngine

    return QueryEngine(root)


def _foo_body(root) -> str | None:
    """search 'foo' -> expand its id, the way the live regression surfaced."""
    engine = _engine(root)
    results = engine.search("foo")
    sid = next(r.symbol_id for r in results if r.qualified_name == "foo")
    return engine.expand(sid)


# ── Detection ──


class TestDetection:
    def test_relocation_detected(self, relocated):
        stale = detect_stale_artifacts(relocated)
        assert stale is not None
        assert stale.reason == "location"
        assert stale.old_root == OLD_ROOT

    def test_old_scheme_and_relocated(self, relocated):
        _strip_id_scheme(relocated)
        stale = detect_stale_artifacts(relocated)
        assert stale is not None
        assert stale.reason == "location"  # root mismatch wins
        assert stale.id_scheme == 1
        assert stale.old_root == OLD_ROOT

    def test_clean_build_silent(self, built_template):
        assert detect_stale_artifacts(built_template) is None

    def test_no_artifacts_silent(self, tmp_path):
        assert detect_stale_artifacts(str(tmp_path)) is None


# ── Auto-migration (the serve-startup hook calls exactly this) ──


class TestAutoMigrate:
    def test_heals_and_expand_finds_body(self, relocated, capsys):
        # Epistemic state keyed under the OLD (v1, absolute-path) scheme,
        # like a pre-move agent left behind.
        plan = compute_id_mapping(relocated, old_root=OLD_ROOT)
        old_foo = next(o for o, name in plan.names.items() if name == "foo")
        new_foo = plan.mapping[old_foo]
        store = EpistemicStore(
            os.path.join(relocated, ".aleph", "project.aleph.epistemic")
        )
        with store.transaction() as data:
            data.setdefault("inferences", []).append(
                {"symbol_id": old_foo, "conclusion": "adds one", "confidence": 0.9}
            )

        assert auto_migrate_ids(relocated) is True
        err = capsys.readouterr().err
        assert f"artifacts built at {OLD_ROOT}" in err
        assert "auto-migrating ids to" in err

        # Healed: detection no longer fires, EXPAND finds the body again.
        assert detect_stale_artifacts(relocated) is None
        body = _foo_body(relocated)
        assert body is not None and "x + 1" in body
        # Epistemic reference carried to the current scheme.
        assert store.load()["inferences"][0]["symbol_id"] == new_foo

    def test_old_scheme_and_relocated_heals(self, relocated):
        _strip_id_scheme(relocated)
        assert auto_migrate_ids(relocated) is True
        assert detect_stale_artifacts(relocated) is None
        body = _foo_body(relocated)
        assert body is not None and "x + 1" in body

    def test_idempotent_second_start_is_noop(self, relocated, capsys):
        assert auto_migrate_ids(relocated) is True
        assert auto_migrate_ids(relocated) is False  # second startup: no-op
        err = capsys.readouterr().err
        assert err.count("auto-migrating ids to") == 1
        assert "auto-migrate failed" not in err

    def test_env_disable_falls_back_to_hint(self, relocated, monkeypatch, capsys):
        monkeypatch.setenv("ALEPH_AUTO_MIGRATE", "0")
        assert auto_migrate_ids(relocated) is False
        err = capsys.readouterr().err
        assert "auto-migrating" not in err
        assert "migrate-ids" in err  # today's hint, unchanged
        assert detect_stale_artifacts(relocated) is not None  # nothing healed

    def test_clean_build_is_noop(self, built_template, capsys):
        id_migration._HINTED_DIRS.clear()
        assert auto_migrate_ids(built_template) is False
        assert "auto-migrat" not in capsys.readouterr().err

    def test_case_only_drift_hints_without_migrating(
        self, built_template, tmp_path, capsys
    ):
        """Case-only path drift must NOT auto-heal (hint only).

        On a case-insensitive filesystem (macOS/Windows) the same project
        can be launched as ~/Repos/Aleph one boot and ~/repos/aleph the
        next. v2 IDs hash the lowercased relative path, so nothing is
        stale — auto-healing would rewrite the ROOT line to whichever case
        launched last and trigger a full migrate+rebuild on every boot.
        """
        root = str(tmp_path / "proj")
        shutil.copytree(built_template, root)
        case_root = root.swapcase()
        assert case_root != root  # sanity: the drifted case really differs
        _rewrite_root(root, old_root=case_root)
        id_migration._HINTED_DIRS.clear()

        stale = detect_stale_artifacts(root)
        assert stale is not None and stale.reason == "case"

        assert auto_migrate_ids(root) is False  # no heal
        err = capsys.readouterr().err
        assert "auto-migrating" not in err
        assert "migrate-ids" in err  # hint emitted instead

        # Untouched: the recorded ROOT still carries the drifted case (no
        # rebuild ran), and detection still reports case-only drift.
        stale = detect_stale_artifacts(root)
        assert stale is not None
        assert stale.reason == "case"
        assert stale.old_root == case_root

    def test_failure_degrades_to_hint(self, relocated, monkeypatch, capsys):
        def boom(*args, **kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(id_migration, "migrate_ids", boom)
        assert auto_migrate_ids(relocated) is False  # never raises
        err = capsys.readouterr().err
        assert "auto-migrate failed: disk on fire" in err
        assert "migrate-ids" in err  # manual hint still printed


# ── Real serve over stdio (the actual startup path) ──


def _rpc_line(proc, msg: dict) -> None:
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _read_result(proc, want_id: int) -> dict:
    while True:
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read().decode("utf-8", errors="replace")
            raise AssertionError(f"server exited before responding:\n{err}")
        try:
            msg = json.loads(line.decode("utf-8"))
        except ValueError:
            continue  # not a JSON-RPC line
        if msg.get("id") == want_id:
            assert "error" not in msg, msg
            return msg["result"]


def _tool_text(result: dict) -> str:
    return "".join(c.get("text", "") for c in result["content"])


class _ServeSession:
    """Minimal JSON-RPC client over a real `aleph serve` stdio subprocess."""

    def __init__(self, root: str, extra_env: dict | None = None):
        env = os.environ.copy()
        env["ALEPH_AUTO_REBUILD"] = "false"  # no background watcher in tests
        env["PYTHONIOENCODING"] = "utf-8"
        env.pop("ALEPH_AUTO_MIGRATE", None)
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "aleph.cli", "serve", root, "--force"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self._watchdog = threading.Timer(120, self.proc.kill)
        self._watchdog.start()
        self._next_id = 1

    def initialize(self) -> dict:
        result = self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )
        _rpc_line(self.proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def call(self, method: str, params: dict) -> dict:
        msg_id = self._next_id
        self._next_id += 1
        _rpc_line(
            self.proc,
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
        )
        return _read_result(self.proc, msg_id)

    def tool(self, name: str, arguments: dict) -> str:
        return _tool_text(
            self.call("tools/call", {"name": name, "arguments": arguments})
        )

    def close(self) -> str:
        """Shut down and return everything the server wrote to stderr."""
        try:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
            return self.proc.stderr.read().decode("utf-8", errors="replace")
        finally:
            self._watchdog.cancel()


class TestServeStdio:
    def test_serve_auto_migrates_then_expand_works(self, relocated):
        session = _ServeSession(relocated)
        try:
            session.initialize()
            # Pre-handshake purity: the heal now runs on the deferred
            # startup thread AFTER initialize (see mcp.server
            # _deferred_startup). Wait for it to persist before driving
            # tools / killing the server, like a client whose first tool
            # call arrives a moment after connect.
            deadline = time.monotonic() + 30
            while (detect_stale_artifacts(relocated) is not None
                   and time.monotonic() < deadline):
                time.sleep(0.05)
            assert detect_stale_artifacts(relocated) is None, (
                "deferred auto-migrate heal never completed"
            )
            search = session.tool("aleph_search", {"term": "foo"})
            assert "Matches" in search
            sid = search.splitlines()[1].strip().split()[0]
            body = session.tool("aleph_expand", {"symbol_id": sid})
            assert "no body found" not in body.lower()  # the live regression
            assert "x + 1" in body or "foo(y)" in body
        finally:
            stderr = session.close()
        assert f"artifacts built at {OLD_ROOT}" in stderr
        assert "auto-migrating ids to" in stderr

        # Second serve start: already healed, migrates nothing.
        session = _ServeSession(relocated)
        try:
            session.initialize()
        finally:
            stderr = session.close()
        assert "auto-migrating" not in stderr
        assert "migrate-ids" not in stderr  # no hint either — it's healed

    def test_serve_with_auto_migrate_disabled(self, relocated):
        session = _ServeSession(relocated, extra_env={"ALEPH_AUTO_MIGRATE": "0"})
        try:
            session.initialize()  # serve still starts (degraded, hint-only)
        finally:
            stderr = session.close()
        assert "auto-migrating" not in stderr
        assert "migrate-ids" in stderr  # today's hint preserved
        assert detect_stale_artifacts(relocated) is not None  # untouched
