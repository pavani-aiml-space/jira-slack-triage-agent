"""
Run Logger — persists agent run outcomes to disk.

One JSON file per run: logs/run_<run_id>.json
Provides:
  - RunLog        : top-level run record
  - BlockResult   : per-block outcome (action + ticket fields + LLM stats)
  - LlmStats      : token counts and tool calls per LLM loop
  - ErrorEntry    : per-block error from Phase 2 accumulator
  - write_run_log : serialise RunLog to disk
  - load_run_logs : read all run files from a directory, newest-first
  - SENTINEL_FILE : path of the .running sentinel used by dashboard.py
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Optional

SENTINEL_FILE = "logs/.running"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class LlmStats:
    iterations: int
    tools_called: list[str]
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


@dataclass
class BlockResult:
    block_index: int
    block_snippet: str
    action: str  # "ticket_created" | "clarification_asked" | "error" | "skipped" | "no_action"
    ticket_key: Optional[str] = None
    ticket_summary: Optional[str] = None
    ticket_type: Optional[str] = None
    ticket_priority: Optional[str] = None
    ticket_description: Optional[str] = None  # Jira description from create_jira_ticket (eval / judge)
    confirmation_ts: Optional[str] = None   # Slack ts of the confirmation post; None if not captured
    llm: Optional[LlmStats] = None


@dataclass
class ErrorEntry:
    block_index: int
    block_snippet: str
    error_type: str
    error_message: str
    phase2_rule: str  # "Rule 1" | "Rule 5" | "Rule 6"


@dataclass
class RunLog:
    run_id: str                    # ISO-8601, e.g. "2026-04-29T13:20:01"
    started_at: str
    completed_at: Optional[str]
    status: str                    # "success" | "partial" | "fatal"
    messages_fetched: int
    blocks_processed: int
    tickets_created_count: int
    clarifications_asked_count: int
    blocks_skipped_count: int      # always 0 in Phase 3; Phase 4 populates
    error_count: int
    duplicates_flagged_count: int = 0
    last_message_ts: str | None = None  # ts of latest message processed; used as next run's watermark
    blocks: list[BlockResult] = field(default_factory=list)
    errors: list[ErrorEntry] = field(default_factory=list)


# ── I/O functions ─────────────────────────────────────────────────────────────

def write_run_log(run_log: RunLog, log_dir: str = "logs") -> str:
    """
    Serialise run_log to JSON and write to log_dir/run_<run_id>.json.
    Creates log_dir if it does not exist.
    Returns the file path written.
    """
    os.makedirs(log_dir, exist_ok=True)
    safe_id = run_log.run_id.replace(":", "-")
    path = os.path.join(log_dir, f"run_{safe_id}.json")
    with open(path, "w") as f:
        json.dump(asdict(run_log), f, indent=2)
    return path


def load_run_logs(log_dir: str = "logs") -> list[dict]:
    """
    Read all run_*.json files from log_dir.
    Returns a list of parsed dicts sorted newest-first by run_id.
    Returns [] if log_dir does not exist or contains no valid run files.
    Silently skips malformed JSON files.
    """
    if not os.path.isdir(log_dir):
        return []

    logs = []
    for fname in os.listdir(log_dir):
        if not fname.startswith("run_") or not fname.endswith(".json"):
            continue
        fpath = os.path.join(log_dir, fname)
        try:
            with open(fpath) as f:
                logs.append(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[run_logger] Skipping malformed log file {fname}: {e}")

    return sorted(logs, key=lambda l: l["run_id"], reverse=True)
