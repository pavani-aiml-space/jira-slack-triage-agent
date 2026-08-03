# jira-slack-triage-agent
AI agent that turns Slack messages into Jira tickets with human-in-the-loop on low-confidence cases, duplicate detection, and an eval framework (golden dataset + LLM as a judge) for measuring ticket quality. Built on MCP, using the official Slack MCP server and mcp-atlassian via a swappable multi-server client.
