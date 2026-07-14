"""Handler functions for ALEPH: protocol commands.

Each handler reads from already-built .aleph/ component files on disk
and returns structured data suitable for MCP tool responses.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone


from aleph.emit.loader import AlephLoader
from aleph.epistemic.store import EpistemicStore
from aleph.project.paths import resolve_artifact_dir
from aleph.query.engine import QueryEngine, is_test_path as _is_test_path

# Hard ceiling for any single tool response. Live incident: an unbounded
# aleph_attention response (>16MB) force-disconnected the MCP client.
MAX_OUTPUT_BYTES = 100_000


def _cap_output(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    """Final shared guard: cap a tool response at max_bytes.

    Truncates at a line boundary and appends a marker telling the model
    how to scope the query instead.
    """
    if not isinstance(text, str):
        return text
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    truncated = raw[:max_bytes].decode("utf-8", errors="ignore")
    cut = truncated.rfind("\n")
    if cut > 0:
        truncated = truncated[:cut]
    return (
        truncated
        + f"\n[OUTPUT TRUNCATED at {max_bytes // 1000}KB — "
        f"use limit/path_prefix/offset params to scope the query]"
    )


def _is_entry_line(line: str) -> bool:
    """True for artifact data lines (headers/sections start with '[')."""
    return bool(line.strip()) and not line.startswith("[")


def _cap_artifact_lines(text: str, limit: int, what: str = "entries") -> str:
    """Cap an artifact's data lines at `limit`, keeping header/section lines.

    Appends a truncation note when entries are dropped. limit <= 0 disables
    the cap (the final _cap_output byte guard still applies).
    """
    if limit is None or limit <= 0:
        return text
    lines = text.split("\n")
    total = sum(1 for line in lines if _is_entry_line(line))
    if total <= limit:
        return text
    out: list[str] = []
    shown = 0
    for line in lines:
        if _is_entry_line(line):
            if shown >= limit:
                continue
            shown += 1
            out.append(line)
            if shown == limit:
                out.append(
                    f"[TRUNCATED: showing {limit} of {total} {what} — "
                    f"raise limit to see more]"
                )
        else:
            out.append(line)
    return "\n".join(out)


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
        from aleph.query.workspace import (
            WorkspaceEngine, find_workspace_file, load_workspace_projects,
        )
        self._workspace_error = ""
        ws_path = find_workspace_file(self.project_dir)
        if ws_path is None:
            return None
        try:
            projects = load_workspace_projects(ws_path)
        except ValueError as e:
            self._workspace_error = str(e)
            return None
        return WorkspaceEngine(projects)

    _NO_WORKSPACE_MSG = (
        "No workspace configured. Create .aleph-workspace.json with "
        "{\"projects\": {\"name\": \"/path\"}} and run `aleph workspace build`."
    )

    def _no_workspace_msg(self) -> str:
        if getattr(self, "_workspace_error", ""):
            return f"Workspace config error: {self._workspace_error}"
        return self._NO_WORKSPACE_MSG

    def _workspace_warning_lines(self) -> list[str]:
        """Per-project warnings (missing/corrupt artifacts) — never silent."""
        if not self._workspace or not self._workspace.warnings:
            return []
        return [
            f"  [WARNING] {name}: {reason}"
            for name, reason in sorted(self._workspace.warnings.items())
        ]

    # Single source of truth: aleph.project.paths.resolve_artifact_dir
    _resolve_artifact_dir = staticmethod(resolve_artifact_dir)

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

    # When no path_prefix is given and the project exceeds this many files,
    # handle_map returns a directory-level rollup instead of every file.
    _MAP_ROLLUP_THRESHOLD = 500

    def handle_map(self, path_prefix: str | None = None, limit: int = 200) -> str:
        text = self._read_artifact("project.aleph.map")
        if text is None:
            return "Error: project.aleph.map not found. Run `aleph build` first."
        lines = text.split("\n")

        if path_prefix:
            alt_prefix = path_prefix.replace("/", "\\")

            def matches(line: str) -> bool:
                return line.startswith(path_prefix) or line.startswith(alt_prefix)
        else:
            def matches(line: str) -> bool:
                return True

        file_lines = [l for l in lines if _is_entry_line(l) and matches(l)]

        # Large project, no prefix: roll up to one line per top-level dir
        if not path_prefix and len(file_lines) > self._MAP_ROLLUP_THRESHOLD:
            return self._map_directory_rollup(file_lines)

        out: list[str] = []
        shown = 0
        for line in lines:
            if _is_entry_line(line):
                if not matches(line):
                    continue
                if limit and limit > 0 and shown >= limit:
                    continue
                shown += 1
                out.append(line)
                if limit and shown == limit and len(file_lines) > limit:
                    out.append(
                        f"[TRUNCATED: showing {limit} of {len(file_lines)} files — "
                        f"refine with path_prefix or raise limit]"
                    )
            else:
                out.append(line)
        return "\n".join(out)

    @staticmethod
    def _map_directory_rollup(file_lines: list[str]) -> str:
        """Aggregate map entries into one line per top-level directory."""
        # dir -> [file_count, original_tokens, compressed_tokens, symbols]
        stats: dict[str, list[int]] = {}
        for line in file_lines:
            parts = line.split()
            if not parts:
                continue
            path = parts[0].replace("\\", "/")
            top = path.split("/")[0] if "/" in path else "."
            entry = stats.setdefault(top, [0, 0, 0, 0])
            entry[0] += 1
            for part in parts[1:]:
                if part.startswith("tokens="):
                    try:
                        orig, comp = part[len("tokens="):].split("->")
                        entry[1] += int(orig)
                        entry[2] += int(comp)
                    except ValueError:
                        pass
                elif part.startswith("syms="):
                    try:
                        entry[3] += int(part.split("=", 1)[1])
                    except ValueError:
                        pass
        lines = [
            "[ALEPH:MAP:1.0]",
            f"[ROLLUP: {len(file_lines)} files across {len(stats)} top-level "
            f"directories — pass path_prefix to drill into a directory]",
        ]
        for top in sorted(stats):
            count, orig, comp, syms = stats[top]
            label = top if top == "." else f"{top}/"
            lines.append(
                f"{label} files={count} syms={syms} tokens={orig}->{comp}"
            )
        return "\n".join(lines)

    def handle_fs(self, limit: int = 100) -> str:
        text = self._read_artifact("project.aleph.fs")
        if text is None:
            return "Error: project.aleph.fs not found. Run `aleph build` first."
        return _cap_artifact_lines(text, limit, "files/deps")

    def handle_struct(self, file: str | None = None, limit: int = 100) -> str:
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
                return _cap_artifact_lines("\n".join(filtered), limit, "cross-refs")
            return f"Error: no struct found for {file}."
        text = self._read_artifact("project.aleph.struct")
        if text is None:
            return "Error: project.aleph.struct not found. Run `aleph build` first."
        return _cap_artifact_lines(text, limit, "cross-refs/deps")

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

    def handle_coverage(self, limit: int = 100) -> str:
        text = self._read_artifact("project.aleph.coverage")
        if text is None:
            return "Error: project.aleph.coverage not found. Run `aleph build` first."
        return _cap_artifact_lines(text, limit, "coverage entries")

    # ── Resolution commands ──

    def _resolve_ref_or_message(self, symbol_id: str):
        """Resolve an id-or-name ref for a symbol-taking handler.

        Returns either (resolved_id: str, note: str) on success, or
        (None, message: str) when the caller should return ``message``
        verbatim. This is the MCP half of the trust contract: a NAME
        auto-resolves (note echoes 'resolved <name> -> <id>'), ambiguity
        returns the candidate list, and a true miss returns a distinct
        'no symbol named X' — never a downstream silent-empty dressed up
        as a real 'no callers' answer.
        """
        ref = self.engine.resolve_ref(symbol_id)
        if ref.status in ("id", "resolved"):
            return ref.symbol_id, (f"[{ref.note}]\n" if ref.note else "")
        if ref.status == "ambiguous":
            lines = [
                f"Ambiguous: '{symbol_id}' matches {len(ref.candidates)} "
                f"symbols — pass an id:"
            ]
            for c in ref.candidates[:self._LIST_CAP]:
                lines.append(f"  {c.symbol_id} {c.qualified_name} ({c.file})")
            return None, "\n".join(lines)
        # not_found
        return None, (
            f"No symbol named '{symbol_id}' (not an id and not a known name). "
            f"Use ALEPH:SEARCH {symbol_id} to find the correct id."
        )

    def handle_expand(self, symbol_id: str) -> str:
        self._log_query("expand", symbol_id)
        try:
            resolved, note = self._resolve_ref_or_message(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if resolved is None:
            return note  # ambiguous / not-found message
        body = self.engine.expand(resolved)
        if body is None:
            # resolved is a confirmed-real symbol: genuinely no body recorded.
            return (
                f"{note}No body recorded for {resolved} "
                f"(per-file bodies require: aleph build <dir> --per-file)."
            )
        return f"{note}{body}" if note else body

    def handle_resolve(self, symbol_id: str) -> str:
        self._log_query("resolve", symbol_id)
        try:
            ref = self.engine.resolve_ref(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if ref.status == "ambiguous":
            lines = [
                f"Ambiguous: '{symbol_id}' matches {len(ref.candidates)} "
                f"symbols — pass an id:"
            ]
            for c in ref.candidates[:self._LIST_CAP]:
                lines.append(f"  {c.symbol_id} {c.qualified_name} ({c.file})")
            return "\n".join(lines)
        if ref.status == "not_found":
            return (
                f"No symbol named '{symbol_id}'. "
                f"Use ALEPH:SEARCH {symbol_id} to find it."
            )
        result = ref.entry
        prefix = f"[{ref.note}]\n" if ref.note else ""
        return (
            f"{prefix}"
            f"ID:        {result.symbol_id}\n"
            f"Name:      {result.name}\n"
            f"Qualified: {result.qualified_name}\n"
            f"Kind:      {result.kind}\n"
            f"Scope:     {result.scope}\n"
            f"File:      {result.file}\n"
            f"Sig hash:  {result.signature_hash}"
        )

    # Max entries per list in callers/context responses.
    _LIST_CAP = 50

    def handle_callers(self, symbol_id: str) -> str:
        self._log_query("callers", symbol_id)
        try:
            resolved, note = self._resolve_ref_or_message(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if resolved is None:
            return note
        results = self.engine.callers(resolved)
        if not results:
            # resolved is confirmed-real: genuinely zero callers.
            return (
                f"{note}{resolved} has no callers "
                f"(0 — confirmed against the resolved symbol)."
            )
        lines = [f"{note}Callers of {resolved}: {len(results)}"]
        for c in results[:self._LIST_CAP]:
            lines.append(f"  {c.caller_id} {c.caller_name} ({c.caller_file})")
        if len(results) > self._LIST_CAP:
            lines.append(
                f"  [TRUNCATED: showing {self._LIST_CAP} of {len(results)} callers]"
            )
        return "\n".join(lines)

    def handle_context(self, symbol_id: str) -> str:
        self._log_query("context", symbol_id)
        try:
            resolved, note = self._resolve_ref_or_message(symbol_id)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if resolved is None:
            return note
        result = self.engine.context(resolved)
        if result is None:
            return f"Error: symbol {resolved} not found."
        header = f"{note}Symbol: {result.symbol.symbol_id} {result.symbol.qualified_name} ({result.symbol.kind}) in {result.symbol.file}"
        lines = [header]
        if result.callers:
            lines.append(f"Callers ({len(result.callers)}):")
            for c in result.callers[:self._LIST_CAP]:
                lines.append(f"  <- {c.caller_id} {c.caller_name}")
            if len(result.callers) > self._LIST_CAP:
                lines.append(
                    f"  [TRUNCATED: showing {self._LIST_CAP} of {len(result.callers)} callers]"
                )
        if result.callees:
            lines.append(f"Callees ({len(result.callees)}):")
            for c in result.callees[:self._LIST_CAP]:
                lines.append(f"  -> {c.symbol_id} {c.qualified_name}")
            if len(result.callees) > self._LIST_CAP:
                lines.append(
                    f"  [TRUNCATED: showing {self._LIST_CAP} of {len(result.callees)} callees]"
                )
        return "\n".join(lines)

    def handle_search(self, term: str, limit: int = 25) -> str:
        self._log_query("search", term)
        try:
            results = self.engine.search(term)
        except FileNotFoundError:
            return self._NO_BUILD_MSG
        if not results:
            # Never dead-end: surface nearest candidates or actionable guidance.
            nearest = self.engine.search_nearest(term)
            if nearest:
                lines = [f"No direct match for '{term}'. Nearest:"]
                for r in nearest:
                    lines.append(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) file={r.file}")
                lines.append(
                    "(weak matches — refine the term, or use ALEPH:MAP / "
                    "ALEPH:STRUCT <file> to explore structure)"
                )
                return "\n".join(lines)
            return (
                f"No match for '{term}'. Try a symbol name or file path; "
                f"for free-text content search, grep may fit better."
            )
        lines = [f"Matches for '{term}': {len(results)}"]
        shown = results[:limit] if limit and limit > 0 else results
        for r in shown:
            lines.append(f"  {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")
        if len(results) > len(shown):
            lines.append(f"  [{len(results) - len(shown)} more matches — refine query]")
        return "\n".join(lines)

    # ── Priority commands ──

    def handle_attention(self, limit: int = 100) -> str:
        text = self._read_artifact("project.aleph.attention")
        if text is None:
            return "Error: project.aleph.attention not found. Run `aleph build` first."
        return _cap_artifact_lines(text, limit, "entries")

    def handle_salience(self, symbol_id: str | None = None, limit: int = 100) -> str:
        text = self._read_artifact("project.aleph.salience")
        if text is None:
            return "Error: project.aleph.salience not found. Run `aleph build` first."
        if symbol_id is None:
            return _cap_artifact_lines(text, limit, "scores")
        # Filter to the requested symbol
        for line in text.splitlines():
            if line.startswith(symbol_id + " "):
                return line
        return f"No salience entry found for {symbol_id}."

    def handle_temporal(self, symbol_id: str | None = None, limit: int = 100) -> str:
        text = self._read_artifact("project.aleph.temporal")
        if text is None:
            return "Error: project.aleph.temporal not found. Run `aleph build` first."
        if symbol_id is None:
            return _cap_artifact_lines(text, limit, "entries")
        # Filter to the requested symbol
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(symbol_id + " ") or stripped.startswith(symbol_id + "\t"):
                return stripped
        return f"No temporal entry found for {symbol_id}."

    # ── Epistemic commands ──

    def _epistemic_path(self) -> str:
        return os.path.join(self._artifact_dir, "project.aleph.epistemic")

    def _epistemic_store(self) -> EpistemicStore:
        return EpistemicStore(self._epistemic_path())

    def _load_epistemic(self) -> dict:
        data = self._epistemic_store().load()
        data.setdefault("inferences", [])
        data.setdefault("flags", [])
        return data

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
        """Build symbol_id -> (status, test_count) lookup from coverage artifact.

        Parses both the [COVERED] section (status "covered" with test counts)
        and the [UNCOVERED] section (status "none").
        """
        text = self._read_artifact("project.aleph.coverage")
        if not text:
            return {}
        index: dict[str, tuple[str, int]] = {}
        section: str | None = None
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped == "[UNCOVERED]":
                section = "none"
                continue
            if stripped == "[COVERED]":
                section = "covered"
                continue
            if stripped in ("[/UNCOVERED]", "[/COVERED]"):
                section = None
                continue
            if section and len(line) > 2 and line[:2] in ("f_", "t_", "v_", "c_", "m_"):
                parts = line.split()
                sid = parts[0]
                test_count = 0
                if section == "covered":
                    for part in parts:
                        if part.startswith("tests="):
                            try:
                                test_count = int(part.split("=", 1)[1])
                            except ValueError:
                                pass
                index[sid] = (section, test_count)
        return index

    def handle_impact(self, symbol_id: str, max_hops: int = 2) -> str:
        """ALEPH:IMPACT — Pre-modification change impact analysis."""
        self._log_query("impact", symbol_id)
        try:
            engine = self.engine
        except FileNotFoundError:
            return self._NO_BUILD_MSG

        resolved, note = self._resolve_ref_or_message(symbol_id)
        if resolved is None:
            return note  # ambiguous / not-found message
        symbol_id = resolved
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

        lines = []
        if note:
            lines.append(note.rstrip("\n"))
        lines += [
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
        entry = {
            "symbol_id": symbol_id,
            "conclusion": conclusion,
            "confidence": confidence,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_id": self.agent_id,
        }
        with self._epistemic_store().transaction() as data:
            data.setdefault("inferences", []).append(entry)
        return f"Inference recorded for {symbol_id} at confidence {confidence}."

    def handle_flag(self, symbol_id: str, reason: str) -> str:
        entry = {
            "symbol_id": symbol_id,
            "reason": reason,
            "verified": False,
        }
        with self._epistemic_store().transaction() as data:
            data.setdefault("flags", []).append(entry)
        return f"Flag recorded for {symbol_id}: {reason}"

    def handle_verify(self, symbol_id: str) -> str:
        found = False
        with self._epistemic_store().transaction() as data:
            for flag in data.get("flags", []):
                if flag.get("symbol_id") == symbol_id and not flag.get("verified"):
                    flag["verified"] = True
                    found = True
        if not found:
            return f"No unverified flags found for {symbol_id}."
        return f"Flag(s) for {symbol_id} marked as verified."

    # ── Rebuild ──

    def handle_rebuild(self) -> str:
        """Force a full rebuild of all artifacts."""
        try:
            from aleph.pipeline import auto_build
            result = auto_build(self.project_dir, full=True)
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

    def handle_workspace_search(self, term: str, limit: int = 25) -> str:
        """Search across all workspace projects."""
        self._log_query("workspace_search", term)
        if not self._workspace:
            return self._no_workspace_msg()

        results = self._workspace.search(term)
        warning_lines = self._workspace_warning_lines()
        if not results:
            return "\n".join(
                [f"No matches for '{term}' across workspace ({', '.join(self._workspace.project_names)})."]
                + warning_lines
            )

        lines = [f"Matches for '{term}' across {len(self._workspace.project_names)} projects: {len(results)}"]
        shown = results[:limit] if limit and limit > 0 else results
        for r in shown:
            lines.append(f"  [{r.project}] {r.symbol_id} {r.qualified_name} ({r.kind}) score={r.score:.3f}")
        if len(results) > len(shown):
            lines.append(f"  [{len(results) - len(shown)} more matches — refine query or raise limit]")
        lines.extend(warning_lines)
        return "\n".join(lines)

    def handle_workspace_status(self) -> str:
        """Per-project build/staleness report for the workspace."""
        self._log_query("workspace_status", "")
        if not self._workspace:
            return self._no_workspace_msg()

        statuses = self._workspace.status()
        lines = [f"WORKSPACE STATUS ({len(statuses)} projects)"]
        for s in statuses:
            if s.error:
                state = f"ERROR ({s.error})"
            elif not s.built:
                state = "NOT BUILT"
            elif s.stale:
                state = f"STALE ({s.stale_files} source files newer than artifacts)"
            else:
                state = "fresh"
            lines.append(f"  [{s.name}] {state}")
            lines.append(f"    path={s.path} files={s.source_files}"
                         + (f" last_build={s.last_build}" if s.last_build else ""))
        if any(s.stale or not s.built for s in statuses if not s.error):
            lines.append("")
            lines.append("Run `aleph workspace build` to refresh stale projects.")
        lines.extend(self._workspace_warning_lines())
        return "\n".join(lines)

    def handle_workspace_brief(self, task: str) -> str:
        """Task-aware brief across all workspace projects."""
        self._log_query("workspace_brief", task)
        if not self._workspace:
            return self._no_workspace_msg()

        query = self._clean_task_query(task)
        results = self._workspace.search(query)
        if not results:
            results = self._workspace.search(task)
        if not results:
            return "\n".join(
                [f"No symbols found matching '{task}' across workspace."]
                + self._workspace_warning_lines()
            )

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

        lines.extend(self._workspace_warning_lines())
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

    # Minimum lexical relevance for a symbol to appear in a brief.
    # Results below this are noise — better to say "no confident match"
    # than to pad the briefing with junk.
    _BRIEF_RELEVANCE_FLOOR = 0.35
    # A weak substring/path match additionally needs either this score or
    # some structural salience (fan-in) to be worth briefing.
    _BRIEF_WEAK_MATCH_SCORE = 0.6

    def _no_confident_match_msg(self, task: str, query: str) -> str:
        return (
            f"No symbols matched '{task}' with enough confidence to brief.\n"
            f"(Searched for: '{query}')\n"
            "Suggestions:\n"
            "  - Use specific identifier terms (function/class/module names)\n"
            "  - Try aleph_search <term> to probe individual keywords\n"
            "  - Use aleph_map / aleph_struct <file> to explore structure directly"
        )

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
            return self._no_confident_match_msg(task, query or task)

        sal_index = self._load_salience_index()
        temporal = self._load_temporal_index()
        cov_index = self._load_coverage_index()
        idx = engine._build_symbol_index()

        # Relevance floor: drop weak matches instead of padding the brief.
        confident = []
        for r in search_results:
            # Import directives are not actionable briefing targets: their
            # qualified names are whole import statements, so they token-match
            # almost any task mentioning module members (and multi-line import
            # names corrupt the line-oriented brief output).
            if r.kind == "d":
                continue
            if r.score < self._BRIEF_RELEVANCE_FLOOR:
                continue
            # Penalize results whose ONLY signal is a name-substring or
            # path-component match and that nothing in the project calls.
            weak_match = getattr(r, "match", "") in ("substring", "path")
            if (weak_match and sal_index.get(r.symbol_id, 0) <= 0
                    and r.score < self._BRIEF_WEAK_MATCH_SCORE):
                continue
            confident.append(r)

        if not confident:
            return self._no_confident_match_msg(task, query or task)

        # Rank by blended search relevance + salience (no padding: only
        # symbols that cleared the relevance floor are eligible).
        # Blend over the FULL confident pool: slicing in lexical order here
        # would starve the salience blend — a high-salience implementation
        # symbol just below the lexical cut could never outrank a wall of
        # zero-salience name-echo matches.
        # Test-file symbols are discounted: brief's contract is "likely
        # modification target", and test names tend to echo task vocabulary
        # verbatim ("rejects path outside project") while the implementation
        # uses its own terms (_contained_path) — without the discount the
        # tests drown the code they test. Skipped when the task itself is
        # about tests.
        task_is_about_tests = bool(
            re.search(r"\b(tests?|fixtures?|coverage|pytest)\b", task, re.I)
        )
        ranked = []
        for r in confident:
            sal = sal_index.get(r.symbol_id, 0)
            combined = r.score * 0.5 + sal * 0.5
            if not task_is_about_tests and _is_test_path(r.file):
                combined *= 0.5
            ranked.append((combined, r))
        ranked.sort(key=lambda x: -x[0])
        top = [r for _, r in ranked[:max_symbols]]
        search_results = confident

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

        # Next steps — only recommend EXPAND when bodies actually exist
        lines.append("")
        lines.append("[NEXT STEPS]")
        if top:
            step = 1
            if engine.has_body(top[0].symbol_id):
                lines.append(f"  {step}. ALEPH:EXPAND {top[0].symbol_id} — likely modification target")
                step += 1
                if len(top) > 1 and engine.has_body(top[1].symbol_id):
                    lines.append(f"  {step}. ALEPH:EXPAND {top[1].symbol_id} — related symbol")
                    step += 1
            else:
                entry = idx.get(top[0].symbol_id)
                file_hint = entry.file if entry else top[0].file
                lines.append(
                    f"  {step}. ALEPH:CONTEXT {top[0].symbol_id} — call neighborhood "
                    f"(no per-file bodies; build with --per-file to enable EXPAND)"
                )
                step += 1
                if file_hint:
                    lines.append(f"  {step}. ALEPH:STRUCT {file_hint} — file architecture")
                    step += 1
            lines.append(f"  {step}. ALEPH:IMPACT {top[0].symbol_id} — full blast radius")

        # One-line note when the optional semantic index can't be used
        # (the search above was lexical-only for natural-language tasks).
        try:
            status = engine.semantic_status()
        except Exception:
            status = "ok"
        if status == "no-index":
            lines.append("")
            lines.append(
                "(semantic search unavailable: index not built — "
                "run `aleph build --semantic` to enable it)"
            )
        elif status == "no-dependency":
            lines.append("")
            lines.append(
                "(semantic search unavailable: install the optional extra — "
                "pip install 'aleph-compiler[semantic]')"
            )

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
        with self._epistemic_store().transaction() as data:
            reviewed = data.setdefault("reviewed", [])
            reviewed.append({
                "session": datetime.now(timezone.utc).isoformat(),
                "agent_id": self.agent_id,
                "symbols": dict(symbols.most_common(50)),
                "queries": len(self._query_log),
            })

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
        try:
            record = mgr.propose(symbol_id, intent, file=file)
        except ValueError as e:
            return f"Error: {e}"
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
