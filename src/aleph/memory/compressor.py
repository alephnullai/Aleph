"""Compress raw conversation transcripts into Aleph memory objects.

Takes a list of messages (role/content) and produces a structured
epistemic record: decisions, conclusions, open questions, code changes,
errors encountered — with symbol IDs for recurring concepts.

Target: 60%+ token reduction vs raw transcript.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryEntry:
    """A single extracted item from a conversation."""
    category: str  # decision | conclusion | open_question | code_change | error
    content: str
    confidence: float = 0.8
    source_turn: int = 0  # which message index it came from
    symbol_refs: list[str] = field(default_factory=list)  # symbol IDs referenced


@dataclass
class CompressedMemory:
    """The output of conversation compression."""
    entries: list[MemoryEntry] = field(default_factory=list)
    symbol_dict: dict[str, str] = field(default_factory=dict)  # s_xxxx -> concept name
    context_summary: str = ""
    original_token_estimate: int = 0
    compressed_token_estimate: int = 0
    message_count: int = 0

    @property
    def reduction_percent(self) -> float:
        if self.original_token_estimate == 0:
            return 0.0
        return (1 - self.compressed_token_estimate / self.original_token_estimate) * 100


def _make_symbol_id(name: str) -> str:
    """Generate a stable symbol ID for a recurring concept."""
    h = hashlib.sha256(name.lower().encode()).hexdigest()[:6]
    return f"s_{h}"


def _estimate_tokens(text: str) -> int:
    """Fast token estimate: ~4 chars per token for English text."""
    return max(1, len(text) // 4)


def _extract_entities(messages: list[dict[str, str]]) -> Counter:
    """Find recurring concepts/entities across messages.

    Looks for:
    - Backtick-quoted identifiers (code references) — kept even at 1 occurrence
    - Capitalized multi-word phrases (proper nouns, project names)
    - File names with common extensions
    - Repeated technical terms (snake_case, camelCase)
    """
    entity_counts: Counter = Counter()
    backtick_entities: set[str] = set()
    file_entities: set[str] = set()

    for msg in messages:
        content = msg.get("content", "")

        # Backtick-quoted code references (important enough to keep at 1 occurrence)
        for match in re.finditer(r"`([^`]{2,60})`", content):
            entity = match.group(1)
            entity_counts[entity] += 1
            backtick_entities.add(entity)

        # Capitalized phrases (2+ words)
        for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", content):
            entity_counts[match.group(1)] += 1

        # File names with common extensions (kept at 1 occurrence for completeness)
        for match in re.finditer(
            r"\b([\w.-]+\.(?:py|md|json|js|ts|yaml|yml|toml|cfg|txt|html|css|sh|go|rs))\b",
            content,
        ):
            entity = match.group(1)
            entity_counts[entity] += 1
            file_entities.add(entity)

        # Technical terms: snake_case or camelCase identifiers mentioned multiple times
        for match in re.finditer(r"\b([a-z]+(?:_[a-z]+){1,5})\b", content):
            term = match.group(1)
            if len(term) > 5:
                entity_counts[term] += 1

        for match in re.finditer(r"\b([a-z]+[A-Z][a-zA-Z]+)\b", content):
            entity_counts[match.group(1)] += 1

    # Keep entities that appear 2+ times, backtick-quoted, or file names (completeness)
    return Counter({
        k: v for k, v in entity_counts.items()
        if v >= 2 or k in backtick_entities or k in file_entities
    })


def _classify_message_content(
    content: str, role: str, turn_idx: int
) -> list[MemoryEntry]:
    """Extract structured entries from a single message."""
    entries: list[MemoryEntry] = []

    sentences = re.split(r"(?<=[.!?])\s+", content)

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        lower = sentence.lower()
        is_question = sentence.rstrip().endswith("?")

        # Decision patterns — but NOT if the sentence is a question
        if not is_question and any(
            p in lower
            for p in [
                "decided to",
                "let's go with",
                "we'll use",
                "the plan is",
                "decision:",
                "agreed to",
                "will use",
                "going with",
                "chosen approach",
                "i'll implement",
                "the approach is",
                "recommendation:",
                "recommend:",
            ]
        ):
            entries.append(
                MemoryEntry(
                    category="decision",
                    content=sentence,
                    confidence=0.9,
                    source_turn=turn_idx,
                )
            )
            continue

        # "we should" — only a decision when asserted by assistant, not in a question
        if "we should" in lower and not is_question and role == "assistant":
            entries.append(
                MemoryEntry(
                    category="decision",
                    content=sentence,
                    confidence=0.85,
                    source_turn=turn_idx,
                )
            )
            continue

        # Error / problem patterns
        if any(
            p in lower
            for p in [
                "error:",
                "failed",
                "exception",
                "traceback",
                "bug",
                "broken",
                "doesn't work",
                "does not work",
                "issue:",
                "problem:",
                "crash",
                "stack trace",
            ]
        ):
            entries.append(
                MemoryEntry(
                    category="error",
                    content=sentence,
                    confidence=0.85,
                    source_turn=turn_idx,
                )
            )
            continue

        # Conclusion / learning patterns — check BEFORE code_change to avoid
        # misclassifying "Conclusion: the watchdog..." as a code change
        if any(
            p in lower
            for p in [
                "turns out",
                "learned that",
                "the reason",
                "the root cause",
                "conclusion",
                "in summary",
                "key insight",
                "important:",
                "note:",
                "found that",
                "discovered",
                "works because",
            ]
        ):
            entries.append(
                MemoryEntry(
                    category="conclusion",
                    content=sentence,
                    confidence=0.8,
                    source_turn=turn_idx,
                )
            )
            continue

        # Code change patterns — but NOT if the sentence is a question
        if not is_question and any(
            p in lower
            for p in [
                "changed",
                "modified",
                "added",
                "removed",
                "refactored",
                "updated",
                "renamed",
                "created",
                "implemented",
                "wrote",
                "fixed the",
                "patched",
            ]
        ):
            if any(
                p in lower
                for p in [
                    "function",
                    "class",
                    "method",
                    "file",
                    "module",
                    "test",
                    "config",
                    "api",
                    "endpoint",
                    "handler",
                    "component",
                    "import",
                    "`",
                    "section",
                    "doc",
                    "documentation",
                    "spec",
                    "format",
                    "guide",
                    "readme",
                    "schema",
                    "template",
                    "hook",
                    "route",
                    "middleware",
                    "model",
                    "migration",
                    "script",
                    "command",
                    "prompt",
                    "pipeline",
                    "workflow",
                ]
            ):
                entries.append(
                    MemoryEntry(
                        category="code_change",
                        content=sentence,
                        confidence=0.85,
                        source_turn=turn_idx,
                    )
                )
                continue

        # Open question / uncertainty patterns
        if any(
            p in lower
            for p in [
                "?",
                "not sure",
                "unclear",
                "need to figure out",
                "todo",
                "investigate",
                "might need",
                "open question",
                "still need to",
                "haven't decided",
                "tbd",
                "worth considering",
            ]
        ):
            entries.append(
                MemoryEntry(
                    category="open_question",
                    content=sentence,
                    confidence=0.6,
                    source_turn=turn_idx,
                )
            )
            continue

        # Weaker conclusion patterns (checked after code_change to avoid
        # over-matching on "because" in code-change contexts)
        if any(
            p in lower
            for p in [
                "because",
                "confirmed",
                "verified",
            ]
        ):
            entries.append(
                MemoryEntry(
                    category="conclusion",
                    content=sentence,
                    confidence=0.8,
                    source_turn=turn_idx,
                )
            )
            continue

    return entries


def _build_context_summary(messages: list[dict[str, str]]) -> str:
    """Build a brief context summary from the conversation.

    If the first user message is short/ambiguous (< 30 chars), looks at
    subsequent user messages and assistant task descriptions for better context.
    """
    # Collect candidate snippets from user messages
    candidates: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        text = msg.get("content", "").strip()
        if not text:
            continue
        dot = text.find(". ")
        if 0 < dot < 200:
            snippet = text[: dot + 1]
        else:
            snippet = text[:200]
        candidates.append(snippet)

    if not candidates:
        return "No context available."

    # If first user message is short/ambiguous, prefer a longer subsequent one
    best = candidates[0]
    if len(best) < 30 and len(candidates) > 1:
        for candidate in candidates[1:3]:
            if len(candidate) > len(best):
                best = candidate

    # If still short, check assistant messages for task descriptions
    if len(best) < 30:
        for msg in messages[:4]:
            if msg.get("role") == "assistant":
                text = msg.get("content", "").strip()
                for pattern in [
                    r"(?:I'll|Let me|I will|I can see|Here's the plan)[^.]*\.",
                    r"(?:The (?:task|issue|problem|goal) is)[^.]*\.",
                    r"(?:Working on|Looking at)[^.]*\.",
                ]:
                    m = re.search(pattern, text, re.IGNORECASE)
                    if m and len(m.group(0)) > len(best):
                        best = m.group(0)[:200]
                        break

    return f"Task: {best}"


_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "we", "should",
    "do", "does", "did", "to", "for", "in", "on", "of", "and",
    "or", "not", "this", "that", "it", "be", "have", "has",
    "will", "can", "about", "with", "from", "i", "you", "our",
    "if", "but", "so", "at", "by", "how", "what", "when",
})


def _remove_resolved_questions(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Remove open_question entries that were answered later in the conversation."""
    questions = [e for e in entries if e.category == "open_question"]
    answers = [e for e in entries if e.category in ("decision", "conclusion")]

    if not questions or not answers:
        return entries

    resolved_ids: set[int] = set()

    for q in questions:
        q_words = set(re.sub(r"[^\w\s]", "", q.content.lower()).split()) - _STOP_WORDS
        if len(q_words) < 2:
            continue

        for a in answers:
            if a.source_turn <= q.source_turn:
                continue
            a_words = set(re.sub(r"[^\w\s]", "", a.content.lower()).split()) - _STOP_WORDS
            overlap = q_words & a_words
            if len(overlap) >= max(2, int(len(q_words) * 0.4)):
                resolved_ids.add(id(q))
                break

    return [e for e in entries if id(e) not in resolved_ids]


def _deduplicate_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Remove near-duplicate entries, keeping the higher-confidence one."""
    seen_content: dict[str, MemoryEntry] = {}
    for entry in entries:
        # Normalize for dedup: lowercase, strip punctuation
        key = re.sub(r"[^\w\s]", "", entry.content.lower()).strip()
        # Use first 80 chars as dedup key
        key = key[:80]
        if key in seen_content:
            if entry.confidence > seen_content[key].confidence:
                seen_content[key] = entry
        else:
            seen_content[key] = entry
    return list(seen_content.values())


def compress_transcript(
    messages: list[dict[str, str]],
) -> CompressedMemory:
    """Compress a conversation transcript into an Aleph memory object.

    Args:
        messages: List of dicts with 'role' and 'content' keys.

    Returns:
        CompressedMemory with extracted entities, symbol dict, and entries.
    """
    if not messages:
        return CompressedMemory()

    # Estimate original size
    raw_text = "\n".join(msg.get("content", "") for msg in messages)
    original_tokens = _estimate_tokens(raw_text)

    # Extract recurring entities and build symbol dictionary
    entity_counts = _extract_entities(messages)
    symbol_dict: dict[str, str] = {}
    entity_to_symbol: dict[str, str] = {}
    # Scale symbol count with conversation size to maintain compression ratio
    max_symbols = min(50, max(8, len(messages) * 2))
    for entity, _count in entity_counts.most_common(max_symbols):
        sid = _make_symbol_id(entity)
        # Handle collisions
        if sid in symbol_dict:
            h = hashlib.sha256(entity.encode()).hexdigest()[:8]
            sid = f"s_{h}"
        symbol_dict[sid] = entity
        entity_to_symbol[entity] = sid

    # Extract structured entries from each message
    all_entries: list[MemoryEntry] = []
    for idx, msg in enumerate(messages):
        content = msg.get("content", "")
        role = msg.get("role", "unknown")
        entries = _classify_message_content(content, role, idx)
        all_entries.extend(entries)

    # Attach symbol references to entries
    for entry in all_entries:
        for entity, sid in entity_to_symbol.items():
            if entity in entry.content:
                entry.symbol_refs.append(sid)

    # Apply symbol substitution in entry content
    for entry in all_entries:
        for entity, sid in sorted(
            entity_to_symbol.items(), key=lambda x: len(x[0]), reverse=True
        ):
            entry.content = entry.content.replace(entity, sid)

    # Deduplicate
    all_entries = _deduplicate_entries(all_entries)

    # Remove questions that were answered later in the conversation
    all_entries = _remove_resolved_questions(all_entries)

    # Truncate entry content for compression (keep first 150 chars)
    for entry in all_entries:
        if len(entry.content) > 150:
            entry.content = entry.content[:147] + "..."

    # Limit total entries to keep compressed output small
    # Prioritize by category importance and confidence
    category_priority = {
        "decision": 0,
        "error": 1,
        "conclusion": 2,
        "code_change": 3,
        "open_question": 4,
    }
    all_entries.sort(
        key=lambda e: (category_priority.get(e.category, 5), -e.confidence)
    )
    # Ensure diverse category representation: reserve 1 slot per category
    # present, then fill remaining budget by priority.
    max_entries = min(15, max(5, len(messages)))
    # First pass: guarantee at least 1 entry per category
    selected: list[MemoryEntry] = []
    remaining: list[MemoryEntry] = []
    seen_cats: set[str] = set()
    for entry in all_entries:
        if entry.category not in seen_cats:
            selected.append(entry)
            seen_cats.add(entry.category)
        else:
            remaining.append(entry)
    # Fill remaining budget with highest-priority entries
    budget = max_entries - len(selected)
    if budget > 0:
        selected.extend(remaining[:budget])
    all_entries = selected

    # Build context summary
    context_summary = _build_context_summary(messages)

    # Build compressed memory
    memory = CompressedMemory(
        entries=all_entries,
        symbol_dict=symbol_dict,
        context_summary=context_summary,
        original_token_estimate=original_tokens,
        message_count=len(messages),
    )

    # Estimate compressed size
    memory.compressed_token_estimate = _estimate_tokens(
        serialize_memory(memory)
    )

    return memory


def serialize_memory(memory: CompressedMemory) -> str:
    """Serialize a CompressedMemory to the [ALEPH:MEMORY:1.0] text format."""
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

    lines: list[str] = [MEMORY_HEADER]

    # Stats
    lines.append(STATS_TAG)
    lines.append(f"messages={memory.message_count}")
    lines.append(f"original_tokens={memory.original_token_estimate}")
    lines.append(f"compressed_tokens={memory.compressed_token_estimate}")
    lines.append(f"reduction={memory.reduction_percent:.1f}%")
    lines.append(STATS_END)

    # Context
    lines.append(CONTEXT_TAG)
    lines.append(memory.context_summary)
    lines.append(CONTEXT_END)

    # Symbol dictionary
    if memory.symbol_dict:
        lines.append(DICT_TAG)
        for sid, name in sorted(memory.symbol_dict.items()):
            lines.append(f"{sid}={name}")
        lines.append(DICT_END)

    # Group entries by category
    by_category: dict[str, list[MemoryEntry]] = {}
    for entry in memory.entries:
        by_category.setdefault(entry.category, []).append(entry)

    category_tags = {
        "decision": (DECISIONS_TAG, DECISIONS_END),
        "conclusion": (CONCLUSIONS_TAG, CONCLUSIONS_END),
        "open_question": (OPEN_QUESTIONS_TAG, OPEN_QUESTIONS_END),
        "code_change": (CODE_CHANGES_TAG, CODE_CHANGES_END),
        "error": (ERRORS_TAG, ERRORS_END),
    }

    for cat, (open_tag, close_tag) in category_tags.items():
        cat_entries = by_category.get(cat, [])
        if cat_entries:
            lines.append(open_tag)
            for entry in cat_entries:
                refs = " ".join(entry.symbol_refs) if entry.symbol_refs else ""
                conf = f" [{entry.confidence:.1f}]"
                ref_suffix = f" refs={refs}" if refs else ""
                lines.append(f"{entry.content}{conf}{ref_suffix}")
            lines.append(close_tag)

    return "\n".join(lines) + "\n"
