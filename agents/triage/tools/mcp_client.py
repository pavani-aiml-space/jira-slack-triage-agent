"""
Atlassian MCP client.

Connects to the official Atlassian remote MCP server using API token auth
(Basic auth: base64(email:api_token)) and exposes two helpers:

    list_tools(session)          → list of mcp.types.Tool
    call_tool(session, name, args) → text result string
"""
import base64
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://mcp.atlassian.com/v1/mcp"


def _auth_header(email: str, api_token: str) -> dict[str, str]:
    """Build the Basic-auth header Atlassian expects."""
    credentials = base64.b64encode(f"{email}:{api_token}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


@asynccontextmanager
async def atlassian_mcp_session(email: str, api_token: str):
    """
    Async context manager that yields a live MCP ClientSession.

    Usage:
        async with atlassian_mcp_session(email, token) as session:
            tools = await list_tools(session)
    """
    headers = _auth_header(email, api_token)
    async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_tools(session: ClientSession) -> list:
    """Return all tools the MCP server exposes."""
    result = await session.list_tools()
    return result.tools


async def call_tool(session: ClientSession, tool_name: str, arguments: dict) -> str:
    """Call a named MCP tool and return the text response."""
    result = await session.call_tool(tool_name, arguments=arguments)
    parts = []
    for block in result.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)
