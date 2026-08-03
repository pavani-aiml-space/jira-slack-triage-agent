"""
MCPClientManager — the single place that knows about all MCP servers.

Agents never configure servers directly. They call:
    async with MCPClientManager.connect(["slack", "jira"]) as tools:
        ...

To add a new server (e.g. GitHub):
    1. Add mcp/servers/github.py
    2. Register it in REGISTRY below
    That's it — no agent code changes.
"""
from contextlib import asynccontextmanager
from langchain_mcp_adapters.client import MultiServerMCPClient

from mcp_servers.servers.slack import get_slack_server_config
from mcp_servers.servers.jira import get_jira_server_config


REGISTRY: dict[str, callable] = {
    "slack": get_slack_server_config,
    "jira":  get_jira_server_config,
    # "github": get_github_server_config,   ← add future servers here
}


@asynccontextmanager
async def connect(server_names: list[str]):
    """
    Start the requested MCP servers and yield their combined tool list.

    Usage:
        async with connect(["slack", "jira"]) as tools:
            agent = create_react_agent(llm, tools)
    """
    unknown = [s for s in server_names if s not in REGISTRY]
    if unknown:
        raise ValueError(f"Unknown MCP servers: {unknown}. Available: {list(REGISTRY)}")

    server_configs = {name: REGISTRY[name]() for name in server_names}

    async with MultiServerMCPClient(server_configs) as client:
        yield client.get_tools()
