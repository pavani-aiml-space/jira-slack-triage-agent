"""
Slack Tools — LLM-callable tools for posting messages to Slack.

Two parts (same pattern as jira_tools.py):
  1. SCHEMA   — JSON schema OpenAI uses to know what tools exist
  2. EXECUTOR — Python function that calls the Slack MCP

Tools:
  - post_slack_message     → post a reply in the channel (e.g. "Created SCRUM-3 ✓")
  - ask_for_clarification  → ask the user for more info (e.g. "Can you describe what's wrong?")

Phase 5 additions:
  - _confirmation_ts_buffer — captures the Slack message_ts of each confirmation post
  - drain_confirmation_ts() — pops and returns the last captured ts (or None)
"""
import json
from typing import Optional
from pipeline.slack_reader import slack_mcp_session
from config.settings import settings


# ── Phase 5: ts capture buffer ────────────────────────────────────────────────
# Populated by post_slack_message() when the MCP response includes a ts field.
# Drained once per block by triage_agent.run() after the LLM loop completes.
_confirmation_ts_buffer: list[str] = []


def drain_confirmation_ts() -> Optional[str]:
    """
    Pop and return the last captured Slack message ts, then clear the buffer.
    Returns None if nothing was captured (MCP didn't return ts, or no post was made).
    """
    if not _confirmation_ts_buffer:
        return None
    ts = _confirmation_ts_buffer[-1]
    _confirmation_ts_buffer.clear()
    return ts


# ── 1. SCHEMAS — what OpenAI sees ────────────────────────────────────────────

POST_SLACK_MESSAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "post_slack_message",
        "description": (
            "Post a message in the Slack channel. "
            "Use this to confirm a Jira ticket was created, "
            "or to notify the team about a duplicate ticket."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to post in Slack",
                },
            },
            "required": ["message"],
        },
    },
}

ASK_FOR_CLARIFICATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "ask_for_clarification",
        "description": (
            "Ask the user for more information in Slack. "
            "Use this when the message is unclear, vague, or missing key details "
            "needed to create a proper Jira ticket."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The clarifying question to ask the user",
                },
            },
            "required": ["question"],
        },
    },
}


# ── 2. EXECUTORS — what Python runs when LLM calls the tools ─────────────────

async def post_slack_message(message: str) -> str:
    """
    Post a message in the Slack channel via Slack MCP.
    Called by triage_agent when LLM decides to notify the team.

    Phase 5: attempts to parse the Slack message_ts from the MCP response
    and append it to _confirmation_ts_buffer for reaction tracking.
    Silent on any parse failure (Rule 9 — missing ts ≠ bad ticket).

    Returns:
        str — confirmation e.g. "Message posted to channel"
    """
    async with slack_mcp_session() as session:
        result = await session.call_tool(
            "slack_post_message",
            arguments={
                "channel_id": settings.SLACK_CHANNEL_ID,
                "text":       message,
            },
        )
    # Phase 5: try to extract ts from MCP response for reaction tracking
    try:
        data = json.loads(result.content[0].text)
        ts = data.get("ts")
        if ts:
            _confirmation_ts_buffer.append(str(ts))
    except Exception:
        pass  # Rule 9 — ts not available; confirmation excluded from reaction tracking
    return f"Message posted: {message}"


async def ask_for_clarification(question: str) -> str:
    """
    Post a clarifying question in the Slack channel via Slack MCP.
    Called by triage_agent when LLM needs more info before creating a ticket.

    Returns:
        str — confirmation e.g. "Question posted to channel"
    """
    async with slack_mcp_session() as session:
        await session.call_tool(
            "slack_post_message",
            arguments={
                "channel_id": settings.SLACK_CHANNEL_ID,
                "text":       f"🤔 {question}",
            },
        )
    return f"Clarification asked: {question}"


# ── All schemas in one list — passed to OpenAI ───────────────────────────────
ALL_SLACK_SCHEMAS = [
    POST_SLACK_MESSAGE_SCHEMA,
    ASK_FOR_CLARIFICATION_SCHEMA,
]
