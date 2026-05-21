"""Manage loading/saving compressed memory to project.aleph.epistemic.

Integrates memory compression with the existing epistemic layer:
- Stores compressed session memories alongside inferences and flags
- Supports multiple session memories (one per conversation)
- Loads prior memory for session resume
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from aleph.memory.compressor import CompressedMemory, MemoryEntry, serialize_memory
from aleph.memory.decompressor import ResumeContext, decompress_memory


def _default_session_id() -> str:
    """Generate a session ID from the current timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _memory_to_dict(memory: CompressedMemory, session_id: str) -> dict[str, Any]:
    """Convert a CompressedMemory to a JSON-serializable dict."""
    return {
        "session_id": session_id,
        "timestamp": _default_session_id(),
        "message_count": memory.message_count,
        "original_tokens": memory.original_token_estimate,
        "compressed_tokens": memory.compressed_token_estimate,
        "reduction_percent": round(memory.reduction_percent, 1),
        "context_summary": memory.context_summary,
        "symbol_dict": memory.symbol_dict,
        "entries": [
            {
                "category": e.category,
                "content": e.content,
                "confidence": e.confidence,
                "source_turn": e.source_turn,
                "symbol_refs": e.symbol_refs,
            }
            for e in memory.entries
        ],
    }


def _dict_to_memory(data: dict[str, Any]) -> CompressedMemory:
    """Reconstruct a CompressedMemory from a stored dict."""
    entries = [
        MemoryEntry(
            category=e["category"],
            content=e["content"],
            confidence=e.get("confidence", 0.8),
            source_turn=e.get("source_turn", 0),
            symbol_refs=e.get("symbol_refs", []),
        )
        for e in data.get("entries", [])
    ]
    return CompressedMemory(
        entries=entries,
        symbol_dict=data.get("symbol_dict", {}),
        context_summary=data.get("context_summary", ""),
        original_token_estimate=data.get("original_tokens", 0),
        compressed_token_estimate=data.get("compressed_tokens", 0),
        message_count=data.get("message_count", 0),
    )


def _epistemic_path(project_dir: str) -> str:
    """Resolve the path to project.aleph.epistemic.

    Uses .aleph/ subdirectory only if it contains project.aleph.dict
    (i.e., a build has been run). This matches the logic in handlers.py
    and query/engine.py to prevent split-brain writes.
    """
    aleph_dir = os.path.join(project_dir, ".aleph")
    if os.path.isdir(aleph_dir) and os.path.isfile(
        os.path.join(aleph_dir, "project.aleph.dict")
    ):
        return os.path.join(aleph_dir, "project.aleph.epistemic")
    return os.path.join(project_dir, "project.aleph.epistemic")


def _load_epistemic(project_dir: str) -> dict[str, Any]:
    """Load the epistemic file, returning empty structure if absent."""
    path = _epistemic_path(project_dir)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_epistemic(project_dir: str, data: dict[str, Any]) -> str:
    """Save the epistemic file, creating directories as needed."""
    path = _epistemic_path(project_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def save_memory(
    project_dir: str,
    memory: CompressedMemory,
    session_id: str | None = None,
) -> str:
    """Save a compressed memory to the project's epistemic file.

    Args:
        project_dir: Project root directory.
        memory: The compressed memory to save.
        session_id: Optional session identifier. Auto-generated if omitted.

    Returns:
        Path to the written epistemic file.
    """
    if session_id is None:
        session_id = _default_session_id()

    data = _load_epistemic(project_dir)
    memories = data.setdefault("memories", [])
    memories.append(_memory_to_dict(memory, session_id))
    path = _save_epistemic(project_dir, data)

    # Also generate and write the resume briefing artifact
    from aleph.memory.briefing import generate_briefing, write_briefing
    briefing = generate_briefing(project_dir)
    write_briefing(project_dir, briefing)

    return path


def load_latest_memory(project_dir: str) -> CompressedMemory | None:
    """Load the most recent compressed memory from the epistemic file.

    Returns None if no memories exist.
    """
    data = _load_epistemic(project_dir)
    memories = data.get("memories", [])
    if not memories:
        return None
    return _dict_to_memory(memories[-1])


def load_all_memories(project_dir: str) -> list[CompressedMemory]:
    """Load all compressed memories from the epistemic file."""
    data = _load_epistemic(project_dir)
    return [_dict_to_memory(m) for m in data.get("memories", [])]


def resume_session(project_dir: str) -> ResumeContext | None:
    """Load the latest memory and produce a session-resume context.

    Returns None if no prior memory exists.
    """
    memory = load_latest_memory(project_dir)
    if memory is None:
        return None
    return decompress_memory(memory)


def resume_session_briefing(project_dir: str) -> "ResumeBriefing | None":
    """Load or generate the resume briefing for session start.

    Prefers the cached project.aleph.resume artifact. Falls back to
    generating from the epistemic store if no artifact exists.
    Returns None if no epistemic data exists.
    """
    from aleph.memory.briefing import load_briefing, generate_briefing, ResumeBriefing

    briefing = load_briefing(project_dir)
    if briefing is not None:
        return briefing

    # No cached briefing — try generating from epistemic store
    data = _load_epistemic(project_dir)
    if not data:
        return None

    return generate_briefing(project_dir)
