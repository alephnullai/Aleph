"""Cross-project workspace engine — queries across multiple Aleph projects."""

from __future__ import annotations

from aleph.query.engine import QueryEngine, SearchResult, ResolveResult, CallerEntry


class WorkspaceEngine:
    """Wraps multiple QueryEngines for cross-project queries."""

    def __init__(self, projects: dict[str, str]) -> None:
        """Initialize with {project_name: project_dir} mapping."""
        self.projects = projects
        self._engines: dict[str, QueryEngine] = {}

    def _engine(self, name: str) -> QueryEngine | None:
        """Lazy-load engine for a project."""
        if name not in self._engines:
            path = self.projects.get(name)
            if not path:
                return None
            try:
                self._engines[name] = QueryEngine(path)
            except Exception:
                return None
        return self._engines[name]

    def search(self, intent: str) -> list[SearchResult]:
        """Search across all projects, tagged by project name."""
        all_results: list[SearchResult] = []
        for name in self.projects:
            engine = self._engine(name)
            if not engine:
                continue
            try:
                results = engine.search(intent)
                for r in results:
                    r.project = name
                all_results.extend(results)
            except FileNotFoundError:
                continue
        all_results.sort(key=lambda r: (-r.score, r.project, r.qualified_name))
        return all_results

    def resolve(self, symbol_id: str, project: str | None = None) -> ResolveResult | None:
        """Resolve a symbol, optionally scoped to a project."""
        if project:
            engine = self._engine(project)
            if engine:
                result = engine.resolve(symbol_id)
                if result:
                    result.project = project
                    return result
            return None

        for name in self.projects:
            engine = self._engine(name)
            if not engine:
                continue
            try:
                result = engine.resolve(symbol_id)
                if result:
                    result.project = name
                    return result
            except FileNotFoundError:
                continue
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
                for c in results:
                    c.project = name
                all_callers.extend(results)
            except FileNotFoundError:
                continue
        return all_callers

    @property
    def project_names(self) -> list[str]:
        return list(self.projects.keys())
