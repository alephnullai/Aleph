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

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from aleph.epistemic.store import EpistemicStore
from aleph.project.paths import resolve_artifact_dir


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

    # Single source of truth: aleph.project.paths.resolve_artifact_dir
    _resolve_artifact_dir = staticmethod(resolve_artifact_dir)

    def _epistemic_path(self) -> str:
        return os.path.join(self._artifact_dir, "project.aleph.epistemic")

    def _epistemic_store(self) -> EpistemicStore:
        return EpistemicStore(self._epistemic_path())

    def _load_epistemic(self) -> dict:
        return self._epistemic_store().load()

    def _save_epistemic(self, data: dict) -> None:
        self._epistemic_store().save(data)

    def _contained_path(self, path: str) -> str | None:
        """Resolve a patch target path and verify it stays inside the project.

        Returns the resolved absolute path, or None if the path escapes the
        project root (absolute paths outside the root, ../ traversal, or
        symlinks pointing outside — realpath resolves all of these).
        """
        if not os.path.isabs(path):
            path = os.path.join(self.project_dir, path)
        root = os.path.realpath(self.project_dir)
        resolved = os.path.realpath(path)
        if resolved != root and not resolved.startswith(root + os.sep):
            return None
        return resolved

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

        Raises ValueError if the target path resolves outside the project root.
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

        if resolved_file and self._contained_path(resolved_file) is None:
            raise ValueError(
                f"Refused: patch target '{resolved_file}' resolves outside "
                f"the project root."
            )

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

    # Languages the apply engine can edit. Propose records any language;
    # apply is explicitly Python-scoped (string/AST line conventions below
    # are Python-specific: def/class keywords, docstrings, indentation).
    _APPLY_LANGUAGES = frozenset({"python"})
    _PYTHON_EXTS = frozenset({".py", ".pyi"})

    def apply(self, patch_id: str, force: bool = False) -> PatchApplyResult:
        """Apply a patch: generate concrete code and write to source file.

        Validates that the target symbol's semantic hash hasn't changed
        since patch creation. If it has, requires --force.

        Python only. The target is located by the symbol's recorded span
        (file + line range from the dictionary artifact); duplicate names
        are disambiguated by qualified name, and remaining ambiguity is an
        error listing the candidates instead of patching the first match.

        Renders the intent as a comment + TODO block in the function body.
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

        # Resolve the target symbol (by ID, falling back to exact
        # name/qualified-name lookup with ambiguity detection).
        resolved, resolve_error = self._resolve_patch_target(record.symbol_id)
        if resolve_error:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=resolve_error,
            )

        # Check semantic hash drift
        hash_changed = False
        if record.semantic_hash and resolved is not None:
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

        # Determine target file
        target_file = record.file
        if not target_file and resolved is not None:
            target_file = resolved.file

        if not target_file:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Cannot determine source file for {record.symbol_id}.",
            )

        # Resolve to absolute path — and refuse anything outside the project.
        # Containment is checked FIRST (P0 security): even a wrong-language
        # target must never leak a path probe outside the root.
        abs_file = self._contained_path(target_file)
        if abs_file is None:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=(
                    f"Refused: patch target '{target_file}' resolves outside "
                    f"the project root."
                ),
            )

        # Language gate: apply is Python-only (propose can record any target)
        language = (resolved.language if resolved else "") or self._language_for_file(target_file)
        if language not in self._APPLY_LANGUAGES:
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=(
                    f"Patch apply currently supports Python only — "
                    f"target '{target_file}' is {language or 'an unrecognized language'}. "
                    f"The patch stays pending; apply the intent manually."
                ),
            )

        if not os.path.isfile(abs_file):
            return PatchApplyResult(
                success=False,
                patch_id=patch_id,
                message=f"Source file not found: {abs_file}",
            )

        # Generate and apply the patch (span-located TODO template)
        success, fail_reason = self._apply_python_patch(abs_file, record, resolved)

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
                message=f"Failed to apply patch {patch_id}: {fail_reason}",
            )

    @classmethod
    def _language_for_file(cls, path: str) -> str:
        """Best-effort language detection from extension (artifact fallback)."""
        ext = os.path.splitext(path)[1].lower()
        if ext in cls._PYTHON_EXTS:
            return "python"
        try:
            from aleph.ingest.languages import LanguageRegistry
            return LanguageRegistry.language_for_extension(ext) or ""
        except Exception:
            return ""

    def _resolve_patch_target(self, symbol_id: str):
        """Resolve a patch target to a single dictionary entry.

        Returns (ResolveResult | None, error_message | None):
          - exact symbol-ID hit → (entry, None)
          - unique name/qualified-name match → (entry, None)
          - multiple matches → (None, error listing candidates)
          - no engine / no match → (None, None)  [legacy path: record.file]
        """
        engine = self._resolve_symbol()
        if engine is None:
            return None, None

        resolved = engine.resolve(symbol_id)
        if resolved is not None:
            return resolved, None

        candidates = engine.find_by_name(symbol_id)
        if not candidates:
            return None, None
        if len(candidates) == 1:
            return candidates[0], None

        listing = "; ".join(
            f"{c.symbol_id} ({c.qualified_name}, {c.file}"
            + (f":{c.start_line}" if c.start_line else "")
            + ")"
            for c in candidates
        )
        return None, (
            f"Ambiguous symbol '{symbol_id}': {len(candidates)} candidates — "
            f"{listing}. Re-propose using the symbol ID or qualified name."
        )

    def _apply_python_patch(
        self,
        file_path: str,
        record: PatchRecord,
        resolved,
    ) -> tuple[bool, str]:
        """Insert the TODO+comment block into the target symbol's body.

        Location strategy (Python only):
          1. If the dictionary recorded a span, find the def/class line
             inside that line range.
          2. Otherwise scan the file for def/class lines matching the
             symbol name; duplicates are disambiguated by the qualified
             name's enclosing class. Remaining ambiguity is an error —
             never patch the first match blindly.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        symbol_name = resolved.name if resolved is not None else record.symbol_id
        qualified_name = resolved.qualified_name if resolved is not None else ""

        def_idx: int | None = None

        # 1. Span-based location (preferred)
        if resolved is not None and resolved.start_line > 0:
            lo = max(resolved.start_line - 1, 0)
            hi = min(resolved.end_line, len(lines))
            in_span = self._find_named_definitions(lines, symbol_name, lo, hi)
            if in_span:
                def_idx = in_span[0]

        # 2. Name scan with qualified-name disambiguation
        if def_idx is None:
            matches = self._find_named_definitions(lines, symbol_name, 0, len(lines))
            if not matches:
                return False, (
                    f"could not locate symbol '{symbol_name}' in source"
                    + (
                        f" (recorded span lines "
                        f"{resolved.start_line}-{resolved.end_line} is stale — "
                        f"run `aleph build` to refresh artifacts)"
                        if resolved is not None and resolved.start_line > 0
                        else ""
                    )
                )
            if len(matches) > 1:
                expected_class = self._enclosing_name_from_qualified(
                    qualified_name, symbol_name
                )
                if expected_class:
                    narrowed = [
                        idx for idx in matches
                        if self._enclosing_class(lines, idx) == expected_class
                    ]
                    if len(narrowed) == 1:
                        matches = narrowed
                if len(matches) > 1:
                    locations = ", ".join(f"line {idx + 1}" for idx in matches)
                    return False, (
                        f"ambiguous definition '{symbol_name}' "
                        f"({len(matches)} occurrences: {locations}) and no span "
                        f"recorded — run `aleph build` to refresh artifacts"
                    )
            def_idx = matches[0]

        insert_idx = self._body_start_from_def(lines, def_idx)
        indent = self._line_indent(lines[def_idx]) + "    "

        todo_lines = [
            f"{indent}# TODO [{record.patch_id}]: {record.intent}\n",
            f"{indent}# ALEPH:PATCH applied — review and implement the above intent\n",
        ]
        for i, todo_line in enumerate(todo_lines):
            lines.insert(insert_idx + i, todo_line)

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True, ""

    @staticmethod
    def _line_indent(line: str) -> str:
        return line[: len(line) - len(line.lstrip())].replace("\t", "    ")

    @staticmethod
    def _is_definition_line(line: str, name: str) -> bool:
        stripped = line.strip()
        for kw in ("def ", "async def ", "class "):
            if stripped.startswith(kw):
                rest = stripped[len(kw):]
                if rest == name or (
                    rest.startswith(name)
                    and rest[len(name):][:1] in ("(", ":", " ")
                ):
                    return True
        return False

    @classmethod
    def _find_named_definitions(
        cls, lines: list[str], name: str, start: int, end: int
    ) -> list[int]:
        """Indices of def/class lines defining ``name`` within [start, end)."""
        return [
            i for i in range(start, min(end, len(lines)))
            if cls._is_definition_line(lines[i], name)
        ]

    @staticmethod
    def _enclosing_name_from_qualified(qualified_name: str, name: str) -> str:
        """Extract the enclosing scope (e.g. class) from a qualified name.

        'ClassA::run' or 'ClassA.run' → 'ClassA'; bare names → ''.
        """
        if not qualified_name or qualified_name == name:
            return ""
        for sep in ("::", "."):
            if sep in qualified_name:
                prefix = qualified_name.rsplit(sep, 1)[0]
                return prefix.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        return ""

    @classmethod
    def _enclosing_class(cls, lines: list[str], def_idx: int) -> str:
        """Name of the closest enclosing class for a definition line, or ''."""
        def_indent = len(cls._line_indent(lines[def_idx]))
        for i in range(def_idx - 1, -1, -1):
            stripped = lines[i].strip()
            if not stripped:
                continue
            indent = len(cls._line_indent(lines[i]))
            if indent < def_indent and stripped.startswith("class "):
                rest = stripped[len("class "):]
                for stop in ("(", ":", " "):
                    if stop in rest:
                        rest = rest.split(stop, 1)[0]
                return rest
        return ""

    @classmethod
    def _body_start_from_def(cls, lines: list[str], def_idx: int) -> int:
        """Index of the first body line after a def/class line at def_idx.

        Skips the signature continuation (to the closing colon) and an
        optional docstring.
        """
        # Find the colon that ends the signature
        colon_line = def_idx
        for j in range(def_idx, min(def_idx + 20, len(lines))):
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

    @classmethod
    def _find_symbol_body_start(cls, lines: list[str], name: str) -> int | None:
        """Find the line index where a symbol's body starts.

        Returns the index after the FIRST def/class line matching ``name``
        (and optional docstring). Kept for backward compatibility — the
        apply path uses span-located/disambiguated lookup instead.
        """
        matches = cls._find_named_definitions(lines, name, 0, len(lines))
        if not matches:
            return None
        return cls._body_start_from_def(lines, matches[0])

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
