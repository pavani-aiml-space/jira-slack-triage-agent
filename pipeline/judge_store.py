"""
LLM judge score persistence (Phase 5b).

Append-only list of per-ticket judge rows in memory/judge_store.json (path from settings).
Safe on missing or corrupt file — never raises from load.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class JudgeScoreEntry:
    run_id: str
    block_index: int
    ticket_key: Optional[str]
    judged_at: str  # ISO-8601 UTC
    type_score: Optional[int] = None
    priority_score: Optional[int] = None
    title_score: Optional[int] = None
    description_score: Optional[int] = None
    reason: str = ""
    error: Optional[str] = None  # set when the judge call or JSON parse failed


def load_judge_store(path: str) -> list[dict[str, Any]]:
    """Return scores list from disk, or [] on error. Never raises."""
    try:
        with open(path) as f:
            data = json.load(f)
        scores = data.get("scores", [])
        return scores if isinstance(scores, list) else []
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError) as e:
        if not isinstance(e, FileNotFoundError):
            print(f"[judge_store] load failed ({type(e).__name__}): {e}")
        return []


def save_judge_store(path: str, scores: list[dict[str, Any]]) -> None:
    """Write scores list. Logs on failure — never raises (Rule 5)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"scores": scores}, f, indent=2)
    except OSError as e:
        print(f"[judge_store] save failed: {e}")


def append_judge_entries(path: str, entries: list[JudgeScoreEntry]) -> None:
    """Load, extend with new entries, save."""
    if not entries:
        return
    existing = load_judge_store(path)
    for e in entries:
        existing.append(asdict(e))
    save_judge_store(path, existing)
