"""
Context Builder

Takes raw Slack messages (from slack_reader.py) and groups them into
conversation blocks based on the configurable time window.

Rules:
    - Messages within CONTEXT_WINDOW_MINUTES of each other → same block
    - Gap larger than CONTEXT_WINDOW_MINUTES → new block
    - Each block becomes one classification request to Claude

Input  : list of raw messages [{user, text, ts}, ...]
Output : list of conversation blocks [{messages, combined_text, start_ts, end_ts}, ...]
"""

from config.settings import settings


def build_context_blocks(messages: list[dict]) -> list[dict]:
    """
    Group raw messages into conversation blocks by time window.

    Args:
        messages: list of {user, text, ts} dicts — oldest first

    Returns:
        list of conversation blocks, each with:
            messages      — the raw messages in this block
            combined_text — all message texts joined into one string
            start_ts      — timestamp of the first message
            end_ts        — timestamp of the last message
    """
    if not messages:
        return []

    window_seconds = settings.CONTEXT_WINDOW_MINUTES * 60
    blocks         = []
    current_block  = [messages[0]]

    for msg in messages[1:]:
        prev_ts = float(current_block[-1]["ts"])
        curr_ts = float(msg["ts"])
        gap     = curr_ts - prev_ts          # seconds between messages

        if gap <= window_seconds:
            current_block.append(msg)        # same block — within time window
        else:
            blocks.append(_make_block(current_block))   # save completed block
            current_block = [msg]                        # start a new block

    blocks.append(_make_block(current_block))            # save the last block
    return blocks


def _make_block(messages: list[dict]) -> dict:
    """
    Turn a list of messages into a conversation block.

    combined_text joins all messages into one string for Claude to read:
        "login page is crashing when password is empty
         it started after yesterday's deploy"
    """
    combined_text = "\n".join(m["text"] for m in messages)

    return {
        "messages":     messages,
        "combined_text": combined_text,
        "start_ts":     messages[0]["ts"],
        "end_ts":       messages[-1]["ts"],
    }
