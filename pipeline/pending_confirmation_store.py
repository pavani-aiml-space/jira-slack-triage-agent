"""
Pending Confirmation Store — cross-run state for low-confidence escalations.

When the agent escalates a low-confidence classification, it posts a proposal
to Slack and persists a PendingConfirmation here instead of filing immediately.
A later run's resolve_pending_confirmations() checks each one for a reply.

Lifecycle:
  escalate_for_confirmation() → add_pending + save_pending_store
  resolve_pending_confirmations() → load_pending_store, check replies,
                                     mark_resolved + save_pending_store
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class PendingConfirmation:
    run_id:                str
    block_index:            int
    block_snippet:          str
    proposed_summary:       str
    proposed_issue_type:    str
    proposed_priority:      str
    proposed_description:   str
    proposed_labels:        list[str]
    confidence:             float
    reasoning:              str
    channel_id:             str
    proposal_ts:            str
    created_at:             str
    status:                 str = "pending"  # "pending" | "resolved"


@dataclass
class PendingConfirmationStore:
    items: list[PendingConfirmation] = field(default_factory=list)


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_pending_store(path: str) -> PendingConfirmationStore:
    """
    Load PendingConfirmationStore from disk.
    Returns empty store if the file is missing or corrupt — never raises.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        items = [PendingConfirmation(**item) for item in data.get("items", [])]
        return PendingConfirmationStore(items=items)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return PendingConfirmationStore()


def save_pending_store(store: PendingConfirmationStore, path: str) -> None:
    """
    Persist PendingConfirmationStore to disk.
    Creates parent directories if needed. Never raises (Rule 5).
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"items": [asdict(item) for item in store.items]}, f, indent=2)
    except Exception as e:
        print(f"[pending_confirmation_store] save_pending_store failed (Rule 5): {e}")


# ── Operations ────────────────────────────────────────────────────────────────

def add_pending(store: PendingConfirmationStore, item: PendingConfirmation) -> None:
    """Append a new pending confirmation to the store."""
    store.items.append(item)


def mark_resolved(store: PendingConfirmationStore, proposal_ts: str) -> None:
    """Remove the pending item matching proposal_ts (it's been filed, resolution is done)."""
    store.items = [item for item in store.items if item.proposal_ts != proposal_ts]


def pending_only(store: PendingConfirmationStore) -> list[PendingConfirmation]:
    """Return only items still awaiting resolution."""
    return [item for item in store.items if item.status == "pending"]
