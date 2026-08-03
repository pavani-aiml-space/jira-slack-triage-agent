# Technical Design: Core Pipeline — Slack → AI → Jira

> Status: **Approved** 
> Phase: 1 — Core Pipeline
> Brainstorm: [2026-04-25-core-pipeline-brainstorm.md](2026-04-25-core-pipeline-brainstorm.md)

---

## Problem 

Engineers manually convert Slack messages into Jira tickets — a slow, inconsistent process that misses issues and delays sprint planning.

---

## Approach

** GPT-4o Tool-Calling Loop.**

GPT-4o is given three tools (`create_jira_ticket`, `post_slack_message`, `ask_for_clarification`) and decides which to call based on the content of each Slack conversation block. The agent loops until `finish_reason: stop`.

Why chosen:
- No custom classification logic needed — GPT-4o handles ambiguity and edge cases through the system prompt
- Tool-calling pattern is idiomatic for agentic systems and trivially extensible (add a tool = add a capability)
- Satisfies Priority Rules 1, 2, and 6 — failures are handled by posting to Slack, not crashing silently

---

## Components

### New Files

| File | Purpose |
|---|---|
| `config/settings.py` | All configuration, typed, loaded from `config/.env` via `python-dotenv` |
| `config/.env` | Secrets — never committed |
| `run_triage.py` | Entry point — bridges sync shell to async agent via `asyncio.run(run())` |
| `pipeline/slack_reader.py` | Fetch and filter raw Slack messages via MCP stdio subprocess |
| `pipeline/context_builder.py` | Group messages into 5-minute time-window conversation blocks |
| `agents/triage/triage_agent.py` | Main orchestrator — system prompt, tool registry, LLM loop, tool dispatch |
| `agents/triage/tools/jira_tools.py` | `create_jira_ticket` JSON schema + async executor |
| `agents/triage/tools/slack_tools.py` | `post_slack_message` + `ask_for_clarification` JSON schemas + async executors |

### Modified Files

None — greenfield project.

---

## Code Diagram

See: [docs/diagrams/2026-04-25-core-pipeline.md](../diagrams/2026-04-25-core-pipeline.md)
> Includes: ASCII overview, Mermaid sequence diagram, decision flowchart, data flow table.

---

## Data Contracts

```python
# slack_reader.py
async def fetch_messages(channel_id: str) -> list[dict]:
    # Returns: [{user: str, text: str, ts: str}]
    # Ordered: oldest first
    # Filtered: empty messages and system events (subtype != None) removed

async def slack_mcp_session() -> AsyncContextManager[ClientSession]:
    # Context manager — spawns npx MCP subprocess, yields live session

# context_builder.py
def build_context_blocks(messages: list[dict]) -> list[dict]:
    # Returns: [{messages: list[dict], combined_text: str, start_ts: str, end_ts: str}]
    # Groups: messages within CONTEXT_WINDOW_MINUTES (default 5) into one block

def _make_block(messages: list[dict]) -> dict:
    # Returns one block dict from a list of messages

# triage_agent.py
async def run() -> None:
    # Orchestrates: fetch → group → LLM loop per block

async def _run_llm_loop(block_text: str) -> None:
    # Sends block to GPT-4o, loops until finish_reason == "stop"
    # Max iterations: MAX_AGENT_ITERATIONS (default 10)

async def _execute_tool(tool_name: str, tool_args: dict) -> str:
    # Dispatches to executor; returns string result for LLM message history

# jira_tools.py
async def create_jira_ticket(
    summary: str,           # max 80 chars, imperative tone
    issue_type: str,        # "Bug" | "Story" | "Task"
    priority: str,          # "Critical" | "High" | "Medium" | "Low"
    description: str,       # structured: What, Steps, Expected, Context
    labels: list[str] | None = None,
) -> str:
    # Returns: "Created SCRUM-3: Fix login crash → https://..."

# slack_tools.py
async def post_slack_message(message: str) -> str:
    # Returns: "Message posted: <message>"

async def ask_for_clarification(question: str) -> str:
    # Posts: "🤔 <question>" to channel
    # Returns: "Clarification asked: <question>"
```

---

## External Calls

| Service | Protocol | Called From | Endpoint / Tool | Auth | Payload Shape | Response Shape |
|---|---|---|---|---|---|---|
| Slack MCP | stdio (npx subprocess) | `slack_reader.py` | `slack_get_channel_history` | `SLACK_BOT_TOKEN` env var | `{channel_id: str, limit: int}` | `{messages: [{text, user, ts, subtype}]}` |
| Slack MCP | stdio (npx subprocess) | `slack_tools.py` | `slack_post_message` | `SLACK_BOT_TOKEN` env var | `{channel_id: str, text: str}` | ack (not used) |
| OpenAI | HTTPS | `triage_agent.py` | `POST /v1/chat/completions` | `OPENAI_API_KEY` Bearer | `{model, tools, messages}` | `{choices: [{finish_reason, message}]}` |
| Jira MCP | stdio (uvx subprocess) | `jira_tools.py` | `jira_create_issue` | `--jira-username` + `--jira-token` passed as CLI args to subprocess | `{project_key, summary, issue_type, description, additional_fields (JSON)}` | `{issue: {key: "SCRUM-3", ...}}` |

---

## Failure Modes

### Slack MCP — `fetch_messages()`
| Failure | What happens | Priority Rule |
|---|---|---|
| MCP subprocess fails to start (missing npx, bad token) | Exception propagates → agent crashes before any Slack notification | Rule 5 partial gap — consolidated error not posted |
| `SLACK_BOT_TOKEN` missing | MCP subprocess exits immediately → exception raised | — |
| Empty channel / no messages | Returns `[]` → 0 blocks → agent exits cleanly with "0 blocks" log | — |

### OpenAI API — `_run_llm_loop()`
| Failure | What happens | Priority Rule |
|---|---|---|
| `OPENAI_API_KEY` invalid | `openai.AuthenticationError` raised → agent crashes | Rule 6: should post to Slack — currently a gap |
| Rate limit / timeout | SDK raises exception → agent crashes | Rule 6: should post to Slack — currently a gap |
| Unexpected `finish_reason` | Loop continues silently to next iteration | Low risk — GPT-4o always returns "stop" or "tool_calls" |
| Max iterations reached | Loop exits after 10 iterations without explicit error | Low risk in practice |

### Jira MCP — `create_jira_ticket()`
| Failure | What happens | Priority Rule |
|---|---|---|
| MCP subprocess fails to start (missing uvx, bad credentials) | Exception propagates → LLM loop crashes | Rule 1: should post "Jira unavailable" to Slack — currently a gap (→ US2.1) |
| `--jira-username` or `--jira-token` missing/wrong | MCP subprocess fails to authenticate → exception raised in `jira_mcp_session()` | Rule 1 gap (→ US2.1) |
| `jira_create_issue` tool call fails (project key wrong, field validation) | Exception propagates → LLM loop crashes | Rule 1 gap (→ US2.1) |
| Ticket key missing from MCP response | Returns `"Created unknown: <summary>"` | Acceptable degradation |

### Known Gaps vs Priority Rules
> Tracked in `PROJECT_ROADMAP.md` Phase 2 — Epic E2 (US2.1, US2.2, US2.3)

- **Rule 1 gap (→ US2.1):** Jira failures don't post a Slack notification before crashing
- **Rule 5 gap (→ US2.3):** Slack MCP failures don't produce a consolidated summary
- **Rule 6 gap (→ US2.2):** OpenAI failures don't post to Slack before crashing

---

## Out of Scope

- Duplicate ticket detection (Phase 2 — E1)
- State tracking / last-processed timestamp (Phase 3 — E2)
- Scheduled / continuous execution (Phase 3 — E3)
- Structured logging and run summaries (Phase 4 — E4)
- Auto-assignment to engineers
- Multi-channel support
- Sprint assignment for Stories

---

## Open Questions Resolved

| Question | Answer |
|---|---|
| Classification engine: tool-calling vs standalone classifier? | Tool-calling loop — handles ambiguity natively; `classifier.py` exists as DEBT-001 |
| Slack: MCP or direct SDK? | MCP — already available; if deprecated, replace with `slack_sdk` (DEBT-004) |
| Jira: MCP or REST? | MCP (`uvx mcp-atlassian`, stdio) — consistent with Slack pattern; no custom httpx/auth code |
| Time window for grouping? | 5 minutes default (`CONTEXT_WINDOW_MINUTES`), configurable via `.env` |
| Confidence thresholds? | Auto-act ≥ 0.90, ask human < 0.65, configurable via `.env` |
| Jira auth | `JIRA_EMAIL` + `JIRA_API_TOKEN` passed as env vars to the MCP subprocess — same pattern as Slack |
