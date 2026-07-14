"""Writes per-file .aleph.struct and .aleph.bodies files."""

from __future__ import annotations

import os

from aleph.model.components import (
    StructComponent, BodiesComponent,
    TemporalComponent, IntentsComponent, ErrorsComponent, TestsComponent,
    ProjectMapComponent, ProjectDictComponent, ProjectFSComponent,
    ProjectStructComponent, ProjectSalienceComponent, ProjectAttentionComponent,
    ProjectTemporalComponent, ProjectCoverageComponent,
)
from aleph.emit.serializer import AlephSerializer


class FileComponentWriter:
    """Writes Aleph component files to disk."""

    def __init__(self, output_dir: str | None = None) -> None:
        self.output_dir = output_dir
        self.serializer = AlephSerializer()

    def write_struct(self, component: StructComponent) -> str:
        """Write .aleph.struct file. Returns the output path."""
        content = self.serializer.serialize_struct(component)
        path = self._output_path(component.source_file, ".aleph.struct")
        self._write(path, content)
        return path

    def write_bodies(self, component: BodiesComponent, include_original_bodies: bool = False) -> str:
        """Write .aleph.bodies file. Returns the output path."""
        content = self.serializer.serialize_bodies(
            component, include_original_bodies=include_original_bodies
        )
        path = self._output_path(component.source_file, ".aleph.bodies")
        self._write(path, content)
        return path

    def write_bundle_json(self, struct_component: StructComponent, bodies_component: BodiesComponent) -> str:
        """Write combined JSON bundle for deterministic machine consumption."""
        content = self.serializer.serialize_bundle_json(struct_component, bodies_component)
        path = self._output_path(struct_component.source_file, ".aleph.json")
        self._write(path, content)
        return path

    def write_temporal(self, component: TemporalComponent) -> str:
        """Write .aleph.temporal file. Returns the output path."""
        content = self.serializer.serialize_temporal(component)
        path = self._output_path(component.source_file, ".aleph.temporal")
        self._write(path, content)
        return path

    def write_intents(self, component: IntentsComponent) -> str:
        """Write .aleph.intents file. Returns the output path."""
        content = self.serializer.serialize_intents(component)
        path = self._output_path(component.source_file, ".aleph.intents")
        self._write(path, content)
        return path

    def write_errors(self, component: ErrorsComponent) -> str:
        """Write .aleph.errors file. Returns the output path."""
        content = self.serializer.serialize_errors(component)
        path = self._output_path(component.source_file, ".aleph.errors")
        self._write(path, content)
        return path

    def write_tests(self, component: TestsComponent) -> str:
        """Write .aleph.tests file. Returns the output path."""
        content = self.serializer.serialize_tests(component)
        path = self._output_path(component.source_file, ".aleph.tests")
        self._write(path, content)
        return path

    def _output_path(self, source_file: str, suffix: str) -> str:
        base = os.path.basename(source_file)
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
            return os.path.join(self.output_dir, base + suffix)
        return source_file + suffix

    # ── Project-level writers (Phase 2.1) ──

    def write_project_map(self, component: ProjectMapComponent) -> str:
        content = self.serializer.serialize_project_map(component)
        path = self._project_output_path("project.aleph.map")
        self._write(path, content)
        return path

    def write_project_dict(self, component: ProjectDictComponent) -> str:
        content = self.serializer.serialize_project_dict(component)
        path = self._project_output_path("project.aleph.dict")
        self._write(path, content)
        return path

    def write_project_fs(self, component: ProjectFSComponent) -> str:
        content = self.serializer.serialize_project_fs(component)
        path = self._project_output_path("project.aleph.fs")
        self._write(path, content)
        return path

    def write_project_struct(self, component: ProjectStructComponent) -> str:
        content = self.serializer.serialize_project_struct(component)
        path = self._project_output_path("project.aleph.struct")
        self._write(path, content)
        return path

    def write_project_salience(self, component: ProjectSalienceComponent) -> str:
        content = self.serializer.serialize_project_salience(component)
        path = self._project_output_path("project.aleph.salience")
        self._write(path, content)
        return path

    def write_project_attention(self, component: ProjectAttentionComponent) -> str:
        content = self.serializer.serialize_project_attention(component)
        path = self._project_output_path("project.aleph.attention")
        self._write(path, content)
        return path

    def write_project_temporal(self, component: ProjectTemporalComponent) -> str:
        content = self.serializer.serialize_project_temporal(component)
        path = self._project_output_path("project.aleph.temporal")
        self._write(path, content)
        return path

    def write_project_coverage(self, component: ProjectCoverageComponent, salience=None) -> str:
        content = self.serializer.serialize_project_coverage(component, salience=salience)
        path = self._project_output_path("project.aleph.coverage")
        self._write(path, content)
        return path

    def _project_output_path(self, filename: str) -> str:
        if self.output_dir:
            os.makedirs(self.output_dir, exist_ok=True)
            return os.path.join(self.output_dir, filename)
        return filename

    def _write(self, path: str, content: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
