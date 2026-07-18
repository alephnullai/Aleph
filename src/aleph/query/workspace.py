# Commercial Component — governed by COMMERCIAL-LICENSE.md, not MIT. See LICENSE.
"""Cross-project workspace engine — queries across multiple Aleph projects.

A workspace is defined by a ``.aleph-workspace.json`` file:

    {"projects": {"name": "/abs/or/relative/path", ...}}

Relative paths resolve against the workspace file's directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from aleph.query.engine import QueryEngine, SearchResult, ResolveResult, CallerEntry

WORKSPACE_FILENAME = ".aleph-workspace.json"


def find_workspace_file(root: str) -> str | None:
    """Locate the workspace config for a directory.

    Checks <root>/.aleph-workspace.json, then the ALEPH_WORKSPACE env var.
    """
    ws_path = os.path.join(root, WORKSPACE_FILENAME)
    if os.path.isfile(ws_path):
        return ws_path
    env_path = os.environ.get("ALEPH_WORKSPACE", "")
    if env_path and os.path.isfile(env_path):
        return env_path
    return None


def load_workspace_projects(ws_path: str) -> dict[str, str]:
    """Parse a workspace file into {project_name: absolute_project_dir}.

    Raises ValueError for unreadable/invalid config or an empty project map.
    """
    try:
        with open(ws_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"invalid workspace file {ws_path}: {e}") from e

    projects = config.get("projects", {})
    if not projects or not isinstance(projects, dict):
        raise ValueError(
            f"workspace file {ws_path} defines no projects "
            f'(expected {{"projects": {{"name": "/path"}}}})'
        )

    ws_dir = os.path.dirname(os.path.abspath(ws_path))
    resolved: dict[str, str] = {}
    for name, path in projects.items():
        if not os.path.isabs(path):
            path = os.path.join(ws_dir, path)
        resolved[name] = os.path.abspath(path)
    return resolved


@dataclass
class ProjectStatus:
    """Build/staleness status for one workspace project."""
    name: str
    path: str
    exists: bool = False
    built: bool = False
    source_files: int = 0
    last_build: str = ""          # ISO timestamp of artifact mtime
    stale: bool = False
    stale_files: int = 0          # sources newer than the artifacts
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "exists": self.exists,
            "built": self.built,
            "source_files": self.source_files,
            "last_build": self.last_build,
            "stale": self.stale,
            "stale_files": self.stale_files,
            "error": self.error,
        }


def project_status(name: str, path: str) -> ProjectStatus:
    """Compute the staleness report for one project.

    When the project has a SQLite store, staleness is per-file against
    the recorded build stamps: one db read, a stat per source file, and
    a content hash ONLY on stat mismatch (the P1 fast path) — so a
    `touch` without an edit doesn't flag the project stale. Without a
    db, falls back to comparing the project.aleph.dict artifact mtime
    against source mtimes.
    """
    from datetime import datetime, timezone
    from aleph.project.discovery import discover_source_files
    from aleph.project.paths import resolve_artifact_dir, DICT_FILENAME

    status = ProjectStatus(name=name, path=path)
    if not os.path.isdir(path):
        status.error = "project directory not found"
        return status
    status.exists = True

    try:
        sources = discover_source_files(path)
    except Exception as e:
        status.error = f"source discovery failed: {e}"
        return status
    status.source_files = len(sources)

    artifact_dir = resolve_artifact_dir(path)
    if _db_status(status, artifact_dir, path, sources):
        return status

    dict_path = os.path.join(artifact_dir, DICT_FILENAME)
    if not os.path.isfile(dict_path):
        status.built = False
        status.stale = True
        status.stale_files = len(sources)
        return status

    status.built = True
    artifact_mtime = os.stat(dict_path).st_mtime
    status.last_build = datetime.fromtimestamp(
        artifact_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    stale_files = 0
    for src in sources:
        try:
            if os.stat(src).st_mtime > artifact_mtime:
                stale_files += 1
        except OSError:
            continue
    status.stale_files = stale_files
    status.stale = stale_files > 0
    return status


def _db_status(status: ProjectStatus, artifact_dir: str, root: str,
               sources: list[str]) -> bool:
    """Fill status from the SQLite store's per-file stamps.

    Returns True when the db was present and used.
    """
    from aleph.store.sqlite_store import open_store

    store = open_store(artifact_dir)
    if store is None:
        return False
    try:
        stamps = store.file_stamps(root)
        built_at = store.get_meta("built_at") or ""
    finally:
        store.close()

    status.built = True
    status.last_build = built_at

    stale_files = 0
    for src in sources:
        stamp = stamps.get(src)
        if stamp is None:
            stale_files += 1  # new file, not in the last build
        elif not stamp.matches_file(src):
            stale_files += 1  # stat mismatch AND content hash mismatch
    # Files that were built but no longer exist also mean stale artifacts
    stale_files += len(set(stamps) - set(sources))
    status.stale_files = stale_files
    status.stale = stale_files > 0
    return True


def workspace_status(projects: dict[str, str]) -> list[ProjectStatus]:
    """Per-project staleness report for a whole workspace."""
    return [project_status(name, path) for name, path in sorted(projects.items())]


def workspace_build(projects: dict[str, str], full: bool = False) -> list[dict]:
    """Build every project in the workspace, continuing past failures.

    Returns one report dict per project:
      {"name", "path", "success", "files", "symbols", "reduction_percent",
       "error"}
    """
    from aleph.pipeline import auto_build

    reports: list[dict] = []
    for name, path in sorted(projects.items()):
        report = {
            "name": name, "path": path, "success": False,
            "files": 0, "symbols": 0, "reduction_percent": 0.0, "error": "",
        }
        if not os.path.isdir(path):
            report["error"] = "project directory not found"
            reports.append(report)
            continue
        try:
            result = auto_build(path, full=full)
            stats = result.stats
            reduction = (
                (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
                if stats.total_original_tokens > 0 else 0.0
            )
            report.update({
                "success": True,
                "files": stats.total_files,
                "symbols": stats.total_symbols,
                "reduction_percent": round(reduction, 1),
            })
            if stats.errors:
                report["error"] = f"{len(stats.errors)} file error(s)"
        except Exception as e:
            report["error"] = str(e)
        reports.append(report)
    return reports


@dataclass
class WorkspaceEngine:
    """Wraps multiple QueryEngines for cross-project queries.

    Per-project failures (missing or corrupt artifacts) never silently
    drop a project: they are recorded in ``warnings`` so consumers can
    surface them alongside results.
    """

    projects: dict[str, str]
    _engines: dict[str, QueryEngine] = field(default_factory=dict, repr=False)
    # project name -> human-readable failure reason
    warnings: dict[str, str] = field(default_factory=dict)

    def _engine(self, name: str) -> QueryEngine | None:
        """Lazy-load engine for a project."""
        if name in self._engines:
            return self._engines[name]
        if name in self.warnings:
            return None
        path = self.projects.get(name)
        if not path:
            self.warnings[name] = "no path configured"
            return None
        try:
            self._engines[name] = QueryEngine(path)
        except Exception as e:
            self.warnings[name] = f"failed to open project: {e}"
            return None
        return self._engines[name]

    def _record_failure(self, name: str, exc: Exception) -> None:
        if isinstance(exc, FileNotFoundError):
            self.warnings[name] = (
                "no artifacts found (run `aleph build` or `aleph workspace build`)"
            )
        else:
            self.warnings[name] = f"corrupt or unreadable artifacts: {exc}"
        # Drop the cached engine so a rebuilt project is retried fresh
        self._engines.pop(name, None)

    def search(self, intent: str) -> list[SearchResult]:
        """Search across all projects, tagged by project name."""
        all_results: list[SearchResult] = []
        for name in self.projects:
            engine = self._engine(name)
            if not engine:
                continue
            try:
                results = engine.search(intent)
            except Exception as e:
                self._record_failure(name, e)
                continue
            for r in results:
                r.project = name
            all_results.extend(results)
        all_results.sort(key=lambda r: (-r.score, r.project, r.qualified_name))
        return all_results

    def resolve(self, symbol_id: str, project: str | None = None) -> ResolveResult | None:
        """Resolve a symbol, optionally scoped to a project."""
        targets = [project] if project else list(self.projects.keys())
        for name in targets:
            engine = self._engine(name)
            if not engine:
                continue
            try:
                result = engine.resolve(symbol_id)
            except Exception as e:
                self._record_failure(name, e)
                continue
            if result:
                result.project = name
                return result
        return None

    def callers(self, symbol_id: str, project: str | None = None) -> list[CallerEntry]:
        """Find callers across all projects (or scoped to one)."""
        all_callers: list[CallerEntry] = []
        targets = [project] if project else list(self.projects.keys())

        for name in targets:
            engine = self._engine(name)
            if not engine:
                continue
            try:
                results = engine.callers(symbol_id)
            except Exception as e:
                self._record_failure(name, e)
                continue
            for c in results:
                c.project = name
            all_callers.extend(results)
        return all_callers

    def status(self) -> list[ProjectStatus]:
        """Per-project staleness report."""
        return workspace_status(self.projects)

    @property
    def project_names(self) -> list[str]:
        return list(self.projects.keys())
