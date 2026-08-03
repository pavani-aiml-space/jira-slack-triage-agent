# Write Tests — JiraSlack

Follow every step below. Do not skip.

---

## Step 1 — Setup Check

Confirm the following before writing any tests:

**`pytest.ini`** at project root must contain:
```ini
[pytest]
asyncio_mode = auto
```

**`conftest.py`** at project root must load `.env`:
```python
from dotenv import load_dotenv
load_dotenv("config/.env")
```

Run: `pytest --co -q` to verify tests are collected (not skipped).

---

## Step 2 — Identify the Test Type

| Type | Location | When to Use |
|---|---|---|
| Unit | `tests/unit/` | Pure logic, all I/O mocked, fast |
| Integration | `tests/integration/` | Real MCP subprocess, read-only, no writes |
| E2E | Manual (`python run_triage.py`) | Full pipeline, real Slack + Jira |

---

## Step 3 — Unit Tests

**One rule:** One behavior per test, one reason to fail. If the description needs "and" → split it.

**What to always cover:**
- Return value of the public function
- Correct tool name called on the mock session
- Correct arguments passed (especially `additional_fields` JSON for Jira)
- Edge cases: empty input, None, bad JSON
- Error paths: unknown tool name, MCP failure

**What to skip:**
- Trivial attribute access with no logic
- That `json.loads` or `dotenv` works
- Config file loading (test that settings are *used*, not that they load)

### Mock Patterns

**Async MCP session (Jira):**
```python
def make_mcp_response(payload: dict) -> AsyncMock:
    block = MagicMock()
    block.text = json.dumps(payload)
    result = MagicMock()
    result.content = [block]
    session = AsyncMock()
    session.call_tool = AsyncMock(return_value=result)
    return session

def patch_jira_session(session):
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("agents.triage.tools.jira_tools.jira_mcp_session", return_value=mock_ctx)
```

Patch path = where it's *imported*, not where it's *defined*.

**OpenAI client:**
```python
with patch("agents.triage.triage_agent._client") as mock_client:
    mock_client.chat.completions.create = MagicMock(return_value=response)
```

**File map:**
```
tests/unit/
├── test_context_builder.py   ← pure Python, no mocks needed
├── test_jira_tools.py        ← mock jira_mcp_session
├── test_slack_tools.py       ← mock slack_mcp_session
├── test_slack_reader.py      ← mock slack_mcp_session
└── test_triage_agent.py      ← mock _client + all tool executors
```

Run: `pytest tests/unit/ -v`

---

## Step 4 — Integration Tests

Real MCP subprocess spawns. No mocks. **Never create tickets or post messages.**

**Hard rules:**
- No writes — never call `jira_create_issue` or `slack_post_message`
- Real `.env` required — tests need valid tokens
- Skip if token missing:
```python
pytestmark = pytest.mark.skipif(
    not os.getenv("JIRA_API_TOKEN"),
    reason="JIRA_API_TOKEN not set"
)
```

**What to assert:**
```python
tool_names = [t.name for t in tools]
assert "jira_create_issue" in tool_names
assert "jira_search" in tool_names
assert len(tool_names) >= 2
```

**Jira MCP command** (from `jira_mcp_session()`):
```
uvx mcp-atlassian --jira-url <URL> --jira-username <EMAIL> --jira-token <TOKEN>
```
⚠️ Flag is `--jira-token`, not `--jira-api-token`.

**Slack MCP command** (from `slack_mcp_session()`):
```
npx -y @modelcontextprotocol/server-slack
```
Env: `SLACK_BOT_TOKEN`, `SLACK_TEAM_ID`

**File map:**
```
tests/integration/
├── test_jira_mcp_connection.py    ← connect + list tools only
└── test_slack_mcp_connection.py   ← connect + list tools + fetch_messages read
```

Run: `pytest tests/integration/ -v`

---

## Step 5 — E2E Verification (Manual)

Run: `python run_triage.py`

Post a message in Slack first, then run the agent and verify:

| Scenario | Input | Expected |
|---|---|---|
| Clear bug report | "Login button crashes on mobile" | Jira Bug created, link posted in Slack |
| Vague message | "Something is broken" | Ticket created + INVEST prompt in Slack |
| Low confidence | Ambiguous feature vs bug | Ticket created + "not fully confident" flag in Slack |
| Duplicate detected | Same bug reported twice | Match posted, existing ticket linked, human asked |
| Jira MCP down | Set `JIRA_API_TOKEN=invalid` | Error posted in Slack, no silent failure |
| OpenAI down | Set `OPENAI_API_KEY=sk-invalid` | Error + manual triage instruction in Slack |
| Run twice | Same messages reprocessed | No new tickets created |

All 7 must pass before `/audit` can be signed off.

---

## Step 6 — Coverage Philosophy

| Layer | Target | Method |
|---|---|---|
| Unit | Every public function, every branch | `pytest tests/unit/` |
| Integration | Every MCP connection point | `pytest tests/integration/` |
| E2E | Every priority rule (Rules 1–7) | Manual `python run_triage.py` |

Do not test framework internals. Test what the *agent does*, not that Python works.
