"""
Watermark — persist the timestamp of the last successfully processed Slack message.

On each run:
  - load_watermark()  → pass to fetch_messages(oldest=ts) so only new messages are fetched
  - save_watermark()  → called after a successful run to advance the cursor

If the watermark file doesn't exist (first run), fetch_messages falls back to the
last MAX_MESSAGES_TO_FETCH messages from the channel.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_watermark(path: str) -> str | None:
    """Return the last-processed message timestamp, or None on first run."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get("last_ts")
    except (json.JSONDecodeError, OSError):
        return None


def save_watermark(path: str, ts: str) -> None:
    """Advance the watermark to the latest processed message timestamp."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"last_ts": ts}))
