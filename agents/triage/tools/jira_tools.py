"""
Jira Tools — LLM-callable tools for creating Jira tickets via the Atlassian MCP server.

Two parts:
  1. SCHEMA  — JSON schema OpenAI uses to know what tools exist and what args they take
  2. EXECUTOR — Python function that calls Jira via MCP (same pattern as slack_tools.py)

MCP server: uvx mcp-atlassian (stdio transport)
Auth: --jira-url, --jira-username, --jira-token passed as CLI args to the subprocess
Tool name: jira_create_issue (confirmed for mcp-atlassian)
"""
import os
import json
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters

from config.settings import settings
from agents.triage.tools.slack_tools import post_slack_message


# ── MCP session ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def jira_mcp_session():
    """
    Launch the mcp-atlassian subprocess and yield a live ClientSession.

    Uses uvx so no global install is needed — uvx fetches mcp-atlassian on demand.

    Usage:
        async with jira_mcp_session() as session:
            result = await session.call_tool("jira_create_issue", {...})
    """
    server_params = StdioServerParameters(
        command="uvx",
        args=[
            "mcp-atlassian",
            "--jira-url",      settings.JIRA_URL,
            "--jira-username", settings.JIRA_EMAIL,
            "--jira-token",    settings.JIRA_API_TOKEN,
        ],
        env=os.environ.copy(),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


JIRA_CREATE_TOOL = "jira_create_issue"  # confirmed tool name for mcp-atlassian


# ── 1. SCHEMA — what OpenAI sees ─────────────────────────────────────────────

CREATE_JIRA_TICKET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_jira_ticket",
        "description": (
            "Create a Jira ticket (Bug, Story, or Task) from a Slack message. "
            "Use this when you are confident about the issue type and priority. "
            "Do NOT use if it looks like a duplicate of an existing ticket."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Short ticket title, max 80 chars, imperative tone e.g. 'Fix login crash on empty password'",
                },
                "issue_type": {
                    "type": "string",
                    "enum": ["Bug", "Story", "Task"],
                    "description": "Type of Jira issue",
                },
                "priority": {
                    "type": "string",
                    "enum": ["Critical", "High", "Medium", "Low"],
                    "description": "Ticket priority",
                },
                "description": {
                    "type": "string",
                    "description": "Structured description with What, Steps to Reproduce, Expected, Context",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of lowercase labels e.g. ['login', 'regression']",
                },
            },
            "required": ["summary", "issue_type", "priority", "description"],
        },
    },
}


# ── 2. EXECUTOR — what Python runs when LLM calls the tool ───────────────────

async def create_jira_ticket(
    summary: str,
    issue_type: str,
    priority: str,
    description: str,
    labels: list[str] | None = None,
) -> str:
    """
    Called by triage_agent when the LLM decides to create a Jira ticket.
    Delegates to mcp-atlassian (uvx) via stdio — same pattern as slack_tools.py.

    Returns:
        str — e.g. "Created SCRUM-3: Fix login crash on empty password"
    """
    additional = {"priority": {"name": priority}}
    if labels:
        additional["labels"] = labels

    try:
        async with jira_mcp_session() as session:
            result = await session.call_tool(
                JIRA_CREATE_TOOL,
                arguments={
                    "project_key":       settings.JIRA_PROJECT_KEY,
                    "summary":           summary,
                    "issue_type":        issue_type,
                    "description":       description,
                    "additional_fields": json.dumps(additional),
                },
            )

            raw = {}
            for block in result.content:
                if hasattr(block, "text"):
                    try:
                        data = json.loads(block.text)
                        raw = data.get("issue", data)
                    except json.JSONDecodeError:
                        pass
                    break

            key = raw.get("key", "unknown")

        return f"Created {key}: {summary} → {settings.JIRA_URL}/browse/{key}"

    except Exception as e:
        await post_slack_message(
            f"⚠️ Jira unavailable — please create this ticket manually:\n"
            f"  Summary: {summary}\n"
            f"  Type: {issue_type} | Priority: {priority}\n"
            f"  Error: {e}"
        )
        return (
            f"[JIRA_ERROR] Jira unavailable — team notified in Slack. "
            f"Do not send additional notification. Summary was: {summary}"
        )
