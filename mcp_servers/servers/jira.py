"""
Jira MCP server configuration.
Swap this file if you switch to a different Jira MCP server — nothing else changes.

Uses uvx mcp-atlassian (stdio transport) — same package as agents/triage/tools/jira_tools.py.
Auth: --jira-url, --jira-username, --jira-token (API token for Jira Cloud)
"""
from config.settings import settings


def get_jira_server_config() -> dict:
    return {
        "command": "uvx",
        "args": [
            "mcp-atlassian",
            "--jira-url",      settings.JIRA_URL,
            "--jira-username", settings.JIRA_EMAIL,
            "--jira-token",    settings.JIRA_API_TOKEN,
        ],
        "transport": "stdio",
    }
