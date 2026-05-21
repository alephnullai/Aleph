"""Load and expand Aleph emitted artifacts."""

from __future__ import annotations

import json

from aleph.model.components import (
    BodiesComponent, BodyEntry,
    TemporalComponent, TemporalEntry,
    IntentsComponent, IntentEntry,
    ErrorsComponent, ErrorSource, ErrorBoundary, UnhandledError,
    TestsComponent, CoverageEntry, TestDetail,
    ProjectMapComponent, ProjectFileEntry,
    ProjectDictComponent, ProjectSymbolEntry,
    ProjectFSComponent, ProjectFSEntry, ProjectModuleDep,
    ProjectStructComponent, ProjectCrossRef, ProjectFileDep,
    ProjectSalienceComponent, ProjectSalienceEntry,
    ProjectAttentionComponent, ProjectAttentionEntry,
)
from aleph.model.enums import AttentionLevel
from aleph.model.enums import BodyLevel
from aleph.model.symbol import SymbolID


class AlephLoader:
    """Deserialize .aleph artifacts and provide expansion helpers."""

    def deserialize_bodies(self, text: str) -> BodiesComponent:
        source_file = ""
        symbol_dict: dict[str, str] = {}
        entries: list[BodyEntry] = []
        lines = text.splitlines()

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[SOURCE:") and line.endswith("]"):
                source_file = line[len("[SOURCE:"):-1]
            elif line == "[DICT]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/DICT]":
                    item = lines[i].strip()
                    if "=" in item:
                        sid, qname = item.split("=", 1)
                        symbol_dict[sid] = qname
                    i += 1
            elif line.startswith("[OMIT:") and line.endswith("]"):
                sid = line[len("[OMIT:"):-1]
                entries.append(
                    BodyEntry(symbol_id=SymbolID.from_string(sid), level=BodyLevel.OMIT, content="")
                )
            elif self._is_body_start(line):
                level, sid = self._parse_body_tag(line)
                closing = f"[/{level}:{sid}]"
                i += 1
                content_lines: list[str] = []
                while i < len(lines) and lines[i].strip() != closing:
                    content_lines.append(lines[i])
                    i += 1
                entries.append(
                    BodyEntry(
                        symbol_id=SymbolID.from_string(sid),
                        level=BodyLevel[level],
                        content="\n".join(content_lines),
                    )
                )
            elif line.startswith("[ORIGINAL:") and line.endswith("]"):
                sid = line[len("[ORIGINAL:"):-1]
                closing = f"[/ORIGINAL:{sid}]"
                i += 1
                original_lines: list[str] = []
                while i < len(lines) and lines[i].strip() != closing:
                    original_lines.append(lines[i])
                    i += 1
                original_body = "\n".join(original_lines)
                for entry in reversed(entries):
                    if str(entry.symbol_id) == sid:
                        entry.original_body = original_body
                        break
            i += 1

        return BodiesComponent(source_file=source_file, entries=entries, symbol_dict=symbol_dict)

    def deserialize_bundle_json(self, text: str) -> dict:
        return json.loads(text)

    def expand_entry(self, entry: BodyEntry, symbol_dict: dict[str, str]) -> str:
        # Prefer exact preserved body when present.
        if entry.original_body:
            return entry.original_body
        if entry.level != BodyLevel.FULL or not entry.content:
            return ""

        expanded = entry.content
        for sid, qname in sorted(symbol_dict.items(), key=lambda item: len(item[1]), reverse=True):
            expanded = expanded.replace(sid, qname)
        return expanded

    def expand_bodies(self, component: BodiesComponent) -> dict[str, str]:
        return {
            str(entry.symbol_id): self.expand_entry(entry, component.symbol_dict)
            for entry in component.entries
        }

    def _is_body_start(self, line: str) -> bool:
        for level in ("FULL", "SUMMARY"):
            if line.startswith(f"[{level}:") and line.endswith("]"):
                return True
        return False

    def _parse_body_tag(self, line: str) -> tuple[str, str]:
        # format: [FULL:f_abcd12]
        payload = line[1:-1]
        level, sid = payload.split(":", 1)
        return level, sid

    def deserialize_temporal(self, text: str) -> TemporalComponent:
        """Parse .aleph.temporal format."""
        source_file = ""
        computed_date = ""
        entries: list[TemporalEntry] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[SOURCE:") and line.endswith("]"):
                source_file = line[len("[SOURCE:"):-1]
            elif line.startswith("[DATE:") and line.endswith("]"):
                computed_date = line[len("[DATE:"):-1]
            elif line == "[ENTRIES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/ENTRIES]":
                    parts = lines[i].strip().split()
                    if len(parts) >= 5:
                        sid = SymbolID.from_string(parts[0])
                        attrs = {}
                        for p in parts[1:]:
                            if "=" in p:
                                k, v = p.split("=", 1)
                                attrs[k] = v
                        entries.append(TemporalEntry(
                            symbol_id=sid,
                            age_days=int(attrs.get("age", "0")),
                            last_modified_days=int(attrs.get("modified", "0")),
                            churn_count=int(attrs.get("churn", "0")),
                            stability=attrs.get("stability", "active"),
                        ))
                    i += 1
            i += 1
        return TemporalComponent(
            source_file=source_file,
            computed_date=computed_date,
            entries=entries,
        )

    def deserialize_intents(self, text: str) -> IntentsComponent:
        """Parse .aleph.intents format."""
        source_file = ""
        entries: list[IntentEntry] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[SOURCE:") and line.endswith("]"):
                source_file = line[len("[SOURCE:"):-1]
            elif line == "[ENTRIES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/ENTRIES]":
                    entry_line = lines[i].strip()
                    # format: f_abc123 INTENT:description [confidence]
                    parts = entry_line.split(None, 2)
                    if len(parts) >= 2:
                        sid = SymbolID.from_string(parts[0])
                        tag_desc = parts[1]
                        confidence = ""
                        if len(parts) >= 3:
                            conf_part = parts[2]
                            if conf_part.startswith("[") and conf_part.endswith("]"):
                                confidence = conf_part[1:-1]
                        if ":" in tag_desc:
                            tag_type, description = tag_desc.split(":", 1)
                        else:
                            tag_type = tag_desc
                            description = ""
                        entries.append(IntentEntry(
                            symbol_id=sid,
                            tag_type=tag_type,
                            description=description,
                            confidence=confidence,
                        ))
                    i += 1
            i += 1
        return IntentsComponent(source_file=source_file, entries=entries)

    def deserialize_errors(self, text: str) -> ErrorsComponent:
        """Parse .aleph.errors format."""
        source_file = ""
        sources: list[ErrorSource] = []
        boundaries: list[ErrorBoundary] = []
        unhandled: list[UnhandledError] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[SOURCE:") and line.endswith("]"):
                source_file = line[len("[SOURCE:"):-1]
            elif line == "[SOURCES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/SOURCES]":
                    parts = lines[i].strip().split(None, 3)
                    if len(parts) >= 4:
                        sid = SymbolID.from_string(parts[0])
                        error_type = parts[1]
                        # "propagation -> surfaces_at"
                        rest = parts[2] + " " + parts[3] if len(parts) > 3 else parts[2]
                        if " -> " in rest:
                            propagation, surfaces_at = rest.split(" -> ", 1)
                        else:
                            propagation = rest
                            surfaces_at = "caller"
                        sources.append(ErrorSource(
                            symbol_id=sid,
                            error_type=error_type,
                            propagation=propagation,
                            surfaces_at=surfaces_at,
                        ))
                    i += 1
            elif line == "[BOUNDARIES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/BOUNDARIES]":
                    entry_line = lines[i].strip()
                    parts = entry_line.split(None, 1)
                    if len(parts) >= 2:
                        sid = SymbolID.from_string(parts[0])
                        attrs = {}
                        for segment in parts[1].split():
                            if "=" in segment:
                                k, v = segment.split("=", 1)
                                attrs[k] = v
                        boundaries.append(ErrorBoundary(
                            symbol_id=sid,
                            catches=attrs.get("catches", ""),
                            recovery=attrs.get("recovery", ""),
                        ))
                    i += 1
            elif line == "[UNHANDLED]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/UNHANDLED]":
                    entry_line = lines[i].strip()
                    parts = entry_line.split(None, 2)
                    if len(parts) >= 3:
                        sid = SymbolID.from_string(parts[0])
                        error_type = parts[1].rstrip(":")
                        description = parts[2]
                        unhandled.append(UnhandledError(
                            symbol_id=sid,
                            error_type=error_type,
                            description=description,
                        ))
                    i += 1
            i += 1
        return ErrorsComponent(
            source_file=source_file,
            sources=sources,
            boundaries=boundaries,
            unhandled=unhandled,
        )

    def deserialize_tests(self, text: str) -> TestsComponent:
        """Parse .aleph.tests format."""
        source_file = ""
        coverage: list[CoverageEntry] = []
        test_details: list[TestDetail] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[SOURCE:") and line.endswith("]"):
                source_file = line[len("[SOURCE:"):-1]
            elif line == "[COVERAGE]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/COVERAGE]":
                    entry_line = lines[i].strip()
                    parts = entry_line.split()
                    if len(parts) >= 2:
                        sid = SymbolID.from_string(parts[0])
                        attrs = {}
                        for p in parts[1:]:
                            if "=" in p:
                                k, v = p.split("=", 1)
                                attrs[k] = v
                        test_ids = [t for t in attrs.get("tests", "").split(",") if t and t != "none"]
                        uncovered = [u for u in attrs.get("uncovered", "").split(",") if u]
                        coverage.append(CoverageEntry(
                            symbol_id=sid,
                            status=attrs.get("status", "none"),
                            test_ids=test_ids,
                            uncovered=uncovered,
                        ))
                    i += 1
            elif line == "[TESTS]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/TESTS]":
                    entry_line = lines[i].strip()
                    parts = entry_line.split()
                    if len(parts) >= 2:
                        sid = SymbolID.from_string(parts[0])
                        attrs = {}
                        for p in parts[1:]:
                            if "=" in p:
                                k, v = p.split("=", 1)
                                attrs[k] = v
                        covers = [c for c in attrs.get("covers", "").split(",") if c and c != "none"]
                        behaviors = [b for b in attrs.get("behaviors", "").split(",") if b and b != "none"]
                        test_details.append(TestDetail(
                            test_id=sid,
                            covers=covers,
                            behaviors=behaviors,
                        ))
                    i += 1
            i += 1
        return TestsComponent(
            source_file=source_file,
            coverage=coverage,
            test_details=test_details,
        )

    # ── Project-level deserialization (Phase 2.1) ──

    def deserialize_project_map(self, text: str) -> ProjectMapComponent:
        root = ""
        files: list[ProjectFileEntry] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line == "[FILES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/FILES]":
                    parts = lines[i].strip().split(None, 1)
                    if len(parts) == 2:
                        path = parts[0]
                        attrs = self._parse_attrs(parts[1])
                        tokens_str = attrs.get("tokens", "0->0")
                        orig, comp = tokens_str.split("->") if "->" in tokens_str else ("0", "0")
                        files.append(ProjectFileEntry(
                            path=path,
                            language=attrs.get("lang", ""),
                            semantic_hash=attrs.get("hash", ""),
                            symbol_count=int(attrs.get("syms", "0")),
                            call_edge_count=int(attrs.get("calls", "0")),
                            original_tokens=int(orig),
                            compressed_tokens=int(comp),
                            reduction_percent=float(attrs.get("reduction", "0").rstrip("%")),
                        ))
                    i += 1
            i += 1
        return ProjectMapComponent(root=root, files=files)

    def deserialize_project_dict(self, text: str) -> ProjectDictComponent:
        root = ""
        symbols: list[ProjectSymbolEntry] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line in ("[SYMBOLS]", "[IMPORTS]"):
                end_tag = "[/SYMBOLS]" if line == "[SYMBOLS]" else "[/IMPORTS]"
                i += 1
                while i < len(lines) and lines[i].strip() != end_tag:
                    entry_line = lines[i].strip()
                    parts = entry_line.split()
                    if parts and "=" in parts[0]:
                        sid, qname = parts[0].split("=", 1)
                        attrs = self._parse_attrs(" ".join(parts[1:]))
                        symbols.append(ProjectSymbolEntry(
                            symbol_id=sid,
                            name=qname.rsplit("::", 1)[-1].rsplit(".", 1)[-1],
                            qualified_name=qname,
                            kind=attrs.get("kind", ""),
                            scope=attrs.get("scope", ""),
                            file=attrs.get("file", ""),
                            signature_hash=attrs.get("sig", ""),
                        ))
                    i += 1
            i += 1
        return ProjectDictComponent(root=root, symbols=symbols)

    def deserialize_project_fs(self, text: str) -> ProjectFSComponent:
        root = ""
        files: list[ProjectFSEntry] = []
        module_deps: list[ProjectModuleDep] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line == "[TREE]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/TREE]":
                    parts = lines[i].strip().split(None, 1)
                    if len(parts) == 2:
                        attrs = self._parse_attrs(parts[1])
                        files.append(ProjectFSEntry(
                            path=parts[0],
                            language=attrs.get("lang", ""),
                            symbol_count=int(attrs.get("syms", "0")),
                        ))
                    i += 1
            elif line == "[DEPS]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/DEPS]":
                    parts = lines[i].strip().split()
                    if parts and "->" in parts[0]:
                        src, tgt = parts[0].split("->", 1)
                        attrs = self._parse_attrs(" ".join(parts[1:]))
                        module_deps.append(ProjectModuleDep(
                            source=src, target=tgt,
                            symbol_count=int(attrs.get("syms", "0")),
                        ))
                    i += 1
            i += 1
        return ProjectFSComponent(root=root, files=files, module_deps=module_deps)

    def deserialize_project_struct(self, text: str) -> ProjectStructComponent:
        root = ""
        cross_refs: list[ProjectCrossRef] = []
        file_deps: list[ProjectFileDep] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line == "[XREFS]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/XREFS]":
                    parts = lines[i].strip().split()
                    if parts and "->" in parts[0]:
                        caller_raw, callee_raw = parts[0].split("->", 1)
                        # Strip optional (name) suffix
                        caller_name = ""
                        callee_name = ""
                        if "(" in caller_raw:
                            caller_id, caller_name = caller_raw.split("(", 1)
                            caller_name = caller_name.rstrip(")")
                        else:
                            caller_id = caller_raw
                        if "(" in callee_raw:
                            callee_id, callee_name = callee_raw.split("(", 1)
                            callee_name = callee_name.rstrip(")")
                        else:
                            callee_id = callee_raw
                        attrs = self._parse_attrs(" ".join(parts[1:]))
                        cross_refs.append(ProjectCrossRef(
                            caller_id=caller_id, callee_id=callee_id,
                            source_file=attrs.get("src", ""),
                            target_file=attrs.get("dst", ""),
                            caller_name=caller_name,
                            callee_name=callee_name,
                        ))
                    i += 1
            elif line == "[FILEDEPS]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/FILEDEPS]":
                    parts = lines[i].strip().split()
                    if parts and "->" in parts[0]:
                        src, tgt = parts[0].split("->", 1)
                        attrs = self._parse_attrs(" ".join(parts[1:]))
                        file_deps.append(ProjectFileDep(
                            source=src, target=tgt,
                            symbol_refs=int(attrs.get("refs", "0")),
                        ))
                    i += 1
            i += 1
        return ProjectStructComponent(root=root, cross_refs=cross_refs, file_deps=file_deps)

    def deserialize_project_salience(self, text: str) -> ProjectSalienceComponent:
        root = ""
        entries: list[ProjectSalienceEntry] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line == "[SCORES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/SCORES]":
                    entry_line = lines[i].strip()
                    # Format: sid qname file=... score=... local=... xfile=... total=...
                    # qname may contain spaces, so split on " file=" to delimit
                    if " file=" in entry_line:
                        prefix, attr_part = entry_line.split(" file=", 1)
                        prefix_parts = prefix.split(None, 1)
                        if len(prefix_parts) >= 2:
                            sid = prefix_parts[0]
                            qname = prefix_parts[1]
                            attrs = self._parse_attrs("file=" + attr_part)
                            entries.append(ProjectSalienceEntry(
                                symbol_id=sid,
                                qualified_name=qname,
                                file=attrs.get("file", ""),
                                score=float(attrs.get("score", "0")),
                                local_fan_in=int(attrs.get("local", "0")),
                                cross_file_fan_in=int(attrs.get("xfile", "0")),
                                total_fan_in=int(attrs.get("total", "0")),
                            ))
                    i += 1
            i += 1
        return ProjectSalienceComponent(root=root, entries=entries)

    def deserialize_project_attention(self, text: str) -> ProjectAttentionComponent:
        root = ""
        entries: list[ProjectAttentionEntry] = []
        budget: dict[str, int] = {}
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("[ROOT:") and line.endswith("]"):
                root = line[len("[ROOT:"):-1]
            elif line == "[BUDGET]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/BUDGET]":
                    item = lines[i].strip()
                    if "=" in item:
                        k, v = item.split("=", 1)
                        budget[k] = int(v)
                    i += 1
            elif line == "[ENTRIES]":
                i += 1
                while i < len(lines) and lines[i].strip() != "[/ENTRIES]":
                    entry_line = lines[i].strip()
                    # Format: sid level qname file=... score=...
                    # qname may contain spaces, so split on " file=" to delimit
                    if " file=" in entry_line:
                        prefix, attr_part = entry_line.split(" file=", 1)
                        prefix_parts = prefix.split(None, 2)
                        if len(prefix_parts) >= 3:
                            sid = prefix_parts[0]
                            level_str = prefix_parts[1]
                            qname = prefix_parts[2]
                            attrs = self._parse_attrs("file=" + attr_part)
                            entries.append(ProjectAttentionEntry(
                                symbol_id=sid,
                                qualified_name=qname,
                                file=attrs.get("file", ""),
                                level=AttentionLevel(level_str),
                                score=float(attrs.get("score", "0")),
                            ))
                    i += 1
            i += 1
        return ProjectAttentionComponent(root=root, entries=entries, budget=budget)

    @staticmethod
    def _parse_attrs(text: str) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for token in text.split():
            if "=" in token:
                k, v = token.split("=", 1)
                attrs[k] = v
        return attrs
