"""
Quality Metrics — Eval & Feedback Loop (Phase 5)

Manages quality state across runs:
  - PendingReaction  : a confirmation post awaiting reaction polling
  - CollectedReaction: reactions found on a specific confirmation post
  - RunQuality       : aggregated thumbs-up/down for one run
  - QualityStore     : the full on-disk state (pending + completed runs)

Functions:
  load_quality_store   : read quality_store.json; safe on missing/corrupt file
  save_quality_store   : write quality_store.json; never raises
  add_pending_from_run : register new confirmation posts from a completed run
  apply_collected      : move collected reactions → RunQuality records
  should_alert         : check if thumbs-up rate is below threshold (Rule 8 gate)
  rolling_thumbs_up_rate : aggregate rate across all runs
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.run_logger import RunLog

from config.settings import settings


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PendingReaction:
    run_id: str
    block_index: int
    ticket_key: Optional[str]
    confirmation_ts: str       # Slack message_ts of the confirmation post
    posted_at_iso: str         # ISO timestamp of the run — for window filtering


@dataclass
class CollectedReaction:
    run_id: str
    block_index: int
    ticket_key: Optional[str]
    thumbs_up: int
    thumbs_down: int
    collected_at: str          # ISO timestamp of collection run


@dataclass
class RunQuality:
    run_id: str
    collected_at: str
    thumbs_up: int
    thumbs_down: int
    reactions_found: int
    thumbs_up_rate: Optional[float]   # None if total == 0 (Rule 9)


@dataclass
class QualityStore:
    pending: list[PendingReaction] = field(default_factory=list)
    runs: list[RunQuality] = field(default_factory=list)


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_quality_store(path: str) -> QualityStore:
    """
    Read quality_store.json and return a QualityStore.
    Returns empty store on missing file, corrupt JSON, or any read error.
    Never raises.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        pending = [PendingReaction(**p) for p in data.get("pending", [])]
        runs    = [RunQuality(**r)      for r in data.get("runs", [])]
        return QualityStore(pending=pending, runs=runs)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, KeyError) as e:
        if not isinstance(e, FileNotFoundError):
            print(f"[quality_metrics] load_quality_store failed ({type(e).__name__}): {e}")
        return QualityStore()


def save_quality_store(store: QualityStore, path: str) -> None:
    """
    Persist quality store to disk.
    Logs a warning on failure — never raises (Rule 5).
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "pending": [asdict(p) for p in store.pending],
                "runs":    [asdict(r) for r in store.runs],
            }, f, indent=2)
    except Exception as e:
        print(f"[quality_metrics] save_quality_store failed: {e}")


# ── Logic ─────────────────────────────────────────────────────────────────────

def add_pending_from_run(store: QualityStore, run_log: "RunLog") -> None:
    """
    Register new confirmation posts from a completed run.
    Appends a PendingReaction for each ticket_created BlockResult that has
    a non-None confirmation_ts.  Mutates store.pending in place.
    """
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for block in run_log.blocks:
        if block.action == "ticket_created" and block.confirmation_ts is not None:
            store.pending.append(PendingReaction(
                run_id=run_log.run_id,
                block_index=block.block_index,
                ticket_key=block.ticket_key,
                confirmation_ts=block.confirmation_ts,
                posted_at_iso=now_iso,
            ))


def apply_collected(store: QualityStore, collected: list[CollectedReaction]) -> None:
    """
    Move matching pending entries into store.runs as RunQuality records.

    Groups CollectedReaction by run_id.  For each run_id represented in
    collected, sums thumbs_up and thumbs_down across all blocks, builds a
    RunQuality, appends to store.runs, and removes the processed pending entries.

    Unmatched pending entries (no collected reaction yet) remain in store.pending.
    thumbs_up_rate is None when total == 0 (Rule 9 — no reactions ≠ bad ticket).
    """
    if not collected:
        return

    # Group by run_id
    by_run: dict[str, list[CollectedReaction]] = {}
    for c in collected:
        by_run.setdefault(c.run_id, []).append(c)

    # Build RunQuality for each run_id that has collected reactions
    processed_run_ids: set[str] = set()
    for run_id, reactions in by_run.items():
        thumbs_up   = sum(r.thumbs_up   for r in reactions)
        thumbs_down = sum(r.thumbs_down for r in reactions)
        total = thumbs_up + thumbs_down
        rate  = (thumbs_up / total) if total > 0 else None  # Rule 9

        collected_at = reactions[0].collected_at
        store.runs.append(RunQuality(
            run_id=run_id,
            collected_at=collected_at,
            thumbs_up=thumbs_up,
            thumbs_down=thumbs_down,
            reactions_found=total,
            thumbs_up_rate=rate,
        ))
        processed_run_ids.add(run_id)

    # Remove pending entries whose run_id was processed
    store.pending = [p for p in store.pending if p.run_id not in processed_run_ids]


def should_alert(
    store: QualityStore,
    threshold: float,
    min_reactions: int,
) -> tuple[bool, Optional[RunQuality]]:
    """
    Return (True, latest_run_quality) if a quality alert should fire.

    Rules:
      Rule 8 — warm-up gate: total reactions across ALL runs must reach
               min_reactions before any alert fires.
      Alert fires only if the most recent RunQuality entry has
               thumbs_up_rate < threshold (and rate is not None).
    Returns (False, None) otherwise.
    """
    if not store.runs:
        return False, None

    total_reactions = sum(r.reactions_found for r in store.runs)
    if total_reactions < min_reactions:   # Rule 8 — warming up
        return False, None

    latest = store.runs[-1]
    if latest.thumbs_up_rate is None:
        return False, None

    if latest.thumbs_up_rate < threshold:
        return True, latest

    return False, None


def rolling_thumbs_up_rate(
    store: QualityStore,
    min_reactions: Optional[int] = None,
) -> Optional[float]:
    """
    Aggregate thumbs-up rate across all runs.
    Returns None if total reactions < min_reactions (Rule 8 warm-up gate).
    """
    if min_reactions is None:
        min_reactions = settings.MIN_REACTIONS_FOR_QUALITY

    total_up    = sum(r.thumbs_up   for r in store.runs)
    total_down  = sum(r.thumbs_down for r in store.runs)
    total       = total_up + total_down

    if total < min_reactions:
        return None

    return total_up / total if total > 0 else None
