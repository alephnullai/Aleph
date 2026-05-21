"""Aleph serializer: renders components to compact .aleph format text.

Optimized for token efficiency (Invariant III). Every token must earn its place.
"""

from __future__ import annotations

import json

from aleph.model.components import (
    StructComponent, BodiesComponent, SignatureEntry, HierarchyNode, BodyEntry,
    TemporalComponent, IntentsComponent, ErrorsComponent, TestsComponent,
    ProjectMapComponent, ProjectDictComponent, ProjectFSComponent,
    ProjectStructComponent, ProjectSalienceComponent, ProjectAttentionComponent,
    ProjectTemporalComponent, ProjectCoverageComponent,
)
from aleph.model.enums import BodyLevel
from aleph.emit.format import (
    STRUCT_HEADER, BODIES_HEADER, TEMPORAL_HEADER,
    INTENTS_HEADER, ERRORS_HEADER, TESTS_HEADER,
    MAP_HEADER, DICT_HEADER, FS_HEADER, PROJECT_STRUCT_HEADER,
    PROJECT_SALIENCE_HEADER, ATTENTION_HEADER, PROJECT_TEMPORAL_HEADER,
    PROJECT_COVERAGE_HEADER,
)
from aleph.util.hashing import byte_hash


class AlephSerializer:
    """Renders StructComponent and BodiesComponent to .aleph text format."""

    def serialize_struct(self, component: StructComponent) -> str:
        """Render .aleph.struct file content.

        Compact format: merged dict+sig, hierarchy only for nested structures, calls as arrows.
        """
        lines = [STRUCT_HEADER, f"[SOURCE:{component.source_file}]"]

        # Merged dict+sig: id kind name [signature]
        lines.append("[SYMBOLS]")
        sig_by_id = {str(s.symbol_id): s for s in component.signatures}
        for id_str, sym in sorted(component.symbols.items()):
            kind = sym.raw.kind.value
            sig = sig_by_id.get(id_str)
            if sig and sig.signature:
                sig_hash = byte_hash(sig.signature)[:8]
                lines.append(f"{id_str} {kind} {sym.raw.qualified_name} sig:{sig_hash}")
            else:
                lines.append(f"{id_str} {kind} {sym.raw.qualified_name}")
        lines.append("[/SYMBOLS]")

        # Hierarchy only if there are nested structures
        has_nesting = any(len(n.children) > 0 for n in component.hierarchy)
        if has_nesting:
            lines.append("[HIER]")
            for node in component.hierarchy:
                if node.children:
                    self._serialize_hierarchy_node(node, lines, indent=0)
            lines.append("[/HIER]")

        # Call graph
        if component.call_edges:
            lines.append("[CALLS]")
            for caller, callee in sorted(component.call_edges):
                lines.append(f"{caller}->{callee}")
            lines.append("[/CALLS]")
        if component.call_edge_metadata:
            lines.append("[CALLMETA]")
            for edge in component.call_edge_metadata:
                lines.append(
                    "|".join(
                        [
                            edge.get("caller_id", ""),
                            edge.get("callee_name", ""),
                            edge.get("status", ""),
                            edge.get("resolved_id", ""),
                            edge.get("candidate_count", "0"),
                        ]
                    )
                )
            lines.append("[/CALLMETA]")

        return "\n".join(lines) + "\n"

    def _serialize_hierarchy_node(
        self, node: HierarchyNode, lines: list[str], indent: int
    ) -> None:
        prefix = "  " * indent
        lines.append(f"{prefix}{node.symbol_id}")
        for child in node.children:
            self._serialize_hierarchy_node(child, lines, indent + 1)

    def serialize_bodies(
        self, component: BodiesComponent, include_original_bodies: bool = False
    ) -> str:
        """Render .aleph.bodies file content.

        Contains: symbol dict for expansion, and body entries.
        OMIT entries are single-line markers. FULL/SUMMARY have content.
        """
        lines = [BODIES_HEADER, f"[SOURCE:{component.source_file}]"]

        # Compact dictionary for bidirectional expansion
        lines.append("[DICT]")
        for id_str, qname in sorted(component.symbol_dict.items()):
            lines.append(f"{id_str}={qname}")
        lines.append("[/DICT]")

        # Body entries
        lines.append("[BODIES]")
        for entry in component.entries:
            if entry.level == BodyLevel.OMIT:
                lines.append(f"[OMIT:{entry.symbol_id}]")
                if include_original_bodies and entry.original_body:
                    lines.append(f"[ORIGINAL:{entry.symbol_id}]")
                    lines.extend(entry.original_body.split("\n"))
                    lines.append(f"[/ORIGINAL:{entry.symbol_id}]")
                continue
            lines.append(f"[{entry.level.value}:{entry.symbol_id}]")
            if entry.content:
                for content_line in entry.content.split("\n"):
                    lines.append(content_line)
            lines.append(f"[/{entry.level.value}:{entry.symbol_id}]")
            if include_original_bodies and entry.original_body:
                lines.append(f"[ORIGINAL:{entry.symbol_id}]")
                lines.extend(entry.original_body.split("\n"))
                lines.append(f"[/ORIGINAL:{entry.symbol_id}]")
        lines.append("[/BODIES]")

        return "\n".join(lines) + "\n"

    def serialize_bundle_json(
        self, struct_component: StructComponent, bodies_component: BodiesComponent
    ) -> str:
        payload = {
            "version": "1.0",
            "source_file": struct_component.source_file,
            "symbols": [
                {
                    "id": sid,
                    "kind": sym.raw.kind.value,
                    "name": sym.raw.name,
                    "qualified_name": sym.raw.qualified_name,
                    "scope": sym.raw.scope,
                    "language": sym.raw.language,
                    "source_file": sym.raw.source_file,
                }
                for sid, sym in sorted(struct_component.symbols.items())
            ],
            "calls": sorted(struct_component.call_edges),
            "call_metadata": struct_component.call_edge_metadata,
            "symbol_dict": dict(sorted(bodies_component.symbol_dict.items())),
            "bodies": [
                {
                    "symbol_id": str(entry.symbol_id),
                    "level": entry.level.value,
                    "content": entry.content,
                    "original_body": entry.original_body,
                }
                for entry in bodies_component.entries
            ],
        }
        return json.dumps(payload, sort_keys=True, indent=2) + "\n"

    def serialize_temporal(self, component: TemporalComponent) -> str:
        """Render .aleph.temporal file content."""
        lines = [TEMPORAL_HEADER, f"[SOURCE:{component.source_file}]"]
        lines.append(f"[DATE:{component.computed_date}]")
        lines.append("[ENTRIES]")
        for entry in component.entries:
            lines.append(
                f"{entry.symbol_id} age={entry.age_days} "
                f"modified={entry.last_modified_days} "
                f"churn={entry.churn_count} "
                f"stability={entry.stability}"
            )
        lines.append("[/ENTRIES]")
        return "\n".join(lines) + "\n"

    def serialize_intents(self, component: IntentsComponent) -> str:
        """Render .aleph.intents file content."""
        lines = [INTENTS_HEADER, f"[SOURCE:{component.source_file}]"]
        lines.append("[ENTRIES]")
        for entry in component.entries:
            lines.append(
                f"{entry.symbol_id} {entry.tag_type}:{entry.description} "
                f"[{entry.confidence}]"
            )
        lines.append("[/ENTRIES]")
        return "\n".join(lines) + "\n"

    def serialize_errors(self, component: ErrorsComponent) -> str:
        """Render .aleph.errors file content."""
        lines = [ERRORS_HEADER, f"[SOURCE:{component.source_file}]"]
        if component.sources:
            lines.append("[SOURCES]")
            for src in component.sources:
                lines.append(
                    f"{src.symbol_id} {src.error_type} "
                    f"{src.propagation} -> {src.surfaces_at}"
                )
            lines.append("[/SOURCES]")
        if component.boundaries:
            lines.append("[BOUNDARIES]")
            for b in component.boundaries:
                lines.append(f"{b.symbol_id} catches={b.catches} recovery={b.recovery}")
            lines.append("[/BOUNDARIES]")
        if component.unhandled:
            lines.append("[UNHANDLED]")
            for u in component.unhandled:
                lines.append(f"{u.symbol_id} {u.error_type}: {u.description}")
            lines.append("[/UNHANDLED]")
        return "\n".join(lines) + "\n"

    def serialize_tests(self, component: TestsComponent) -> str:
        """Render .aleph.tests file content."""
        lines = [TESTS_HEADER, f"[SOURCE:{component.source_file}]"]
        if component.coverage:
            lines.append("[COVERAGE]")
            for c in component.coverage:
                test_list = ",".join(c.test_ids) if c.test_ids else "none"
                line = f"{c.symbol_id} status={c.status} tests={test_list}"
                if c.uncovered:
                    line += f" uncovered={','.join(c.uncovered)}"
                lines.append(line)
            lines.append("[/COVERAGE]")
        if component.test_details:
            lines.append("[TESTS]")
            for t in component.test_details:
                covers = ",".join(t.covers) if t.covers else "none"
                behaviors = ",".join(t.behaviors) if t.behaviors else "none"
                lines.append(
                    f"{t.test_id} covers={covers} behaviors={behaviors}"
                )
            lines.append("[/TESTS]")
        return "\n".join(lines) + "\n"

    # ── Project-level serialization (Phase 2.1) ──

    def serialize_project_map(self, component: ProjectMapComponent) -> str:
        from aleph.__version__ import __version__
        lines = [MAP_HEADER, f"[ROOT:{component.root}]", f"[ALEPH_VERSION:{__version__}]"]
        lines.append("[FILES]")
        for f in component.files:
            lines.append(
                f"{f.path} hash={f.semantic_hash} lang={f.language} "
                f"syms={f.symbol_count} calls={f.call_edge_count} "
                f"tokens={f.original_tokens}->{f.compressed_tokens} "
                f"reduction={f.reduction_percent:.1f}%"
            )
        lines.append("[/FILES]")
        return "\n".join(lines) + "\n"

    def serialize_project_dict(self, component: ProjectDictComponent) -> str:
        lines = [DICT_HEADER, f"[ROOT:{component.root}]"]
        symbols = [s for s in component.symbols if s.kind != "d"]
        imports = [s for s in component.symbols if s.kind == "d"]
        lines.append("[SYMBOLS]")
        for s in symbols:
            parts = [f"{s.symbol_id}={s.qualified_name}", f"file={s.file}", f"kind={s.kind}"]
            if s.scope:
                parts.append(f"scope={s.scope}")
            if s.signature_hash:
                parts.append(f"sig={s.signature_hash}")
            lines.append(" ".join(parts))
        lines.append("[/SYMBOLS]")
        if imports:
            lines.append("[IMPORTS]")
            for s in imports:
                parts = [f"{s.symbol_id}={s.qualified_name}", f"file={s.file}", f"kind={s.kind}"]
                lines.append(" ".join(parts))
            lines.append("[/IMPORTS]")
        return "\n".join(lines) + "\n"

    def serialize_project_fs(self, component: ProjectFSComponent) -> str:
        lines = [FS_HEADER, f"[ROOT:{component.root}]"]
        lines.append("[TREE]")
        for f in component.files:
            lines.append(f"{f.path} lang={f.language} syms={f.symbol_count}")
        lines.append("[/TREE]")
        if component.module_deps:
            lines.append("[DEPS]")
            for dep in component.module_deps:
                lines.append(f"{dep.source}->{dep.target} syms={dep.symbol_count}")
            lines.append("[/DEPS]")
        return "\n".join(lines) + "\n"

    def serialize_project_struct(self, component: ProjectStructComponent) -> str:
        lines = [PROJECT_STRUCT_HEADER, f"[ROOT:{component.root}]"]
        if component.cross_refs:
            lines.append("[XREFS]")
            for xref in component.cross_refs:
                caller = xref.caller_id
                callee = xref.callee_id
                if xref.caller_name:
                    caller += f"({xref.caller_name})"
                if xref.callee_name:
                    callee += f"({xref.callee_name})"
                lines.append(
                    f"{caller}->{callee} "
                    f"src={xref.source_file} dst={xref.target_file}"
                )
            lines.append("[/XREFS]")
        if component.file_deps:
            lines.append("[FILEDEPS]")
            for dep in component.file_deps:
                lines.append(f"{dep.source}->{dep.target} refs={dep.symbol_refs}")
            lines.append("[/FILEDEPS]")
        return "\n".join(lines) + "\n"

    def serialize_project_salience(self, component: ProjectSalienceComponent) -> str:
        lines = [PROJECT_SALIENCE_HEADER, f"[ROOT:{component.root}]"]
        lines.append("[SCORES]")
        for e in component.entries:
            lines.append(
                f"{e.symbol_id} {e.qualified_name} file={e.file} "
                f"score={e.score} local={e.local_fan_in} "
                f"xfile={e.cross_file_fan_in} total={e.total_fan_in}"
            )
        lines.append("[/SCORES]")
        return "\n".join(lines) + "\n"

    def serialize_project_attention(self, component: ProjectAttentionComponent) -> str:
        lines = [ATTENTION_HEADER, f"[ROOT:{component.root}]"]
        lines.append("[BUDGET]")
        for level, count in sorted(component.budget.items()):
            lines.append(f"{level}={count}")
        lines.append("[/BUDGET]")
        lines.append("[ENTRIES]")
        for e in component.entries:
            lines.append(
                f"{e.symbol_id} {e.level.value} {e.qualified_name} "
                f"file={e.file} score={e.score}"
            )
        lines.append("[/ENTRIES]")
        return "\n".join(lines) + "\n"

    def serialize_project_temporal(self, component: ProjectTemporalComponent) -> str:
        """Render project.aleph.temporal matching PLAN.md format."""
        history = "insufficient" if component.insufficient_history else "sufficient"
        lines = [
            PROJECT_TEMPORAL_HEADER,
            f"[PROJECT:{component.root}]",
            f"[COMPUTED:{component.computed_date}]",
            f"[HISTORY:{history}]",
        ]
        if component.insufficient_history:
            lines.append(
                "[NOTE:all symbols show identical git history — "
                "likely a shallow clone or bulk commit. "
                "For meaningful temporal data: git fetch --unshallow]"
            )
        lines.append("[SYMBOLS]")
        for e in component.entries:
            lines.append(
                f"{e.symbol_id}  age={e.age_days}d  last={e.last_modified_days}d  "
                f"churn={e.churn_label}    stability={e.stability}"
            )
        lines.append("[/SYMBOLS]")
        return "\n".join(lines) + "\n"

    def serialize_project_coverage(
        self, component: ProjectCoverageComponent,
        salience: "ProjectSalienceComponent | None" = None,
    ) -> str:
        lines = [PROJECT_COVERAGE_HEADER, f"[ROOT:{component.root}]"]
        lines.append("[SUMMARY]")
        lines.append(f"symbols_total={component.symbols_total}")
        lines.append(f"covered={component.covered}")
        lines.append(f"partial={component.partial}")
        lines.append(f"none={component.none_count}")
        lines.append("[/SUMMARY]")
        uncovered = [e for e in component.entries if e.status == "none"]
        if uncovered and salience:
            from aleph.link.project_salience import (
                _is_vendor_file, _is_test_file, PERIPHERAL_THRESHOLD,
            )
            sal_scores = {e.symbol_id: e.score for e in salience.entries}
            filtered = [
                e for e in uncovered
                if not _is_vendor_file(e.file)
                and not _is_test_file(e.file)
                and sal_scores.get(e.symbol_id, 0) >= PERIPHERAL_THRESHOLD
            ]
            if filtered:
                filtered.sort(key=lambda e: -sal_scores.get(e.symbol_id, 0))
                lines.append("[UNCOVERED]")
                for e in filtered:
                    lines.append(f"{e.symbol_id} {e.qualified_name} file={e.file}")
                n_filtered = len(uncovered) - len(filtered)
                if n_filtered > 0:
                    lines.append(f"[NOTE:filtered {n_filtered} low-salience/vendor/test symbols]")
                lines.append("[/UNCOVERED]")
        elif uncovered:
            lines.append("[UNCOVERED]")
            for e in uncovered:
                lines.append(f"{e.symbol_id} {e.qualified_name} file={e.file}")
            lines.append("[/UNCOVERED]")
        return "\n".join(lines) + "\n"
