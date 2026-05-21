"""Handler functions for ALEPH: protocol commands.

Each handler reads from already-built .aleph/ component files on disk
and returns structured data suitable for MCP tool responses.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from aleph.emit.loader import AlephLoader
from aleph.query.engine import QueryEngine


@dataclass
class AlephHandlers:
    """Stateful handler set bound to a built .aleph project directory.

    Lazily loads component files as needed and caches them for the session.
    """

    project_dir: str
    agent_id: str = "default"
    _query_log: list[dict] = field(default_factory=list, repr=False)
    _engine: QueryEngine | None = field(default=None, repr=False)
    _loader: AlephLoader = field(default_factory=AlephLoader, repr=False)
    _artifact_dir: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self._artifact_dir = self._resolve_artifact_dir(self.project_dir)
        self._workspace = self._load_workspace()

    def _load_workspace(self):
        """Load workspace config if .aleph-workspace.json exists."""
        ws_path = os.path.join(self.project_dir, ".aleph-workspace.json")
        if not os.path.isfile(ws_path):
            env_path = os.environ.get("ALEPH_WORKSPACE", "")
            if env_path and os.path.isfile(env_path):
                ws_path = env_path
            else:
                return None
        try:
            ws_dir = os.path.dirname(os.path.abspath(ws_path))
            with open(ws_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            projects = config.get("projects", {})
            if not projects:
                return None
            # Resolve relative paths against workspace file location, not cwd
            resolved = {}
            for name, path in projects.items():
                if not os.path.isabs(path):
                    path = os.path.join(ws_dir, path)
                resolved[name] = os.path.abspath(path)
            from aleph.query.workspace import WorkspaceEngine
            return WorkspaceEngine(resolved)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _resolve_artifact_dir(project_dir: str) -> str:
        aleph_subdir = os.path.join(project_dir, ".aleph")
        if os.path.isdir(aleph_subdir) and os.path.isfile(
            os.path.join(aleph_subdir, "project.aleph.dict")
        ):
            return aleph_subdir
        return project_dir

    _NO_BUILD_MSG = "Error: no .aleph/ artifacts found. Run `aleph build .` first to generate project artifacts."

    def _log_query(self, operation: str, target: str) -> None:
        """Passively track tool queries — appends to disk immediately.

        Uses an append-only JSONL file so no data is lost on crash/Ctrl+C.
        """
        entry = {
            "op": operation,
            "target": target,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
        }
        self._query_log.append(entry)
        # Flush to disk immediately — don't rely on session_summary being called
        self._flush_query_entry(entry)

    def _query_log_path(self) -> str:
        return os.path.join(self._artifact_dir, ".aleph.query_log.jsonl")

    def _flush_query_entry(self, entry: dict) -> None:
        """Append a single query entry to the JSONL log file."""
        try:
            path = self._query_log_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass  # Non-critical — don't crash the tool call

    @property
    def engine(self) -> QueryEngine:
        if self._engine is None:
            self._engine = QueryEngine(self.project_dir)
        return self._engine

    def _require_engine(self) -> QueryEngine | None:
        """Return the engine if artifacts exist, or None."""
        try:
            return self.engine
        except Exception:
            return None

    def _read_artifact(self, filename: str) -> str | None:
        path = os.path.join(self._artifact_dir, filename)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # ── Navigation commands ──

    def handle_map(self, path_prefix: str | None = None) -> str:
        text = self._read_artifact("project.aleph.map")
        if text is None:
            return "Error: project.aleph.map not found. Run `aleph build` first."
        if path_prefix:
            # Filter to only files matching the prefix
            filtered = []
            for line in text.split("\n"):
                if line.startswith("[") or not line.strip():
                    filtered.append(line)
                elif line.startswith(path_prefix) or line.startswith(path_prefix.replace("/", "\\")):
                    filtered.append(line)
            return "\n".join(filtered)
        return text

    def handle_fs(self) -> str:
        text = self._read_artifact("project.aleph.fs")
        if text is None:
            return "Error: project.aleph.fs not found. Run `aleph build` first."
        return text

    def handle_struct(self, file: str | None = None) -> str:
        if file:
            # Try file-level struct
            basename = os.path.basename(file)
            text = self._read_artifact(f"{basename}.aleph.struct")
            if text:
                return text
            # Fall back: filter project struct to only this file's cross-refs
            text = self._read_artifact("project.aleph.struct")
            if text:
                filtered = [f"[NOTE: showing cross-refs involving {file} (filtered from project struct)]"]
                for line in text.split("\n"):
                    if f"src={file}" in line or f"dst={file}" in line:
                        filtered.append(line)
                    elif line.startswith("[") and ("XREFS" in line or "FILEDEPS" in line
                                                   or "STRUCT" in line or "ROOT" in line):
                        filtered.append(line)
                    elif line.startswith(f"{file}->") or line.endswith(f"->{file}") or f"->{file} " in line:
                        filtered.append(line)
                return "\n".join(filtered)
            return f"Error: no struct found for {file}."
        text = self._read_artifact("project.aleph.struct")
        if text is None:
            return "Error: project.aleph.struct not found. Run `aleph build` first."
        return text

    def handle_bodies(self, file: str) -> str:
        basename = os.path.basename(file)
        text = self._read_artifact(f"{basename}.aleph.bodies")
        if text is None:
            return f"Error: no bodies file found for {file}. Run `aleph build --per-file` first."
        return text

    def handle_errors(self, file: str) -> str:
        basename = os.path.basename(file)
        text = self._read_artifact(f"{basename}.aleph.errors")
        if text is None:
            return f"Error: no errors file found for {file}."
        return text

    def handle_intents(self, file: str) -> str:
        basename = os.path.basename(file)
        text = self._read_artifact(f"{basename}.aleph.intents")
        if text is None:
            return f"Error: no intents file found for {file}."
        return text

    def handle_tests(self, file: str) -> str:
        basename = os.path.basename(file)
        text = self._read_artifact(f"{basename}.aleph.tests")
        if text is None:
            return f"Error: no tests file found for {file}."
        return text

    def handle_coverage(self) -> str:
        text = self._read_artifact("project.aleph.coverage")
        if text is None:
            return "Error: project.aleph.coverage not found. Run `aleph build` first."
        return text

    # ── Resolution commands ──

    def handle_expand(self, symbol_id: str) -> str:
        self._log_query("expand", symbol_id)
        try:
            body = self.engine.expand(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if body is None:
            return (
                f"Error: no body found for {symbol_id}. "
                f"Per-file bodies require: aleph build <dir> --per-file. "
                f"Use ALEPH:RESOLVE {symbol_id} to check the symbol exists."
            )
        return body

    def handle_resolve(self, symbol_id: str) -> str:
        self._log_query("resolve", symbol_id)
        try:
            result = self.engine.resolve(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if result is None:
            return f"Error: symbol {symbol_id} not found in dictionary."
        return (
            f"ID:        {result.symbol_id}\n"
            f"Name:      {result.name}\n"
            f"Qualified: {result.qualified_name}\n"
            f"Kind:      {result.kind}\n"
            f"Scope:     {result.scope}\n"
            f"File:      {result.file}\n"
            f"Sig hash:  {result.signature_hash}"
        )

    def handle_callers(self, symbol_id: str) -> str:
        self._log_query("callers", symbol_id)
        try:
            results = self.engine.callers(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if not results:
            return f"No callers found for {symbol_id}."
        lines = [f"Callers of {symbol_id}: {len(results)}"]
        for c in results:
            lines.append(f"  {c.caller_id} {c.caller_name} ({c.caller_file})")
        return "\n".join(lines)

    def handle_context(self, symbol_id: str) -> str:
        self._log_query("context", symbol_id)
        try:
            result = self.engine.context(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if result is None:
            return f"Error: symbol {symbol_id} not found."
        lines = [f"Symbol: {result.symbol.symbol_id} {result.symbol.qualified_name} ({result.symbol.kind}) in {result.symbol.file}"]
        if result.callers:
            lines.append(f"Callers ({len(result.callers)}):")
            for c in result.callers:
                lines.append(f"  <- {c.caller_id} {c.caller_name}")
        if result.callees:
            lines.append(f"Callees ({len(result.callees)}):")
            for c in result.callees:
                lines.append(f"  -> {c.symbol_id} {c.qualified_name}")
        return "\n".join(lines)

    def handle_search(self, term: str) -> str:
        self._log_query("search", term)
        try:
            results = self.engine.search(term)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if not results:
            return f"No matches for '{term}'."
        lines = [f"Matches for '{term}': {len(results)}"]
        for r in results:
            lines.append(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")
        return "\n".join(lines)

    # ── Priority commands ──

    def handle_attention(self) -> str:
        text = self._read_artifact("project.aleph.attention")
        if text is None:
            return "Error: project.aleph.attention not found. Run `aleph build` first."
        return text

    def handle_salience(self, symbol_id: str | None = None) -> str:
        text = self._read_artifact("project.aleph.salience")
        if text is None:
            return "Error: project.aleph.salience not found. Run `aleph build` first."
        if symbol_id is None:
            return text
        # Filter to the requested symbol
        for line in text.splitlines():
            if line.startswith(symbol_id + " "):
                return line
        return f"No salience entry found for {symbol_id}."

    def handle_temporal(self, symbol_id: str | None = None) -> str:
        text = self._read_artifact("project.aleph.temporal")
        if text is None:
            return "Error: project.aleph.temporal not found. Run `aleph build` first."
        if symbol_id is None:
            return text
        # Filter to the requested symbol
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(symbol_id + " ") or stripped.startswith(symbol_id + "\t"):
                return stripped
        return f"No temporal entry found for {symbol_id}."

    # ── Epistemic commands ──

    def _epistemic_path(self) -> str:
        return os.path.join(self._artifact_dir, "project.aleph.epistemic")

    def _load_epistemic(self) -> dict:
        path = self._epistemic_path()
        if not os.path.isfile(path):
            return {"inferences": [], "flags": []}
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {"inferences": [], "flags": []}

    def _save_epistemic(self, data: dict) -> None:
        path = self._epistemic_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def handle_epistemic(self, symbol_id: str | None = None) -> str:
        data = self._load_epistemic()
        inferences = data.get("inferences", [])
        flags = data.get("flags", [])

        if symbol_id:
            inferences = [i for i in inferences if i.get("symbol_id") == symbol_id]
            flags = [f for f in flags if f.get("symbol_id") == symbol_id]

        # Apply confidence decay based on temporal data
        inferences = self._apply_confidence_decay(inferences)

        if not inferences and not flags:
            target = f" for {symbol_id}" if symbol_id else ""
            return f"No epistemic state{target}."

        lines = []
        if inferences:
            lines.extend(self._format_inferences(inferences))
        if flags:
            lines.append(f"Flags ({len(flags)}):")
            for fl in flags:
                verified = " [VERIFIED]" if fl.get("verified") else ""
                lines.append(f"  {fl['symbol_id']} {fl.get('reason', '')}{verified}")
        return "\n".join(lines)

    def _apply_confidence_decay(self, inferences: list[dict]) -> list[dict]:
        """Decay confidence based on inference age and symbol stability."""
        temporal = self._load_temporal_index()
        now = datetime.now(timezone.utc)
        decay_rates = {"volatile": 0.05, "active": 0.02, "stable": 0.005}
        for inf in inferences:
            created = inf.get("created_at")
            if not created:
                continue
            try:
                age_days = (now - datetime.fromisoformat(created)).days
            except (ValueError, TypeError):
                continue
            stability = temporal.get(inf["symbol_id"], "stable")
            rate = decay_rates.get(stability, 0.01)
            decay = math.exp(-rate * age_days)
            inf["confidence"] = round(inf["confidence"] * decay, 3)
        return inferences

    def _load_temporal_index(self) -> dict[str, str]:
        """Build symbol_id -> stability lookup from temporal artifact."""
        text = self._read_artifact("project.aleph.temporal")
        if not text:
            return {}
        index: dict[str, str] = {}
        for line in text.split("\n"):
            parts = line.split()
            if len(parts) >= 4 and parts[0][:2] in ("f_", "t_", "v_", "c_", "m_", "d_"):
                sid = parts[0]
                for part in parts:
                    if part.startswith("stability="):
                        index[sid] = part.split("=")[1]
        return index

    def _load_salience_index(self) -> dict[str, float]:
        """Build symbol_id -> salience score lookup."""
        text = self._read_artifact("project.aleph.salience")
        if not text:
            return {}
        index: dict[str, float] = {}
        for line in text.split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[0][:2] in ("f_", "t_", "v_", "c_", "m_", "d_"):
                for part in parts:
                    if part.startswith("score="):
                        try:
                            index[parts[0]] = float(part.split("=")[1])
                        except ValueError:
                            pass
        return index

    def _load_coverage_index(self) -> dict[str, tuple[str, int]]:
        """Build symbol_id -> (status, test_count) lookup from coverage artifact."""
        text = self._read_artifact("project.aleph.coverage")
        if not text:
            return {}
        index: dict[str, tuple[str, int]] = {}
        in_uncovered = False
        for line in text.split("\n"):
            if "[UNCOVERED]" == line.strip():
                in_uncovered = True
                continue
            if "[/UNCOVERED]" == line.strip():
                in_uncovered = False
                continue
            if in_uncovered and len(line) > 2 and line[:2] in ("f_", "t_", "v_", "c_", "m_"):
                sid = line.split()[0]
                index[sid] = ("none", 0)
        return index

    def handle_impact(self, symbol_id: str, max_hops: int = 2) -> str:
        """ALEPH:IMPACT — Pre-modification change impact analysis."""
        self._log_query("impact", symbol_id)
        try:
            engine = self.engine
        except FileNotFoundError:
            return self._NO_BUILD_MSG

        target = engine.resolve(symbol_id)
        if target is None:
            return f"Error: symbol {symbol_id} not found. Use ALEPH:SEARCH to find the correct ID."

        callers_by_distance = engine.transitive_callers(symbol_id, max_hops=max_hops)
        sal_index = self._load_salience_index()
        cov_index = self._load_coverage_index()
        temporal = self._load_temporal_index()
        idx = engine._build_symbol_index()

        direct = {sid: d for sid, d in callers_by_distance.items() if d == 1}
        transitive = {sid: d for sid, d in callers_by_distance.items() if d > 1}

        high_risk, covered, low_risk = [], [], []
        for sid in direct:
            entry = idx.get(sid)
            if not entry:
                continue
            sal = sal_index.get(sid, 0)
            cov_status, test_count = cov_index.get(sid, ("unknown", 0))
            info = {"id": sid, "name": entry.qualified_name, "file": entry.file,
                    "salience": sal, "coverage": cov_status, "tests": test_count}
            if cov_status == "covered" or test_count > 0:
                covered.append(info)
            elif sal >= 0.1:
                high_risk.append(info)
            else:
                low_risk.append(info)

        direct_files = {idx[sid].file for sid in direct if sid in idx}
        trans_files = {idx[sid].file for sid in transitive if sid in idx}
        target_sal = sal_index.get(symbol_id, 0)
        target_stab = temporal.get(symbol_id, "unknown")

        lines = [
            f"IMPACT ANALYSIS: {symbol_id} ({target.qualified_name})",
            f"File: {target.file} | Salience: {target_sal} | Stability: {target_stab}",
            "",
        ]

        lines.append(f"[DIRECT CALLERS] {len(direct)} across {len(direct_files)} files")
        if high_risk:
            high_risk.sort(key=lambda x: -x["salience"])
            lines.append(f"  HIGH RISK ({len(high_risk)} — high salience, no test coverage):")
            for h in high_risk[:10]:
                lines.append(f"    {h['id']} {h['name']}  file={h['file']}  salience={h['salience']}")
            if len(high_risk) > 10:
                lines.append(f"    ... {len(high_risk) - 10} more")
        if covered:
            covered.sort(key=lambda x: -x["salience"])
            lines.append(f"  COVERED ({len(covered)} — tests will catch regressions):")
            for c in covered[:5]:
                lines.append(f"    {c['id']} {c['name']}  tests={c['tests']}")
            if len(covered) > 5:
                lines.append(f"    ... {len(covered) - 5} more")
        if low_risk:
            lines.append(f"  LOW RISK ({len(low_risk)} — salience < 0.1)")

        if transitive:
            lines.append("")
            lines.append(f"[TRANSITIVE IMPACT] {len(transitive)} symbols across {len(trans_files)} files")
            trans_sorted = sorted(
                [(sid, d) for sid, d in transitive.items() if sid in idx],
                key=lambda x: -sal_index.get(x[0], 0),
            )
            lines.append("  Top by salience:")
            for sid, dist in trans_sorted[:5]:
                e = idx[sid]
                sal = sal_index.get(sid, 0)
                cov = cov_index.get(sid, ("unknown", 0))[0]
                lines.append(f"    {sid} {e.qualified_name}  salience={sal}  distance={dist}  coverage={cov}")

        tested = len(covered)
        total_untested = len(direct) - tested
        lines.append("")
        lines.append("[RISK SUMMARY]")
        lines.append(f"  Direct callers:          {len(direct)} ({tested} tested, {total_untested} untested)")
        lines.append(f"  Transitive reach:        {len(transitive)} symbols across {len(trans_files)} files")
        if high_risk:
            lines.append(f"  Untested high-salience:  {len(high_risk)} (DANGER)")
            suggested = [h["id"] for h in high_risk[:3]]
            lines.append(f"  Suggested test targets:  {', '.join(suggested)}")
        else:
            lines.append("  All high-salience callers are tested.")

        cov_status = cov_index.get(symbol_id, ("unknown", 0))
        if cov_status[0] == "none":
            lines.append("")
            lines.append("[WARNING] This symbol itself has NO test coverage.")

        return "\n".join(lines)

    def _format_inferences(self, inferences: list[dict]) -> list[str]:
        """Format inferences, grouping by agent when multiple agents exist."""
        agents = {inf.get("agent_id", "default") for inf in inferences}
        lines = []

        if len(agents) <= 1:
            lines.append(f"Inferences ({len(inferences)}):")
            for inf in inferences:
                lines.append(
                    f"  {inf['symbol_id']} [{inf.get('confidence', '?')}] "
                    f"{inf.get('conclusion', '')}"
                )
        else:
            # Multi-agent: group by symbol, show agent attribution
            by_symbol: dict[str, list[dict]] = defaultdict(list)
            for inf in inferences:
                by_symbol[inf["symbol_id"]].append(inf)

            lines.append(f"Inferences ({len(inferences)}, {len(agents)} agents):")
            for sid, infs in by_symbol.items():
                sid_agents = {i.get("agent_id", "default") for i in infs}
                if len(sid_agents) > 1:
                    lines.append(f"  {sid} [MULTI-AGENT]")
                    for inf in sorted(infs, key=lambda x: -x.get("confidence", 0)):
                        agent = inf.get("agent_id", "default")
                        lines.append(
                            f"    [{agent}] [{inf.get('confidence', '?')}] "
                            f"{inf.get('conclusion', '')}"
                        )
                else:
                    latest = max(infs, key=lambda x: x.get("created_at", ""))
                    lines.append(
                        f"  {sid} [{latest.get('confidence', '?')}] "
                        f"{latest.get('conclusion', '')}"
                    )
        return lines

    def handle_infer(self, symbol_id: str, conclusion: str, confidence: float) -> str:
        data = self._load_epistemic()
        entry = {
            "symbol_id": symbol_id,
            "conclusion": conclusion,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
        }
        data.setdefault("inferences", []).append(entry)
        self._save_epistemic(data)
        return f"Inference recorded for {symbol_id} at confidence {confidence}."

    def handle_flag(self, symbol_id: str, reason: str) -> str:
        data = self._load_epistemic()
        entry = {
            "symbol_id": symbol_id,
            "reason": reason,
            "verified": False,
        }
        data.setdefault("flags", []).append(entry)
        self._save_epistemic(data)
        return f"Flag recorded for {symbol_id}: {reason}"

    def handle_verify(self, symbol_id: str) -> str:
        data = self._load_epistemic()
        found = False
        for flag in data.get("flags", []):
            if flag.get("symbol_id") == symbol_id and not flag.get("verified"):
                flag["verified"] = True
                found = True
        if not found:
            return f"No unverified flags found for {symbol_id}."
        self._save_epistemic(data)
        return f"Flag(s) for {symbol_id} marked as verified."

    # ── Rebuild ──

    def handle_rebuild(self) -> str:
        """Force a full rebuild of all artifacts."""
        try:
            from aleph.cli import _auto_build
            result = _auto_build(self.project_dir, full=True)
            # Invalidate cached engine
            self._engine = None
            stats = result.stats
            reduction = (
                (1 - stats.total_compressed_tokens / stats.total_original_tokens) * 100
                if stats.total_original_tokens > 0 else 0.0
            )
            return (
                f"Rebuild complete.\n"
                f"Files: {stats.total_files}\n"
                f"Symbols: {stats.total_symbols}\n"
                f"Tokens: {stats.total_original_tokens} -> {stats.total_compressed_tokens}\n"
                f"Reduction: {reduction:.1f}%\n"
                f"Rebuilt: {stats.rebuilt_files} files"
            )
        except Exception as e:
            return f"Rebuild failed: {e}"

    # ── Workspace (cross-project) ──

    def handle_workspace_search(self, term: str) -> str:
        """Search across all workspace projects."""
        self._log_query("workspace_search", term)
        if not self._workspace:
            return "No workspace configured. Create .aleph-workspace.json with {\"projects\": {\"name\": \"/path\"}}."

        results = self._workspace.search(term)
        if not results:
            return f"No matches for '{term}' across workspace ({', '.join(self._workspace.project_names)})."

        lines = [f"Matches for '{term}' across {len(self._workspace.project_names)} projects: {len(results)}"]
        for r in results[:30]:
            lines.append(f"  [{r.project}] {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")
        if len(results) > 30:
            lines.append(f"  ... {len(results) - 30} more")
        return "\n".join(lines)

    def handle_workspace_brief(self, task: str) -> str:
        """Task-aware brief across all workspace projects."""
        self._log_query("workspace_brief", task)
        if not self._workspace:
            return "No workspace configured. Create .aleph-workspace.json with {\"projects\": {\"name\": \"/path\"}}."

        query = self._clean_task_query(task)
        results = self._workspace.search(query)
        if not results:
            results = self._workspace.search(task)
        if not results:
            return f"No symbols found matching '{task}' across workspace."

        # Take top 15, grouped by project
        top = results[:15]
        by_project: dict[str, list] = {}
        for r in top:
            by_project.setdefault(r.project, []).append(r)

        lines = [f"WORKSPACE BRIEF: {task}", ""]
        lines.append(f"[PROJECTS] {', '.join(self._workspace.project_names)}")
        lines.append("")

        for proj, syms in by_project.items():
            lines.append(f"[{proj}] ({len(syms)} matches)")
            for r in syms[:5]:
                lines.append(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f} file={r.file}")

        # Cross-project connections
        if len(by_project) > 1:
            lines.append("")
            lines.append("[CROSS-PROJECT CONNECTIONS]")
            # Find symbols with same name across projects
            names: dict[str, list[tuple[str, str]]] = {}
            for r in top:
                simple = r.qualified_name.split("::")[-1]
                names.setdefault(simple, []).append((r.project, r.symbol_id))
            shared = {n: projs for n, projs in names.items() if len(set(p for p, _ in projs)) > 1}
            if shared:
                for name, projs in list(shared.items())[:5]:
                    proj_list = ", ".join(f"{p}:{sid}" for p, sid in projs)
                    lines.append(f"  {name} → appears in: {proj_list}")
            else:
                lines.append("  No shared symbol names found in top results.")

        lines.append("")
        lines.append("[NEXT STEPS]")
        if top:
            lines.append(f"  1. ALEPH:RESOLVE {top[0].symbol_id} (in {top[0].project})")
            if len(by_project) > 1:
                lines.append(f"  2. Compare implementations across projects")

        return "\n".join(lines)

    # ── Task briefing ──

    _BRIEF_STOP_WORDS = frozenset({
        "fix", "add", "update", "change", "modify", "refactor", "implement",
        "debug", "investigate", "check", "review", "improve", "optimize",
        "the", "a", "an", "in", "on", "for", "to", "of", "and", "or",
        "is", "are", "was", "with", "from", "by", "at", "this", "that",
        "how", "why", "what", "where", "when", "does", "do", "can",
        "i", "we", "need", "want", "should", "would", "could",
        "new", "bug", "issue", "error", "broken", "work", "make",
    })

    def _clean_task_query(self, task: str) -> str:
        """Strip common action/stop words from a task description for search."""
        words = task.lower().split()
        keywords = [w for w in words if w not in self._BRIEF_STOP_WORDS and len(w) > 1]
        return " ".join(keywords) if keywords else task

    def handle_brief(self, task: str, max_symbols: int = 10) -> str:
        """ALEPH:BRIEF — Task-aware context optimizer."""
        self._log_query("brief", task)
        try:
            engine = self.engine
        except FileNotFoundError:
            return self._NO_BUILD_MSG

        # Strip action/stop words for better symbol matching
        query = self._clean_task_query(task)
        search_results = engine.search(query)

        # If cleaned query finds nothing, try the original
        if not search_results:
            search_results = engine.search(task)

        # Try individual words as fallback
        if not search_results:
            words = [w for w in task.lower().split() if w not in self._BRIEF_STOP_WORDS and len(w) > 2]
            for word in words:
                results = engine.search(word)
                if results:
                    search_results.extend(results)
            # Deduplicate
            seen = set()
            deduped = []
            for r in search_results:
                if r.symbol_id not in seen:
                    seen.add(r.symbol_id)
                    deduped.append(r)
            search_results = deduped

        if not search_results:
            return f"No symbols found matching '{task}'. Try different keywords."

        sal_index = self._load_salience_index()
        temporal = self._load_temporal_index()
        cov_index = self._load_coverage_index()
        idx = engine._build_symbol_index()

        # Rank by blended search relevance + salience
        ranked = []
        for r in search_results[:max_symbols * 3]:
            sal = sal_index.get(r.symbol_id, 0)
            combined = r.score * 0.5 + sal * 0.5
            ranked.append((combined, r))
        ranked.sort(key=lambda x: -x[0])
        top = [r for _, r in ranked[:max_symbols]]

        lines = [f"TASK BRIEF: {task}", ""]

        # Relevant symbols
        lines.append(f"[RELEVANT SYMBOLS] ({len(top)} of {len(search_results)} matches)")
        for r in top:
            sal = sal_index.get(r.symbol_id, 0)
            stab = temporal.get(r.symbol_id, "unknown")
            cov = cov_index.get(r.symbol_id, ("unknown", 0))[0]
            entry = idx.get(r.symbol_id)
            file = entry.file if entry else r.file
            lines.append(
                f"  {r.symbol_id} {r.qualified_name}  "
                f"salience={sal}  stability={stab}  coverage={cov}  "
                f"file={file}"
            )

        # Call context for top symbol
        if top:
            top_id = top[0].symbol_id
            lines.append("")
            lines.append(f"[CALL CONTEXT] ({top[0].qualified_name})")
            try:
                ctx = engine.context(top_id)
                if ctx and ctx.callers:
                    lines.append(f"  Callers ({len(ctx.callers)}):")
                    for c in ctx.callers[:5]:
                        lines.append(f"    <- {c.caller_name}")
                    if len(ctx.callers) > 5:
                        lines.append(f"    ... {len(ctx.callers) - 5} more")
                if ctx and ctx.callees:
                    lines.append(f"  Callees ({len(ctx.callees)}):")
                    for c in ctx.callees[:5]:
                        lines.append(f"    -> {c.qualified_name}")
            except FileNotFoundError:
                pass

            # Impact summary
            lines.append("")
            try:
                trans = engine.transitive_callers(top_id, max_hops=2)
                direct = {s: d for s, d in trans.items() if d == 1}
                high_risk = [
                    s for s in direct
                    if sal_index.get(s, 0) >= 0.1
                    and cov_index.get(s, ("none", 0))[0] in ("none", "unknown")
                ]
                lines.append(f"[IMPACT] {len(direct)} direct callers, {len(trans)} transitive")
                if high_risk:
                    lines.append(f"  HIGH RISK: {len(high_risk)} untested high-salience callers")
            except FileNotFoundError:
                pass

        # Temporal warnings
        volatile = [r for r in top if temporal.get(r.symbol_id, "stable") == "volatile"]
        if volatile:
            lines.append("")
            lines.append("[TEMPORAL WARNING]")
            for r in volatile:
                lines.append(f"  {r.qualified_name}: volatile — check carefully")

        # Prior epistemic knowledge
        data = self._load_epistemic()
        inferences = data.get("inferences", [])
        top_ids = {r.symbol_id for r in top}
        relevant = [inf for inf in inferences if inf.get("symbol_id") in top_ids]
        if relevant:
            lines.append("")
            lines.append("[PRIOR KNOWLEDGE]")
            for inf in relevant[:5]:
                lines.append(
                    f"  {inf['symbol_id']}: \"{inf['conclusion']}\" "
                    f"[{inf.get('confidence', '?')}]"
                )

        # Next steps
        lines.append("")
        lines.append("[NEXT STEPS]")
        if top:
            lines.append(f"  1. ALEPH:EXPAND {top[0].symbol_id} — likely modification target")
            if len(top) > 1:
                lines.append(f"  2. ALEPH:EXPAND {top[1].symbol_id} — related symbol")
            lines.append(f"  3. ALEPH:IMPACT {top[0].symbol_id} — full blast radius")

        return "\n".join(lines)

    # ── Session tracking ──

    def handle_session_summary(self) -> str:
        """Generate a summary of this session's queries and save a review trail."""
        if not self._query_log:
            return "No queries recorded this session."

        from collections import Counter
        ops = Counter(q["op"] for q in self._query_log)
        symbols = Counter(
            q["target"] for q in self._query_log
            if q["op"] in ("resolve", "expand", "callers", "context", "impact")
        )

        lines = [f"Session activity ({len(self._query_log)} queries):"]
        lines.append(f"  Operations: {dict(ops)}")
        if symbols:
            lines.append(f"  Symbols examined ({len(symbols)}):")
            for sid, count in symbols.most_common(20):
                lines.append(f"    {sid} ({count}x)")

        # Persist to epistemic layer
        data = self._load_epistemic()
        reviewed = data.setdefault("reviewed", [])
        reviewed.append({
            "session": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
            "symbols": dict(symbols.most_common(50)),
            "queries": len(self._query_log),
        })
        self._save_epistemic(data)

        lines.append(f"\nReview trail saved to epistemic layer ({len(symbols)} symbols).")
        return "\n".join(lines)

    # ── Memory resume ──

    def handle_memory_resume(self) -> str:
        """Generate and return the session resume briefing."""
        from aleph.memory.session_memory import resume_session_briefing

        briefing = resume_session_briefing(self.project_dir)
        if briefing is None:
            return "No prior session memory found."
        return briefing.to_prompt()

    # ── Patch commands ──

    def _patch_manager(self):
        from aleph.patch.manager import PatchManager
        return PatchManager(self.project_dir)

    def handle_patch_propose(self, symbol_id: str, intent: str, file: str | None = None) -> str:
        mgr = self._patch_manager()
        record = mgr.propose(symbol_id, intent, file=file)
        return (
            f"Patch {record.patch_id} created for {record.symbol_id}.\n"
            f"  Intent: {record.intent}\n"
            f"  File: {record.file or '(unknown)'}\n"
            f"  Semantic hash: {record.semantic_hash or '(none)'}"
        )

    # Keep old name for backward compat with existing MCP tool registration
    def handle_patch(self, symbol_id: str, patch_body: str) -> str:
        return self.handle_patch_propose(symbol_id, patch_body)

    def handle_patch_list(self) -> str:
        mgr = self._patch_manager()
        patches = mgr.list_patches()
        pending = [p for p in patches if p.status == "pending"]
        if not pending:
            return "No pending patches."
        lines = [f"Pending patches ({len(pending)}):"]
        for p in pending:
            lines.append(
                f"  {p.patch_id} {p.symbol_id} [{p.status}] "
                f"hash={p.semantic_hash or '?'} file={p.file or '?'}"
            )
            lines.append(f"    Intent: {p.intent}")
        return "\n".join(lines)

    def handle_patch_apply(self, patch_id: str, force: bool = False) -> str:
        mgr = self._patch_manager()
        result = mgr.apply(patch_id, force=force)
        return result.message

    def handle_patch_reject(self, patch_id: str) -> str:
        mgr = self._patch_manager()
        return mgr.reject(patch_id)
