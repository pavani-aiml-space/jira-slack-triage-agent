"""
Semantic Store — semantic memory layer.

Extracts recurring patterns from episodic memory and formats them
for injection into the LLM system prompt.

Lifecycle:
  post_run → extract_count_patterns → (optionally) summarise_with_llm
           → save_semantic_store
  pre_run  → load_semantic_store → build_semantic_injection → MemoryContext
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agents.llm.factory import get_llm_provider
from config.settings import settings

if TYPE_CHECKING:
    from pipeline.episode_store import Episode

_provider = get_llm_provider(settings)


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    type_priority_key:  str
    count:              int
    example_summaries:  list[str]
    summary_text:       str
    created_at:         str
    source:             str   # "count_based" | "llm_summarised"


@dataclass
class SemanticStore:
    patterns:                     list[Pattern] = field(default_factory=list)
    last_extracted_episode_count: int           = 0


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_semantic_store(path: str) -> SemanticStore:
    """
    Load SemanticStore from disk.
    Returns empty SemanticStore if the file is missing or corrupt — never raises.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        patterns = [Pattern(**p) for p in data.get("patterns", [])]
        return SemanticStore(
            patterns=patterns,
            last_extracted_episode_count=data.get("last_extracted_episode_count", 0),
        )
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return SemanticStore()


def save_semantic_store(store: SemanticStore, path: str) -> None:
    """
    Persist SemanticStore to disk.
    Creates parent directories if needed. Never raises (Rule 5).
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {
            "patterns": [asdict(p) for p in store.patterns],
            "last_extracted_episode_count": store.last_extracted_episode_count,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[semantic_store] save_semantic_store failed (Rule 5): {e}")


# ── Pattern extraction ────────────────────────────────────────────────────────

def extract_count_patterns(episodes: list[Episode], min_count: int) -> list[Pattern]:
    """
    Pure function — no I/O, no API calls.
    Emits a Pattern for each (ticket_type, ticket_priority) combination
    that appears at least min_count times across episodes.
    """
    counts: dict[str, list[str]] = defaultdict(list)
    for ep in episodes:
        key = f"{ep.ticket_type}:{ep.ticket_priority}"
        counts[key].append(ep.ticket_summary)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return [
        Pattern(
            type_priority_key=key,
            count=len(summaries),
            example_summaries=summaries[:5],
            summary_text=f"{key} ({len(summaries)} decisions)",
            created_at=now,
            source="count_based",
        )
        for key, summaries in counts.items()
        if len(summaries) >= min_count
    ]


async def summarise_with_llm(patterns: list[Pattern]) -> list[Pattern]:
    """
    Enrich each Pattern's summary_text using the configured LLM provider
    (Claude by default, via agents/llm/factory.get_llm_provider(), same
    provider abstraction the main triage call uses).
    On any failure, returns the pattern unchanged (Rule 10).
    """
    result = list(patterns)
    for i, p in enumerate(result):
        examples = "\n".join(f"- {s}" for s in p.example_summaries[:5])
        prompt = (
            f"Summarise this triage pattern in one plain-English sentence "
            f"(type: {p.type_priority_key}, {p.count} decisions):\n{examples}"
        )
        try:
            turn = await _provider.chat([{"role": "user", "content": prompt}], [])
            result[i] = Pattern(
                type_priority_key=p.type_priority_key,
                count=p.count,
                example_summaries=p.example_summaries,
                summary_text=(turn.content or "").strip(),
                created_at=p.created_at,
                source="llm_summarised",
            )
        except Exception as e:
            # Rule 10 — return pattern unchanged on LLM failure
            print(f"[semantic_store] summarise_with_llm failed for {p.type_priority_key} (Rule 10): {e}")
    return result


def build_semantic_injection(store: SemanticStore, max_chars: int) -> str:
    """
    Format all patterns as a text block for SYSTEM_PROMPT injection.
    Returns "" if no patterns. Truncated to max_chars.
    """
    if not store.patterns:
        return ""
    lines = ["## Learned Patterns"]
    for p in store.patterns:
        lines.append(f"- {p.summary_text}")
    text = "\n".join(lines)
    return text[:max_chars]
