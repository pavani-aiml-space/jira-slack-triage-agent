"""
Slack Reader — Step A: Imports

Fetches raw messages from a Slack channel via the official
@modelcontextprotocol/server-slack MCP server (stdio transport).

Responsibilities:
    - Connect to Slack MCP
    - Fetch raw messages from a channel
    - Return them as-is (grouping is done by context_builder.py)
"""

# ── Standard library ──────────────────────────────────────────────────────────
import json
import os                          # pass env vars to the MCP subprocess
from contextlib import asynccontextmanager  # same session pattern as mcp_client.py

# ── MCP SDK ───────────────────────────────────────────────────────────────────
from mcp import ClientSession                  # manages the MCP conversation
from mcp.client.stdio import stdio_client      # Slack MCP runs locally via npx (stdio)
from mcp import StdioServerParameters          # tells MCP how to launch the subprocess

# ── Project config ────────────────────────────────────────────────────────────
from config.settings import settings           # SLACK_BOT_TOKEN, SLACK_TEAM_ID, MAX_MESSAGES_TO_FETCH


# ── Step B: Connection function ───────────────────────────────────────────────
@asynccontextmanager
async def slack_mcp_session():
    """
    Launch the Slack MCP subprocess and yield a live ClientSession.

    Usage:
        async with slack_mcp_session() as session:
            tools = await session.list_tools()
    """
    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        env={
            **os.environ,                          # carry through PATH, NODE, etc.
            "SLACK_BOT_TOKEN": settings.SLACK_BOT_TOKEN,
            "SLACK_TEAM_ID":   settings.SLACK_TEAM_ID,
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()             # MCP handshake
            yield session


# ── Step C: Fetch raw messages from a channel ─────────────────────────────────
async def fetch_messages(
    channel_id: str,
    oldest: str | None = None,
) -> list[dict]:
    """
    Connect to Slack MCP, fetch messages from a channel, and return them as a
    raw list — no grouping, no formatting.

    Args:
        channel_id: Slack channel ID (e.g. "C123ABC")
        oldest:     Only return messages with ts > oldest (Unix timestamp string).
                    Pass the watermark from the previous run to fetch only new messages.
                    None (default) fetches the last MAX_MESSAGES_TO_FETCH messages.

    Each returned message is a dict with:
        text  — the message content
        user  — Slack user ID who sent it
        ts    — Unix timestamp string (e.g. "1714045800.000100")

    Note: The official Slack MCP server does not support DMs.
          Use a channel (e.g. #bug-reports) where the bot is invited.
    """
    async with slack_mcp_session() as session:

        arguments: dict = {
            "channel_id": channel_id,
            "limit":      settings.MAX_MESSAGES_TO_FETCH,
        }
        if oldest is not None:
            arguments["oldest"] = oldest

        result = await session.call_tool(
            "slack_get_channel_history",
            arguments=arguments,
        )

        raw = []
        for block in result.content:
            if hasattr(block, "text"):
                data = json.loads(block.text)
                raw  = data.get("messages", [])
                break

        # return only fields we care about, oldest first
        messages = [
            {
                "user": m.get("user", "unknown"),
                "text": m.get("text", ""),
                "ts":   m.get("ts", "0"),
            }
            for m in reversed(raw)                   # Slack returns newest first → flip
            if m.get("text", "").strip()             # skip empty messages
            and m.get("subtype") is None             # skip system events (joins, leaves etc.)
        ]

        return messages


# ── Step D: Fetch replies to a specific thread ────────────────────────────────
async def fetch_thread_replies(channel_id: str, thread_ts: str) -> list[dict]:
    """
    Connect to Slack MCP, fetch all replies in a thread, and return them as a
    raw list — same shape as fetch_messages(), excluding the root message itself.

    Used by confirmation_resolver.py to check whether a human has replied to a
    low-confidence escalation proposal.

    Args:
        channel_id: Slack channel ID (e.g. "C123ABC")
        thread_ts:  ts of the thread's root message (the escalation proposal)

    Each returned message is a dict with:
        text  — the message content
        user  — Slack user ID who sent it
        ts    — Unix timestamp string
    """
    async with slack_mcp_session() as session:

        result = await session.call_tool(
            "slack_get_thread_replies",
            arguments={
                "channel_id": channel_id,
                "thread_ts":  thread_ts,
            },
        )

        raw = []
        for block in result.content:
            if hasattr(block, "text"):
                data = json.loads(block.text)
                raw  = data.get("messages", [])
                break

        # conversations.replies (unlike conversations.history) returns oldest-first
        # already — no reversal needed here.
        replies = [
            {
                "user": m.get("user", "unknown"),
                "text": m.get("text", ""),
                "ts":   m.get("ts", "0"),
            }
            for m in raw
            if m.get("text", "").strip()
            and m.get("subtype") is None
            and m.get("ts") != thread_ts             # exclude the proposal message itself
        ]

        return replies
