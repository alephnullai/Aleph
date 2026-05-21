"""Resume briefing artifact — a fast-load session summary.

Generates project.aleph.resume from the epistemic store:
  - Top 10 inferences by confidence
  - All unverified flags
  - All pending patches
  - Key learned conclusions and decisions from compressed memories

This is the lightweight artifact for session start — not the full epistemic store.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from aleph.memory.formats import (
    RESUME_HEADER,
    RESUME_INFERENCES_TAG,
    RESUME_INFERENCES_END,
    RESUME_FLAGS_TAG,
    RESUME_FLAGS_END,
    RESUME_PATCHES_TAG,
    RESUME_PATCHES_END,
    RESUME_LEARNED_TAG,
    RESUME_LEARNED_END,
    RESUME_DECISIONS_TAG,
    RESUME_DECISIONS_END,
    CONTEXT_TAG,
    CONTEXT_END,
)


@dataclass
class InferenceBrief:
    """A single inference for the briefing."""
    symbol_id: str
    conclusion: str
    confidence: float

    def to_line(self) -> str:
        return f"{self.symbol_id} [{self.confidence:.4f}] {self.conclusion}"


@dataclass
class FlagBrief:
    """A single flag for the briefing."""
    symbol_id: str
    reason: str
    verified: bool = False

    def to_line(self) -> str:
        status = " [VERIFIED]" if self.verified else ""
        return f"{self.symbol_id} {self.reason}{status}"


@dataclass
class PatchBrief:
    """A single pending patch for the briefing."""
    patch_id: str
    symbol_id: str
    intent: str

    def to_line(self) -> str:
        return f"{self.patch_id} {self.symbol_id} {self.intent}"


@dataclass
class ResumeBriefing:
    """The fast-load session briefing artifact."""
    context_summary: str = ""
    inferences: list[InferenceBrief] = field(default_factory=list)
    flags: list[FlagBrief] = field(default_factory=list)
    patches: list[PatchBrief] = field(default_factory=list)
    learned: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)

    def serialize(self) -> str:
        """Serialize to [ALEPH:RESUME:1.0] text format."""
        lines = [RESUME_HEADER]

        if self.context_summary:
            lines.append(CONTEXT_TAG)
            lines.append(self.context_summary)
            lines.append(CONTEXT_END)

        if self.inferences:
            lines.append(RESUME_INFERENCES_TAG)
            for inf in self.inferences:
                lines.append(inf.to_line())
            lines.append(RESUME_INFERENCES_END)

        if self.flags:
            lines.append(RESUME_FLAGS_TAG)
            for fl in self.flags:
                lines.append(fl.to_line())
            lines.append(RESUME_FLAGS_END)

        if self.patches:
            lines.append(RESUME_PATCHES_TAG)
            for p in self.patches:
                lines.append(p.to_line())
            lines.append(RESUME_PATCHES_END)

        if self.decisions:
            lines.append(RESUME_DECISIONS_TAG)
            for d in self.decisions:
                lines.append(d)
            lines.append(RESUME_DECISIONS_END)

        if self.learned:
            lines.append(RESUME_LEARNED_TAG)
            for l in self.learned:
                lines.append(l)
            lines.append(RESUME_LEARNED_END)

        return "\n".join(lines) + "\n"

    def to_prompt(self) -> str:
        """Generate a markdown session-resume prompt for LLM injection."""
        lines = ["## Session Briefing (Aleph Resume)"]
        lines.append("")

        if self.context_summary:
            lines.append(f"**Context:** {self.context_summary}")
            lines.append("")

        if self.inferences:
            lines.append("### Prior Inferences (by confidence)")
            for inf in self.inferences:
                lines.append(f"- **{inf.symbol_id}** [{inf.confidence}]: {inf.conclusion}")
            lines.append("")

        if self.flags:
            lines.append("### Flags (needs verification)")
            for fl in self.flags:
                status = " (verified)" if fl.verified else ""
                lines.append(f"- **{fl.symbol_id}**: {fl.reason}{status}")
            lines.append("")

        if self.patches:
            lines.append("### Pending Patches")
            for p in self.patches:
                lines.append(f"- **{p.patch_id}** ({p.symbol_id}): {p.intent}")
            lines.append("")

        if self.decisions:
            lines.append("### Key Decisions")
            for d in self.decisions:
                lines.append(f"- {d}")
            lines.append("")

        if self.learned:
            lines.append("### Key Learnings")
            for l in self.learned:
                lines.append(f"- {l}")
            lines.append("")

        return "\n".join(lines)


def parse_briefing(text: str) -> ResumeBriefing:
    """Parse a serialized [ALEPH:RESUME:1.0] text back into a ResumeBriefing."""
    import re

    briefing = ResumeBriefing()
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line == CONTEXT_TAG:
            i += 1
            ctx_lines = []
            while i < len(lines) and lines[i].strip() != CONTEXT_END:
                ctx_lines.append(lines[i].strip())
                i += 1
            briefing.context_summary = "\n".join(ctx_lines).strip()

        elif line == RESUME_INFERENCES_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != RESUME_INFERENCES_END:
                inf_line = lines[i].strip()
                if inf_line:
                    match = re.match(
                        r"(\S+)\s+\[(\d+(?:\.\d+)?)\]\s+(.*)", inf_line
                    )
                    if match:
                        briefing.inferences.append(InferenceBrief(
                            symbol_id=match.group(1),
                            confidence=float(match.group(2)),
                            conclusion=match.group(3),
                        ))
                i += 1

        elif line == RESUME_FLAGS_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != RESUME_FLAGS_END:
                flag_line = lines[i].strip()
                if flag_line:
                    verified = flag_line.endswith("[VERIFIED]")
                    clean = flag_line.replace(" [VERIFIED]", "").strip()
                    parts = clean.split(None, 1)
                    if len(parts) == 2:
                        briefing.flags.append(FlagBrief(
                            symbol_id=parts[0],
                            reason=parts[1],
                            verified=verified,
                        ))
                i += 1

        elif line == RESUME_PATCHES_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != RESUME_PATCHES_END:
                patch_line = lines[i].strip()
                if patch_line:
                    parts = patch_line.split(None, 2)
                    if len(parts) >= 3:
                        briefing.patches.append(PatchBrief(
                            patch_id=parts[0],
                            symbol_id=parts[1],
                            intent=parts[2],
                        ))
                i += 1

        elif line == RESUME_DECISIONS_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != RESUME_DECISIONS_END:
                d_line = lines[i].strip()
                if d_line:
                    briefing.decisions.append(d_line)
                i += 1

        elif line == RESUME_LEARNED_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != RESUME_LEARNED_END:
                l_line = lines[i].strip()
                if l_line:
                    briefing.learned.append(l_line)
                i += 1

        i += 1

    return briefing


def generate_briefing(project_dir: str) -> ResumeBriefing:
    """Generate a resume briefing from the project's epistemic store.

    Reads project.aleph.epistemic and extracts:
      - Top 10 inferences by confidence
      - All flags (verified and unverified)
      - All pending patches
      - Key conclusions and decisions from compressed memories
    """
    from aleph.memory.session_memory import _load_epistemic

    data = _load_epistemic(project_dir)
    briefing = ResumeBriefing()

    # Extract inferences, sorted by confidence (top 10)
    inferences = data.get("inferences", [])
    inferences_sorted = sorted(
        inferences, key=lambda x: x.get("confidence", 0), reverse=True
    )
    for inf in inferences_sorted[:10]:
        briefing.inferences.append(InferenceBrief(
            symbol_id=inf.get("symbol_id", "?"),
            conclusion=inf.get("conclusion", ""),
            confidence=inf.get("confidence", 0.0),
        ))

    # Extract all flags
    for fl in data.get("flags", []):
        briefing.flags.append(FlagBrief(
            symbol_id=fl.get("symbol_id", "?"),
            reason=fl.get("reason", ""),
            verified=fl.get("verified", False),
        ))

    # Extract pending patches
    for p in data.get("patches", []):
        if p.get("status") == "pending":
            briefing.patches.append(PatchBrief(
                patch_id=p.get("patch_id", "?"),
                symbol_id=p.get("symbol_id", "?"),
                intent=p.get("intent", ""),
            ))

    # Extract conclusions and decisions from compressed memories
    memories = data.get("memories", [])
    if memories:
        latest = memories[-1]
        # Set context summary from latest memory
        briefing.context_summary = latest.get("context_summary", "")

        for entry in latest.get("entries", []):
            category = entry.get("category", "")
            content = entry.get("content", "")
            if not content:
                continue
            # Expand symbols back to readable form
            symbol_dict = latest.get("symbol_dict", {})
            for sid, name in sorted(
                symbol_dict.items(), key=lambda x: len(x[0]), reverse=True
            ):
                content = content.replace(sid, name)
            if category == "conclusion":
                briefing.learned.append(content)
            elif category == "decision":
                briefing.decisions.append(content)

    return briefing


def write_briefing(project_dir: str, briefing: ResumeBriefing) -> str:
    """Write the briefing to project.aleph.resume in the artifact directory."""
    aleph_dir = os.path.join(project_dir, ".aleph")
    if os.path.isdir(aleph_dir):
        path = os.path.join(aleph_dir, "project.aleph.resume")
    else:
        path = os.path.join(project_dir, "project.aleph.resume")

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(briefing.serialize())
    return path


def load_briefing(project_dir: str) -> ResumeBriefing | None:
    """Load a previously written briefing from project.aleph.resume."""
    for base in [
        os.path.join(project_dir, ".aleph", "project.aleph.resume"),
        os.path.join(project_dir, "project.aleph.resume"),
    ]:
        if os.path.isfile(base):
            with open(base, "r", encoding="utf-8") as f:
                return parse_briefing(f.read())
    return None
