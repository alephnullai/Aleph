"""Symbol-ID migration: legacy absolute-path IDs -> portable v2 IDs.

The legacy (v1) ID scheme hashed the verbatim — in practice absolute —
``source_file`` path, so a repo move, checkout-path difference, or a
path-case change churned every symbol ID and orphaned all epistemic
inferences, temporal entries, and pending patches keyed by ID. Scheme v2
hashes the project-root-relative, POSIX-normalized, lowercased path
instead (see :mod:`aleph.symbols.identifier`).

This module recovers from a v1 build:

  * :func:`compute_id_mapping` — re-derives BOTH schemes from the live
    source files. The dictionary artifact records only relative file
    paths, qualified names, and a truncated signature *hash* — not the
    signature text that feeds the ID hash — so old IDs cannot be
    recomputed from the artifacts alone. Instead each file listed in the
    dictionary is re-parsed; for every extracted symbol the old ID is
    computed against the artifact's recorded ``[ROOT:...]`` line (accepted
    as the old absolute root — the old root is not otherwise recorded
    anywhere) and the new ID against the live project root. Files that
    were deleted or whose symbols changed since the artifacts were built
    are NOT recoverable; their dictionary IDs are reported as unmatched.

  * :func:`migrate_ids` — applies the old->new mapping to the epistemic
    store (inferences, flags, pending patch records, session-memory and
    review symbol tables) via the EpistemicStore transaction API.
    Idempotent: new-scheme IDs are never mapping keys (identity pairs
    aside), so a second run rewrites nothing.

  * :func:`maybe_hint_migration` — cheap startup check (string compare of
    the recorded ROOT line vs the live root, plus the recorded
    ``[ID_SCHEME:n]``) that prints a one-line hint suggesting
    ``aleph migrate-ids`` when artifacts look like they predate a move,
    case change, or the v2 scheme.

  * :func:`auto_migrate_ids` — serve-startup self-heal: when the same
    condition fires, runs :func:`migrate_ids` followed by a full rebuild
    so the server comes up healthy after a machine move instead of
    hinting until someone migrates by hand. Fail-soft and disabled by
    ``ALEPH_AUTO_MIGRATE=0``.

Compressed artifacts themselves (map/dict/struct/...) are not rewritten —
a rebuild regenerates them under the new scheme; the migration's job is to
carry the agent-derived state (which a rebuild cannot regenerate) across.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace

from aleph.epistemic.store import EpistemicStore
from aleph.project.paths import DICT_FILENAME, resolve_artifact_dir
from aleph.symbols.identifier import ID_SCHEME_VERSION

EPISTEMIC_FILENAME = "project.aleph.epistemic"
LEGACY_EPISTEMIC_FILENAME = ".aleph.epistemic.json"

# Epistemic list-of-dict sections whose entries reference a symbol via a
# "symbol_id" field (patches are the pending patch records).
_SYMBOL_ID_SECTIONS = ("inferences", "flags", "patches")


# ── Artifact meta / startup hint ──


def read_artifact_meta(artifact_dir: str) -> dict | None:
    """Read the recorded ROOT and ID_SCHEME from artifact headers.

    Scans only the first few lines of project.aleph.map (falling back to
    project.aleph.dict) — cheap enough to run at every engine startup.
    Returns ``{"root": str | None, "id_scheme": int | None}`` or None when
    no artifacts exist. A missing ID_SCHEME line means the artifacts were
    written before the line existed, i.e. scheme v1.
    """
    for name in ("project.aleph.map", DICT_FILENAME):
        path = os.path.join(artifact_dir, name)
        if not os.path.isfile(path):
            continue
        root: str | None = None
        scheme: int | None = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                for _ in range(8):  # headers precede the first section
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line.startswith("[ROOT:") and line.endswith("]"):
                        root = line[len("[ROOT:"):-1]
                    elif line.startswith("[ID_SCHEME:") and line.endswith("]"):
                        try:
                            scheme = int(line[len("[ID_SCHEME:"):-1])
                        except ValueError:
                            pass
        except (OSError, UnicodeDecodeError):
            # Unreadable/corrupt artifact — let the real loaders report it.
            continue
        if root is not None or scheme is not None:
            return {"root": root, "id_scheme": scheme}
    return None


# Hint at most once per artifact dir per process — engines are constructed
# freely (CLI, MCP, tests) and the hint should not become log spam.
_HINTED_DIRS: set[str] = set()


@dataclass
class StaleArtifacts:
    """Why artifacts look stale: a root move/case change or an old scheme."""

    old_root: str | None  # recorded [ROOT:...] line (None if absent)
    id_scheme: int  # recorded scheme (1 when the stamp predates the line)
    reason: str  # "case" | "location" | "scheme"


def detect_stale_artifacts(
    project_dir: str, artifact_dir: str | None = None
) -> StaleArtifacts | None:
    """Detect artifacts built at a different root or under an old ID scheme.

    The single source of truth for the staleness condition: the recorded
    ROOT differs from the live root (by case or location), or the recorded
    ID scheme predates ID_SCHEME_VERSION. Kept cheap: a header read plus
    string compares against os.path.abspath/realpath of the live root.
    Returns None when there are no artifacts or nothing looks stale.
    """
    artifact_dir = artifact_dir or resolve_artifact_dir(project_dir)
    meta = read_artifact_meta(artifact_dir)
    if meta is None:  # no built artifacts — nothing to migrate
        return None

    live_abs = os.path.abspath(project_dir)
    live_real = os.path.realpath(project_dir)
    recorded = meta.get("root")
    scheme = meta.get("id_scheme") or 1

    if recorded and recorded not in (live_abs, live_real):
        reason = (
            "case"
            if recorded.lower() in (live_abs.lower(), live_real.lower())
            else "location"
        )
        return StaleArtifacts(old_root=recorded, id_scheme=scheme, reason=reason)
    if scheme < ID_SCHEME_VERSION:
        return StaleArtifacts(old_root=recorded, id_scheme=scheme, reason="scheme")
    return None


def maybe_hint_migration(project_dir: str, artifact_dir: str | None = None) -> str | None:
    """Print a one-line migrate-ids hint when artifacts look stale.

    Triggers on the :func:`detect_stale_artifacts` condition. Returns the
    hint (also printed to stderr) or None.
    """
    artifact_dir = artifact_dir or resolve_artifact_dir(project_dir)
    if artifact_dir in _HINTED_DIRS:
        return None
    if read_artifact_meta(artifact_dir) is None:  # no built artifacts
        return None
    _HINTED_DIRS.add(artifact_dir)

    stale = detect_stale_artifacts(project_dir, artifact_dir)
    if stale is None:
        return None
    if stale.reason == "scheme":
        hint = (
            f"[aleph] Artifacts use symbol-ID scheme v{stale.id_scheme} "
            f"(current v{ID_SCHEME_VERSION}) — run `aleph migrate-ids "
            f"{project_dir}`, then rebuild."
        )
    else:
        hint = (
            f"[aleph] Artifact ROOT '{stale.old_root}' differs from live root "
            f"'{os.path.realpath(project_dir)}' ({stale.reason} change) — "
            f"symbol IDs may be stale; run `aleph migrate-ids {project_dir}`."
        )
    print(hint, file=sys.stderr)
    return hint


def auto_migrate_ids(project_dir: str, artifact_dir: str | None = None) -> bool:
    """Self-heal artifacts that moved roots (or predate the ID scheme).

    Serve-startup counterpart to :func:`maybe_hint_migration`: when
    :func:`detect_stale_artifacts` fires, runs :func:`migrate_ids` —
    carrying the epistemic state's symbol references to the current
    scheme — and then a full rebuild so every artifact (ROOT line, db,
    bodies) matches the live root, i.e. the "then rebuild" half of the
    manual procedure. Idempotent: after a successful heal the detection
    no longer fires, so the next startup is a no-op.

    Fail-soft by design: any error prints the failure plus the manual
    migrate-ids hint and returns False — a degraded server beats a dead
    one. Disable with ALEPH_AUTO_MIGRATE=0 (falls back to the hint).
    Case-only root drift under the current scheme hints instead of
    healing (see inline comment). Returns True when a migration ran and
    completed.
    """
    artifact_dir = artifact_dir or resolve_artifact_dir(project_dir)
    try:
        if os.environ.get("ALEPH_AUTO_MIGRATE", "").lower() in ("false", "0", "no"):
            maybe_hint_migration(project_dir, artifact_dir)
            return False
        stale = detect_stale_artifacts(project_dir, artifact_dir)
        if stale is None:
            return False
        if stale.reason == "case" and stale.id_scheme >= ID_SCHEME_VERSION:
            # Case-only drift (e.g. ~/Repos/Aleph vs ~/repos/aleph on a
            # case-insensitive filesystem): v2 IDs hash the lowercased
            # relative path, so nothing is actually stale — and healing
            # would rewrite the ROOT line to whichever case launched last,
            # so alternating launch paths would trigger a full
            # migrate+rebuild on every boot. Hint only.
            maybe_hint_migration(project_dir, artifact_dir)
            return False

        new_root = os.path.abspath(project_dir)
        if stale.reason == "scheme" and stale.old_root in (None, new_root):
            print(
                f"[aleph] artifacts use symbol-ID scheme v{stale.id_scheme} "
                f"— auto-migrating ids to v{ID_SCHEME_VERSION}",
                file=sys.stderr,
            )
        else:
            print(
                f"[aleph] artifacts built at {stale.old_root} — "
                f"auto-migrating ids to {new_root}",
                file=sys.stderr,
            )
        report = migrate_ids(project_dir, dry_run=False, old_root=stale.old_root)

        # Rebuild re-emits map/dict/struct/bodies/db under the live root and
        # current scheme, healing full-body expand and the stale detection
        # itself (heavy import kept local, like compute_id_mapping's).
        from aleph.pipeline import auto_build

        auto_build(project_dir, full=True)
        print(
            f"[aleph] auto-migrate complete: {len(report.plan.changed)} id(s) "
            f"remapped, {report.rewritten_refs} epistemic ref(s) rewritten, "
            f"artifacts rebuilt",
            file=sys.stderr,
        )
        _HINTED_DIRS.add(artifact_dir)  # healed — suppress the stale hint
        return True
    except Exception as e:  # never let self-heal kill the server
        print(f"[aleph] auto-migrate failed: {e}", file=sys.stderr)
        maybe_hint_migration(project_dir, artifact_dir)
        return False


# ── Mapping computation ──


@dataclass
class MigrationPlan:
    """Old->new ID mapping derived from the live sources + artifact ROOT."""

    old_root: str
    new_root: str
    mapping: dict[str, str] = field(default_factory=dict)  # all derived pairs
    names: dict[str, str] = field(default_factory=dict)  # old_id -> qualified name
    matched_dict_ids: int = 0  # derived old IDs present in the dict artifact
    unmatched_dict_ids: list[str] = field(default_factory=list)  # not re-derivable
    missing_files: list[str] = field(default_factory=list)

    @property
    def changed(self) -> dict[str, str]:
        return {o: n for o, n in self.mapping.items() if o != n}


def compute_id_mapping(project_dir: str, old_root: str | None = None) -> MigrationPlan:
    """Re-derive v1 and v2 IDs for every file recorded in the dictionary.

    The dict artifact does not store signature text (only an 8-hex digest),
    so the live source files are re-parsed to recover the full hash inputs.
    Old IDs are computed as if each file lived under *old_root* (default:
    the artifact's ROOT line); new IDs use the live project root.
    """
    # Heavy imports kept local: maybe_hint_migration must stay cheap to
    # import from the query engine.
    from aleph.emit.loader import AlephLoader
    from aleph.ingest.parser import TreeSitterParser
    from aleph.symbols.extractor import SymbolExtractor
    from aleph.symbols.registry import SymbolRegistry

    artifact_dir = resolve_artifact_dir(project_dir)
    dict_path = os.path.join(artifact_dir, DICT_FILENAME)
    if not os.path.isfile(dict_path):
        raise FileNotFoundError(
            f"No {DICT_FILENAME} found under {project_dir} — build the project first."
        )
    with open(dict_path, "r", encoding="utf-8") as f:
        component = AlephLoader().deserialize_project_dict(f.read())

    new_root = os.path.abspath(project_dir)
    old_root = old_root or component.root or new_root
    plan = MigrationPlan(old_root=old_root, new_root=new_root)

    dict_ids = {s.symbol_id for s in component.symbols}
    dict_names = {s.symbol_id: s.qualified_name for s in component.symbols}
    rel_files = sorted({s.file for s in component.symbols if s.file})

    parser = TreeSitterParser()
    extractor = SymbolExtractor()

    for rel in rel_files:
        live_path = os.path.join(new_root, rel)
        if not os.path.isfile(live_path):
            plan.missing_files.append(rel)
            continue
        try:
            tree, source, language = parser.parse_file(live_path)
        except Exception:
            plan.missing_files.append(rel)
            continue
        raws = extractor.extract(tree, source, language, source_file=live_path)

        # Parallel registries reproduce per-file dedup and collision
        # auto-extension under each scheme.
        old_path = os.path.join(old_root, rel)
        old_registry = SymbolRegistry()  # legacy v1: verbatim (absolute) path
        new_registry = SymbolRegistry(project_root=new_root)  # portable v2
        for raw in raws:
            old_sym = old_registry.register(replace(raw, source_file=old_path))
            new_sym = new_registry.register(raw)
            old_id, new_id = str(old_sym.id), str(new_sym.id)
            plan.mapping[old_id] = new_id
            plan.names[old_id] = raw.qualified_name

    derived = set(plan.mapping)
    plan.matched_dict_ids = len(dict_ids & derived)
    plan.unmatched_dict_ids = sorted(dict_ids - derived)
    # Prefer the artifact's qualified names for reporting where available.
    for old_id in plan.mapping:
        if old_id in dict_names:
            plan.names[old_id] = dict_names[old_id]
    return plan


# ── Applying the mapping ──


def _remap_epistemic_data(data: dict, changed: dict[str, str]) -> int:
    """Rewrite symbol-ID references in epistemic data in place.

    Covers inference/flag/pending-patch records (symbol_id fields) plus the
    symbol tables of session memories and review trails. Returns the number
    of rewritten references.
    """
    rewritten = 0
    for section in _SYMBOL_ID_SECTIONS:
        for entry in data.get(section, []):
            if isinstance(entry, dict):
                old = entry.get("symbol_id")
                if old in changed:
                    entry["symbol_id"] = changed[old]
                    rewritten += 1

    def _remap_keys(table: dict) -> dict:
        nonlocal rewritten
        out = {}
        for key, value in table.items():
            if key in changed:
                out[changed[key]] = value
                rewritten += 1
            else:
                out[key] = value
        return out

    for memory in data.get("memories", []):
        if isinstance(memory, dict) and isinstance(memory.get("symbol_dict"), dict):
            memory["symbol_dict"] = _remap_keys(memory["symbol_dict"])
    for review in data.get("reviewed", []):
        if isinstance(review, dict) and isinstance(review.get("symbols"), dict):
            review["symbols"] = _remap_keys(review["symbols"])
    return rewritten


@dataclass
class MigrationReport:
    plan: MigrationPlan
    dry_run: bool
    rewritten_refs: int = 0
    stores_updated: list[str] = field(default_factory=list)

    def summary(self, samples: int = 5) -> str:
        plan = self.plan
        changed = plan.changed
        lines = [
            f"Symbol-ID migration (scheme v1 -> v{ID_SCHEME_VERSION})",
            f"  old root: {plan.old_root}",
            f"  new root: {plan.new_root}",
            f"  symbols derived: {len(plan.mapping)} "
            f"(changed: {len(changed)}, unchanged: {len(plan.mapping) - len(changed)})",
            f"  dictionary IDs matched: {plan.matched_dict_ids}",
        ]
        if plan.unmatched_dict_ids:
            shown = ", ".join(plan.unmatched_dict_ids[:samples])
            lines.append(
                f"  dictionary IDs NOT re-derivable (file changed since build): "
                f"{len(plan.unmatched_dict_ids)} [{shown}...]"
                if len(plan.unmatched_dict_ids) > samples
                else f"  dictionary IDs NOT re-derivable (file changed since build): "
                f"{len(plan.unmatched_dict_ids)} [{shown}]"
            )
        if plan.missing_files:
            lines.append(f"  missing/unparseable files skipped: {len(plan.missing_files)}")
        if changed:
            lines.append("  sample mapping:")
            for old_id, new_id in list(changed.items())[:samples]:
                name = plan.names.get(old_id, "")
                lines.append(f"    {old_id} -> {new_id}  {name}")
        if self.dry_run:
            lines.append("  dry-run: no changes written")
        else:
            stores = ", ".join(self.stores_updated) or "none"
            lines.append(
                f"  epistemic references rewritten: {self.rewritten_refs} ({stores})"
            )
            lines.append(
                "  next: rebuild artifacts (`aleph build <root> --full`) to "
                "re-emit map/dict/struct under the new scheme"
            )
        return "\n".join(lines)


def migrate_ids(
    project_dir: str,
    dry_run: bool = False,
    old_root: str | None = None,
) -> MigrationReport:
    """Compute the v1->v2 ID mapping and rewrite epistemic state.

    Idempotent: identity pairs are skipped and v2 IDs never appear as
    mapping keys, so re-running after a successful migration rewrites
    nothing. With *dry_run* the mapping is computed but nothing is written.
    """
    plan = compute_id_mapping(project_dir, old_root=old_root)
    report = MigrationReport(plan=plan, dry_run=dry_run)
    if dry_run:
        return report

    changed = plan.changed
    artifact_dir = resolve_artifact_dir(project_dir)
    candidates = [os.path.join(artifact_dir, EPISTEMIC_FILENAME)]
    legacy = os.path.join(project_dir, LEGACY_EPISTEMIC_FILENAME)
    if os.path.abspath(legacy) not in (os.path.abspath(c) for c in candidates):
        candidates.append(legacy)

    for path in candidates:
        if not os.path.isfile(path) or not changed:
            continue
        store = EpistemicStore(path)
        with store.transaction() as data:
            count = _remap_epistemic_data(data, changed)
        if count:
            report.rewritten_refs += count
            report.stores_updated.append(os.path.basename(path))
    return report
