"""
Integration tests for Jira MCP (mcp-atlassian via uvx).

These tests make a REAL connection to the Jira MCP subprocess.
They do NOT create or modify any Jira tickets.

Run with:
    pytest tests/integration/ -v
"""
import pytest
from agents.triage.tools.jira_tools import jira_mcp_session, JIRA_CREATE_TOOL


@pytest.mark.asyncio
async def test_jira_mcp_session_connects_successfully():
    """The MCP subprocess starts and the session initialises without error."""
    async with jira_mcp_session() as session:
        assert session is not None


@pytest.mark.asyncio
async def test_jira_mcp_exposes_create_issue_tool():
    """The expected create tool is present in the tool list."""
    async with jira_mcp_session() as session:
        result = await session.list_tools()
        tool_names = [t.name for t in result.tools]
        assert JIRA_CREATE_TOOL in tool_names, (
            f"Expected '{JIRA_CREATE_TOOL}' in tools, got: {tool_names}"
        )


@pytest.mark.asyncio
async def test_jira_mcp_exposes_search_tool():
    """jira_search is available — needed for Phase 2 duplicate detection."""
    async with jira_mcp_session() as session:
        result = await session.list_tools()
        tool_names = [t.name for t in result.tools]
        assert "jira_search" in tool_names, (
            f"Expected 'jira_search' in tools, got: {tool_names}"
        )


@pytest.mark.asyncio
async def test_jira_mcp_lists_at_least_ten_tools():
    """Sanity check — a healthy mcp-atlassian connection exposes many tools."""
    async with jira_mcp_session() as session:
        result = await session.list_tools()
        assert len(result.tools) >= 10
