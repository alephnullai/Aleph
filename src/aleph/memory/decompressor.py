"""Reconstruct a session-resume summary from a compressed memory object.

Must preserve enough fidelity for 90%+ task success rate on resume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from aleph.memory.compressor import CompressedMemory, MemoryEntry
from aleph.memory.formats import (
    MEMORY_HEADER,
    DICT_TAG,
    DICT_END,
    CONTEXT_TAG,
    CONTEXT_END,
    DECISIONS_TAG,
    DECISIONS_END,
    CONCLUSIONS_TAG,
    CONCLUSIONS_END,
    OPEN_QUESTIONS_TAG,
    OPEN_QUESTIONS_END,
    CODE_CHANGES_TAG,
    CODE_CHANGES_END,
    ERRORS_TAG,
    ERRORS_END,
    STATS_TAG,
    STATS_END,
)


@dataclass
class ResumeContext:
    """A reconstructed session context suitable for injecting into a new session."""
    context_summary: str = ""
    decisions: list[str] = field(default_factory=list)
    conclusions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    code_changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    symbol_dict: dict[str, str] = field(default_factory=dict)

    def to_prompt(self) -> str:
        """Generate a session-resume prompt suitable for LLM injection."""
        lines = ["## Prior Session State (Aleph Memory)"]
        lines.append("")

        if self.context_summary:
            lines.append(f"**Context:** {self.context_summary}")
            lines.append("")

        if self.decisions:
            lines.append("### Decisions Made")
            for d in self.decisions:
                lines.append(f"- {d}")
            lines.append("")

        if self.conclusions:
            lines.append("### Key Learnings")
            for c in self.conclusions:
                lines.append(f"- {c}")
            lines.append("")

        if self.code_changes:
            lines.append("### Code Changes")
            for ch in self.code_changes:
                lines.append(f"- {ch}")
            lines.append("")

        if self.errors:
            lines.append("### Errors Encountered")
            for e in self.errors:
                lines.append(f"- {e}")
            lines.append("")

        if self.open_questions:
            lines.append("### Open Questions (Unresolved)")
            for q in self.open_questions:
                lines.append(f"- {q}")
            lines.append("")

        return "\n".join(lines)


def _expand_symbols(text: str, symbol_dict: dict[str, str]) -> str:
    """Replace symbol IDs with their full names."""
    expanded = text
    for sid, name in sorted(
        symbol_dict.items(), key=lambda x: len(x[0]), reverse=True
    ):
        expanded = expanded.replace(sid, name)
    return expanded


def _strip_metadata(text: str) -> str:
    """Strip confidence scores and ref markers from entry text."""
    # Remove refs=... suffixes first (may appear after confidence)
    text = re.sub(r"\s*refs=\S+", "", text)
    # Remove trailing [0.8] confidence markers
    text = re.sub(r"\s*\[\d+\.\d+\]", "", text)
    return text.strip()


def decompress_memory(memory: CompressedMemory) -> ResumeContext:
    """Reconstruct a ResumeContext from a CompressedMemory object."""
    ctx = ResumeContext(
        context_summary=memory.context_summary,
        symbol_dict=dict(memory.symbol_dict),
    )

    for entry in memory.entries:
        # Expand symbols back to human-readable
        expanded = _expand_symbols(entry.content, memory.symbol_dict)
        expanded = _strip_metadata(expanded)

        if entry.category == "decision":
            ctx.decisions.append(expanded)
        elif entry.category == "conclusion":
            ctx.conclusions.append(expanded)
        elif entry.category == "open_question":
            ctx.open_questions.append(expanded)
        elif entry.category == "code_change":
            ctx.code_changes.append(expanded)
        elif entry.category == "error":
            ctx.errors.append(expanded)

    return ctx


def decompress_from_text(text: str) -> ResumeContext:
    """Parse the [ALEPH:MEMORY:1.0] text format and produce a ResumeContext."""
    memory = parse_memory_text(text)
    return decompress_memory(memory)


def parse_memory_text(text: str) -> CompressedMemory:
    """Parse the serialized [ALEPH:MEMORY:1.0] format back into a CompressedMemory."""
    lines = text.splitlines()
    symbol_dict: dict[str, str] = {}
    entries: list[MemoryEntry] = []
    context_summary = ""
    original_tokens = 0
    compressed_tokens = 0
    message_count = 0

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line == STATS_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != STATS_END:
                stat_line = lines[i].strip()
                if stat_line.startswith("messages="):
                    message_count = int(stat_line.split("=", 1)[1])
                elif stat_line.startswith("original_tokens="):
                    original_tokens = int(stat_line.split("=", 1)[1])
                elif stat_line.startswith("compressed_tokens="):
                    compressed_tokens = int(stat_line.split("=", 1)[1])
                i += 1

        elif line == CONTEXT_TAG:
            i += 1
            ctx_lines: list[str] = []
            while i < len(lines) and lines[i].strip() != CONTEXT_END:
                ctx_lines.append(lines[i])
                i += 1
            context_summary = "\n".join(ctx_lines).strip()

        elif line == DICT_TAG:
            i += 1
            while i < len(lines) and lines[i].strip() != DICT_END:
                dict_line = lines[i].strip()
                if "=" in dict_line:
                    sid, name = dict_line.split("=", 1)
                    symbol_dict[sid] = name
                i += 1

        elif line in (
            DECISIONS_TAG,
            CONCLUSIONS_TAG,
            OPEN_QUESTIONS_TAG,
            CODE_CHANGES_TAG,
            ERRORS_TAG,
        ):
            category_map = {
                DECISIONS_TAG: ("decision", DECISIONS_END),
                CONCLUSIONS_TAG: ("conclusion", CONCLUSIONS_END),
                OPEN_QUESTIONS_TAG: ("open_question", OPEN_QUESTIONS_END),
                CODE_CHANGES_TAG: ("code_change", CODE_CHANGES_END),
                ERRORS_TAG: ("error", ERRORS_END),
            }
            category, end_tag = category_map[line]
            i += 1
            while i < len(lines) and lines[i].strip() != end_tag:
                entry_line = lines[i].strip()
                if entry_line:
                    # Parse confidence
                    conf_match = re.search(r"\[(\d+\.\d+)\]", entry_line)
                    confidence = float(conf_match.group(1)) if conf_match else 0.8
                    # Parse refs
                    refs: list[str] = []
                    refs_match = re.search(r"refs=(\S+)", entry_line)
                    if refs_match:
                        refs = refs_match.group(1).split()
                    # Clean content
                    content = re.sub(r"\s*\[\d+\.\d+\]", "", entry_line)
                    content = re.sub(r"\s*refs=\S+", "", content).strip()
                    entries.append(
                        MemoryEntry(
                            category=category,
                            content=content,
                            confidence=confidence,
                            symbol_refs=refs,
                        )
                    )
                i += 1

        i += 1

    return CompressedMemory(
        entries=entries,
        symbol_dict=symbol_dict,
        context_summary=context_summary,
        original_token_estimate=original_tokens,
        compressed_token_estimate=compressed_tokens,
        message_count=message_count,
    )
