"""
Slack MCP server configuration.
Swap this file if Slack releases a new MCP server — nothing else changes.
"""
import os
from config.settings import settings


def get_slack_server_config() -> dict:
    return {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {
            **os.environ,
            "SLACK_BOT_TOKEN": settings.SLACK_BOT_TOKEN,
            "SLACK_TEAM_ID": settings.SLACK_TEAM_ID,
        },
        "transport": "stdio",
    }
