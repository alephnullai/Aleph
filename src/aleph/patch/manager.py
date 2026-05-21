"""Patch manager — core logic for semantic patch lifecycle.

Patches are stored in project.aleph.epistemic as agent-derived components.
Each patch records:
  - patch_id: unique identifier (patch_<n>)
  - symbol_id: target symbol
  - intent: what the patch intends to change
  - semantic_hash: hash of the target symbol at patch creation time
  - file: source file containing the symbol
  - status: pending | applied | rejected
  - created_at: ISO timestamp
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class PatchRecord:
    """A single semantic patch record."""
    patch_id: str
    symbol_id: str
    intent: str
    semantic_hash: str
    file: str
    status: str = "pending"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "patch_id": self.patch_id,
            "symbol_id": self.symbol_id,
            "intent": self.intent,
            "semantic_hash": self.semantic_hash,
            "file": self.file,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PatchRecord:
        return cls(
            patch_id=d["patch_id"],
            symbol_id=d["symbol_id"],
            intent=d["intent"],
            semantic_hash=d.get("semantic_hash", ""),
            file=d.get("file", ""),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", ""),
        )


@dataclass
class PatchApplyResult:
    """Result of applying a patch."""
    success: bool
    patch_id: str
    message: str
    file_path: str = ""
    hash_changed: bool = False


class PatchManager:
    """Manages the semantic patch lifecycle against .aleph/project.aleph.epistemic."""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self._artifact_dir = self._resolve_artifact_dir(project_dir)

    @staticmethod
    def _resolve_artifact_dir(project_dir: str) -> str:
        """Resolve .aleph/ as artifact root only if it contains project.aleph.dict.

        This matches the logic in handlers.py, query/engine.py, and session_memory.py
        to prevent split-brain writes to different epistemic files.
        """
        aleph_subdir = os.path.join(project_dir, ".aleph")
        if os.path.isdir(aleph_subdir) and os.path.isfile(
            os.path.join(aleph_subdir, "project.aleph.dict")
        ):
            return aleph_subdir
        return project_dir

    def _epistemic_path(self) -> str:
        return os.path.join(self._artifact_dir, "project.aleph.epistemic")

    def _load_epistemic(self) -> dict:
        path = self._epistemic_path()
        if not os.path.isfile(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}

    def _save_epistemic(self, data: dict) -> None:
        path = self._epistemic_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _next_patch_id(self, data: dict) -> str:
        patches = data.get("patches", [])
        max_n = 0
        for p in patches:
            pid = p.get("patch_id", "")
            if pid.startswith("patch_"):
                try:
                    n = int(pid[6:])
                    max_n = max(max_n, n)
                except ValueError:
                    pass
        return f"patch_{max_n + 1}"

    def _resolve_symbol(self) -> "QueryEngine | None":
        """Lazily get a QueryEngine for symbol resolution.

        Returns None if the project doesn't have the required artifacts.
        """
        try:
            from aleph.query.engine import QueryEngine
            engine = QueryEngine(self.project_dir)
            # Verify dict file exists before returning
            dict_path = os.path.join(engine._artifact_dir, "project.aleph.dict")
            if not os.path.isfile(dict_path):
                return None
            return engine
        except Exception:
            return None

    def propose(
        self,
        symbol_id: str,
        intent: str,
        file: str | None = None,
    ) -> PatchRecord:
        """Create a new pending patch.

        Resolves the symbol to get its file and semantic hash at creation time.
        If file is provided, it overrides the resolved file path.
        """
        semantic_hash = ""
        resolved_file = file or ""

        engine = self._resolve_symbol()
        if engine is not None:
            resolved = engine.resolve(symbol_id)
            if resolved is not None:
                if not resolved_file:
                    resolved_file = resolved.file
                semantic_hash = resolved.signature_hash or ""

        data = self._load_epistemic()
        patch_id = self._next_patch_id(data)

        record = PatchRecord(
            patch_id=patch_id,
            symbol_id=symbol_id,
            intent=intent,
            semantic_hash=semantic_hash,
            file=resolved_file,
            status="pending",
        )

        data.setdefault("patches", []).append(record.to_dict())
        self._save_epistemic(data)
        return record

    def list_patches(self, status: str | None = None) -> list[PatchRecord]:
        """List patches, optionally filtered by status."""
        data = self._load_epistemic()
        patches = data.get("patches", [])
        records = [PatchRecord.from_dict(p) for p in patches]
        if status:
            records = [r for r in records if r.status == status]
        return records

    def get_patch(self, patch_id: str) -> PatchRecord | None:
        """Get a specific patch by ID."""
        for r in self.list_patches():
            if r.patch_id == patch_id:
                return r
        return None

    def apply(self, patch_id: str, force: bool = False) -> PatchApplyResult:
        """Apply a patch: generate concrete code and write to source file.

        Validates that the target symbol's semantic hash hasn't changed
        since patch creation. If it has, requires --force.

        Phase 3.4: template-based approach — renders the intent as a
        comment + TODO block in the function body.
        """
        data = self._load_epistemic()
        patches = data.get("patches", [])

        # Find the patch
        patch_idx = None
        patch_dict = None
        for i, p in enumerate(patches):
            if p.get("patch_id") == patch_id:
                patch_idx = i
                patch_dict = p
                break

        if patch_dict is None:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Patch {patch_id} not found.",
            )

        record = PatchRecord.from_dict(patch_dict)

        if record.status != "pending":
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Patch {patch_id} is {record.status}, not pending.",
            )

        # Check semantic hash drift
        hash_changed = False
        current_hash = ""
        engine = self._resolve_symbol()
        if engine is not None and record.semantic_hash:
            resolved = engine.resolve(record.symbol_id)
            if resolved is not None:
                current_hash = resolved.signature_hash or ""
                if current_hash and current_hash != record.semantic_hash:
                    hash_changed = True
                    if not force:
                        return PatchApplyResult(
                            success=False,
                            patch_id=patch_id,
                            message=(
                                f"Semantic hash changed for {record.symbol_id}: "
                                f"{record.semantic_hash} -> {current_hash}. "
                                f"Use --force to apply anyway."
                            ),
                            hash_changed=True,
                        )

        # Get current body via expand
        current_body = None
        if engine is not None:
            current_body = engine.expand(record.symbol_id)

        # Determine target file
        target_file = record.file
        if not target_file and engine is not None:
            resolved = engine.resolve(record.symbol_id)
            if resolved is not None:
                target_file = resolved.file

        if not target_file:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Cannot determine source file for {record.symbol_id}.",
            )

        # Resolve to absolute path
        abs_file = target_file
        if not os.path.isabs(abs_file):
            abs_file = os.path.join(self.project_dir, abs_file)

        if not os.path.isfile(abs_file):
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Source file not found: {abs_file}",
            )

        # Generate and apply the patch (template-based)
        success = self._apply_template_patch(abs_file, record, current_body)

        if success:
            # Update status in epistemic
            patches[patch_idx]["status"] = "applied"
            self._save_epistemic(data)
            return PatchApplyResult(
                success=True,
                patch_id=patch_id,
                message=f"Patch {patch_id} applied to {target_file}.",
                file_path=abs_file,
                hash_changed=hash_changed,
            )
        else:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Failed to apply patch {patch_id}: could not locate symbol in source.",
            )

    def _apply_template_patch(
        self,
        file_path: str,
        record: PatchRecord,
        current_body: str | None,
    ) -> bool:
        """Apply a template-based patch: insert TODO+comment block.

        Finds the symbol definition in the source file and inserts a
        TODO block after the first line of the function/method body.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Build the TODO block
        todo_lines = [
            f"    # TODO [{record.patch_id}]: {record.intent}\n",
            f"    # ALEPH:PATCH applied — review and implement the above intent\n",
        ]

        # Find the symbol definition line
        # Look for "def <name>" or "class <name>" patterns
        symbol_name = record.symbol_id
        # Try to get the actual name from the engine
        engine = self._resolve_symbol()
        if engine is not None:
            resolved = engine.resolve(record.symbol_id)
            if resolved is not None:
                symbol_name = resolved.name

        insert_idx = self._find_symbol_body_start(lines, symbol_name)
        if insert_idx is None:
            return False

        # Insert TODO block after the body start
        for i, todo_line in enumerate(todo_lines):
            lines.insert(insert_idx + i, todo_line)

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True

    @staticmethod
    def _find_symbol_body_start(lines: list[str], name: str) -> int | None:
        """Find the line index where a symbol's body starts.

        Returns the index after the def/class line (and optional docstring),
        where we should insert the TODO block.
        """
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Match function/method or class definitions
            if (
                stripped.startswith(f"def {name}(")
                or stripped.startswith(f"def {name} (")
                or stripped.startswith(f"class {name}(")
                or stripped.startswith(f"class {name}:")
                or stripped.startswith(f"class {name} ")
                or stripped == f"def {name}():"
            ):
                # Find the colon that ends the signature
                colon_line = i
                for j in range(i, min(i + 20, len(lines))):
                    if ":" in lines[j] and (
                        lines[j].rstrip().endswith(":")
                        or ":" in lines[j].split("#")[0]
                    ):
                        colon_line = j
                        break

                body_start = colon_line + 1

                # Skip docstring if present
                if body_start < len(lines):
                    body_stripped = lines[body_start].strip()
                    if body_stripped.startswith('"""') or body_stripped.startswith("'''"):
                        quote = body_stripped[:3]
                        if body_stripped.count(quote) >= 2 and len(body_stripped) > 6:
                            # Single-line docstring
                            body_start += 1
                        else:
                            # Multi-line docstring
                            for k in range(body_start + 1, len(lines)):
                                if quote in lines[k]:
                                    body_start = k + 1
                                    break

                return body_start

        return None

    def reject(self, patch_id: str) -> str:
        """Mark a patch as rejected."""
        data = self._load_epistemic()
        patches = data.get("patches", [])

        for p in patches:
            if p.get("patch_id") == patch_id:
                if p.get("status") != "pending":
                    return f"Patch {patch_id} is {p['status']}, not pending."
                p["status"] = "rejected"
                self._save_epistemic(data)
                return f"Patch {patch_id} rejected."

        return f"Patch {patch_id} not found."
