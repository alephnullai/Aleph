"""Semantic diff engine: report meaning changes, not line changes."""

from __future__ import annotations

from dataclasses import dataclass, field

from aleph.util.hashing import byte_hash


@dataclass
class SemanticDiffReport:
    """Report of semantic-level changes between two versions of a file."""
    semantic_hash_changed: bool = False
    previous_hash: str = ""
    current_hash: str = ""
    symbols_added: list[str] = field(default_factory=list)
    symbols_removed: list[str] = field(default_factory=list)
    signatures_changed: list[str] = field(default_factory=list)
    calls_added: list[tuple[str, str]] = field(default_factory=list)
    calls_removed: list[tuple[str, str]] = field(default_factory=list)
    bodies_changed: list[str] = field(default_factory=list)
    intents_changed: bool = False
    errors_changed: bool = False
    coverage_changed: bool = False

    def to_dict(self) -> dict:
        return {
            "semantic_hash_changed": self.semantic_hash_changed,
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "symbols_added": self.symbols_added,
            "symbols_removed": self.symbols_removed,
            "signatures_changed": self.signatures_changed,
            "calls_added": [list(c) for c in self.calls_added],
            "calls_removed": [list(c) for c in self.calls_removed],
            "bodies_changed": self.bodies_changed,
            "intents_changed": self.intents_changed,
            "errors_changed": self.errors_changed,
            "coverage_changed": self.coverage_changed,
        }


class SemanticDiff:
    """Compare an indexed file entry against current pipeline results."""

    def diff(self, old_index_entry: dict | None, new_result: dict) -> SemanticDiffReport:
        """Produce a semantic diff report.

        Args:
            old_index_entry: Previous index entry for the file (may be None).
            new_result: Current run_pipeline() result dict.
        """
        report = SemanticDiffReport()
        report.current_hash = new_result.get("semantic_hash", "")

        if not old_index_entry:
            report.previous_hash = ""
            report.semantic_hash_changed = True
            # All current symbols are "added"
            report.symbols_added = [
                str(s.id) for s in new_result.get("symbols", [])
            ]
            return report

        report.previous_hash = old_index_entry.get("semantic_hash", "")
        report.semantic_hash_changed = report.previous_hash != report.current_hash

        # Symbol comparison
        old_syms = {s["id"]: s for s in old_index_entry.get("symbols", [])}
        new_syms = {str(s.id): s for s in new_result.get("symbols", [])}

        old_ids = set(old_syms.keys())
        new_ids = set(new_syms.keys())

        report.symbols_added = sorted(new_ids - old_ids)
        report.symbols_removed = sorted(old_ids - new_ids)

        # Signature changes (compare signature hashes from index)
        old_sig_hashes = old_index_entry.get("signature_hashes", {})
        if old_sig_hashes:
            for sid in old_ids & new_ids:
                new_sym = new_syms.get(sid)
                if new_sym:
                    new_sig_hash = byte_hash(new_sym.raw.signature_text)[:8]
                    old_sig_hash = old_sig_hashes.get(sid, "")
                    if old_sig_hash and new_sig_hash != old_sig_hash:
                        report.signatures_changed.append(sid)

        # Body changes (compare body hashes from index)
        old_body_hashes = old_index_entry.get("body_hashes", {})
        if old_body_hashes:
            for sid in old_ids & new_ids:
                new_sym = new_syms.get(sid)
                if new_sym:
                    new_body_hash = byte_hash(new_sym.raw.body_text)[:8]
                    old_body_hash = old_body_hashes.get(sid, "")
                    if old_body_hash and new_body_hash != old_body_hash:
                        report.bodies_changed.append(sid)

        # Call edge comparison
        old_calls = set(tuple(c) for c in old_index_entry.get("calls", []))
        new_calls = set(new_result.get("struct_component").call_edges) if new_result.get("struct_component") else set()
        report.calls_added = sorted(new_calls - old_calls)
        report.calls_removed = sorted(old_calls - new_calls)

        # Component-level change flags
        # These are simple: if the semantic hash changed, we mark them as potentially changed
        # A more precise check would require storing component hashes in the index
        if report.semantic_hash_changed:
            report.intents_changed = True
            report.errors_changed = True
            report.coverage_changed = True

        return report
