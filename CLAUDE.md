# JiraSlack Integration

**Purpose:** This file defines how this project is built and maintained. Read it at the start of every session to stay aligned with the project's architecture, conventions, and workflow.

---

## What This Project Does

JiraSlack is an AI triage agent that monitors a Slack channel for bug reports, feature requests, and tasks. It automatically classifies messages using GPT-4o and creates Jira tickets — without human intervention when the message is clear enough.

**The pipeline:**
```
Slack channel messages
    → fetch & group into conversation blocks (by time window)
    → send each block to GPT-4o with tools
    → GPT-4o decides: create_jira_ticket / post_slack_message / ask_for_clarification
    → agent executes the tool
    → result posted back to Slack
```

**Entry point:** `python run_triage.py`

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| LLM | OpenAI GPT-4o (via `openai` SDK) |
| Slack | Slack MCP server (`@modelcontextprotocol/server-slack`) |
| Jira | Jira REST API v3 (Basic Auth: email + API token) |
| Config | `python-dotenv` → `config/.env` → `config/settings.py` |
| Tests | pytest + pytest-asyncio |

---

## Project Structure

```
JiraSlack/
├── run_triage.py                        # Entry point
├── config/
│   ├── .env                             # Secrets (never commit)
│   └── settings.py                      # Typed settings class
├── pipeline/
│   ├── slack_reader.py                  # Fetch & filter messages via Slack MCP
│   └── context_builder.py               # Group messages into time-window blocks
├── agents/
│   └── triage/
│       ├── triage_agent.py              # Main LLM loop orchestrator
│       ├── classifier.py                # Standalone classifier (unused, DEBT-001)
│       └── tools/
│           ├── jira_tools.py            # create_jira_ticket tool + schema
│           └── slack_tools.py           # post_slack_message + ask_for_clarification
├── memory/                              # Phase 6 — agent memory (planned)
│   ├── episodic_store.py                # Past ticket decisions — duplicate prevention
│   ├── semantic_store.py                # Learned patterns — injected into system prompt
│   ├── ticket_embeddings.json           # Phase 2 — embedding cache for open Jira tickets
│   └── agent_memory.db                  # SQLite — episodic + semantic tables (never commit)
├── mcp_servers/                         # MCP server wrappers
├── tests/
│   ├── unit/                            # 47 unit tests — all I/O mocked
│   └── integration/                     # MCP connection tests — read-only
├── docs/
│   └── plans/                           # Design docs and implementation plans
├── .agent/
│   └── workflows/                       # /brainstorm, /plan, /build, /audit, /kaizen, /closeout
├── PROJECT_HISTORY.md                   # Session log and key decisions
└── PROJECT_ROADMAP.md                   # Phases and what's next
```

---

## Key Conventions

### Tool Pattern (jira_tools.py / slack_tools.py)
Every tool has two parts — always keep them together in the same file:
1. `*_SCHEMA` — the JSON schema OpenAI reads to know the tool exists
2. `async def tool_name()` — the Python function that actually executes it

### Settings
All config comes from `config/.env` via `config/settings.py`. Never hardcode secrets. Never read `os.getenv()` directly outside `settings.py`.

### Async
The entire agent pipeline is `async`. All tool executors must be `async def`. Entry point uses `asyncio.run()`.

### Error Handling
- Jira calls use `jira_mcp_session()` — let errors bubble up clearly
- Slack calls go through the MCP session context manager (`slack_mcp_session()`)

### Memory Architecture (Phase 6)
The agent has four memory types. Two already exist; two will be built in Phase 6:

| Type | What it stores | Implemented as | Status |
|---|---|---|---|
| **Working** | Live LLM `messages` list for one block | `messages` list in `_classify_block()` | ✅ exists |
| **Procedural** | Classification rules, when to ask for clarification | System prompt in `triage_agent.py` | ✅ exists |
| **Episodic** | Specific past decisions: message → ticket key, type, priority, confidence | `memory/episodic_store.py` + `episodic` table in `agent_memory.db` | Phase 6 |
| **Semantic** | Patterns extracted from many episodes: "login → Bug/High 87% of cases" | `memory/semantic_store.py` + `semantic` table in `agent_memory.db` | Phase 6 |

**What GPT-4o receives per call (Phase 6):**
```python
messages=[
    {"role": "system",  "content": system_prompt + "\n\nLearned from past:\n" + semantic_patterns},
    {"role": "user",    "content": block_text},
    # working memory appended below during the loop:
    {"role": "assistant", "tool_calls": [...]},
    {"role": "tool",      "content": "Created SCRUM-7..."},
]
tools=[CREATE_JIRA_TICKET_SCHEMA, POST_SLACK_MESSAGE_SCHEMA, ASK_FOR_CLARIFICATION_SCHEMA]
```

**Episodic memory is a pre-LLM gate** — if a duplicate is found in the episodic store, GPT-4o is never called for that block. Memory check happens before the LLM loop, not inside it.

**Duplicate detection (Phase 2) uses an embedding cache** (`memory/ticket_embeddings.json`):
- At run start: fetch open ticket keys + timestamps from Jira, add new, re-embed changed, remove closed
- Comparison: cosine similarity between Slack block embedding and cached ticket embeddings
- Threshold: `DUPLICATE_SIMILARITY_THRESHOLD` (default 0.85) — configurable via `.env`
- Cache written immediately when agent creates a new ticket — no lag for agent-created tickets
- Fetch runs in parallel with Slack fetch via `asyncio.gather()` — zero added wall-clock time

**Storage:** Single SQLite file `memory/agent_memory.db` — never commit this file (add to `.gitignore`). Two tables: `episodic` (one row per decision) and `semantic` (one row per learned pattern, minimum 5 supporting episodes before injection).

### Tests
- Location: `tests/unit/` (mocked) and `tests/integration/` (real MCP, read-only)
- Run with: `pytest tests/unit/ -v` or `pytest -v` for the full suite
- Async tests need `@pytest.mark.asyncio` (requires `pytest-asyncio` installed)
- `_execute_decisions()` calls `create_jira_ticket`, `ask_for_clarification`, `post_slack_message` directly, mock each at its own import site, not through a dict

---

## Writing Style

Do not use em dashes (—) in any documentation, README, architecture doc, plan, or code comment in this repository. Use a period, comma, colon, semicolon, or parentheses instead, whichever fits the sentence.

---

## Agent Behaviour Settings

| Setting | Default | Meaning |
|---|---|---|
| `CONFIDENCE_AUTO_ACT` | 0.90 | Above this → agent acts without asking |
| `CONFIDENCE_ASK_HUMAN` | 0.65 | Below this → agent asks for clarification |
| `CONTEXT_WINDOW_MINUTES` | 5 | Group messages within this time window |
| `MAX_MESSAGES_TO_FETCH` | 20 | How many recent Slack messages to read |

---

## Development Workflow

Every feature follows this 7-step process in strict order:

| Step | Command | Mode | What it produces |
|---|---|---|---|
| 1 | `/brainstorm` | Strategic | Customer problem, goals, success metrics → `*-brainstorm.md` |
| 2 | `/design` | Technical | System design, code diagram, failure modes → `*-design.md` + `*-diagram.md` |
| 3 | `/plan` | Execution planning | TDD chunks with exact files, tests, commits → `*-plan.md` |
| 4 | `/build` | Execution | RED→GREEN→REFACTOR→COMMIT per chunk |
| 5 | `/audit` | Quality gate | Tests pass + behaviour verified vs success metrics |
| 6 | `/kaizen` | Clean up | Entropy removed, debt logged |
| 7 | `/closeout` | Wrap up | Roadmap updated, history written, committed |

Workflow files live in `.agent/workflows/`. Each command reads the corresponding file and follows it.

**Hard rules:**
- Never start `/design` without an approved brainstorm doc
- Never start `/plan` without an approved design doc and diagram
- Never start `/build` without an approved plan
- Never run `/closeout` without a passing `/audit`

**The separation of concerns:**
- `/brainstorm` = WHAT and WHY (no implementation details)
- `/design` = HOW (no code, just design)
- `/plan` = WHEN and IN WHAT ORDER (break design into executable chunks)
- `/build` onwards = execution

---

## Workflow Contracts

> These are the project-specific values that all `.agent/workflows/` files reference.
> When adopting this SDLC for a new project, fill in this section first.
> The workflow files themselves are generic — they read from here.

### Project Identity
- **Name:** JiraSlack
- **One-liner:** AI triage agent that reads Slack and creates Jira tickets
- **Entry point:** `python run_triage.py`
- **Language / runtime:** Python 3.11

### Test Runner
- **Framework:** `pytest` + `pytest-asyncio`
- **Unit tests:** `pytest tests/unit/ -v`
- **Integration tests:** `pytest tests/integration/ -v`
- **Full suite:** `pytest -v`
- **E2E test:** `python run_triage.py` (manual, verified against real Slack + Jira)

### Key Modules
List the core files any new developer or workflow step should know about:
- `run_triage.py` — entry point; orchestrates eval pre-step → triage → eval post-step
- `pipeline/slack_reader.py` — fetch and filter Slack messages
- `pipeline/context_builder.py` — group messages into conversation blocks
- `pipeline/run_logger.py` — structured run logging (RunLog, BlockResult, LlmStats)
- `pipeline/duplicate_detector.py` — pre-ticket duplicate gate (embeddings + cosine similarity)
- `pipeline/eval_runner.py` — Phase 5 eval lifecycle owner (pre/post-triage hooks)
- `pipeline/judge_calibration.py` — calibrate LLM judge vs `tests/eval/label_fixtures.json`; CLI: `python run_judge_calibration.py` (`--mode gold|mismatch|both`). Playbook: `tests/eval/FIXTURES_GUIDE.md` Part A–D; learnings pointer: `docs/LEARNINGS.md` (session 2026-04-30).
- `pipeline/quality_metrics.py` — Phase 5 quality store I/O, metric computation, alert logic
- `pipeline/reaction_collector.py` — Phase 5 Slack reaction fetcher (one MCP call per run)
- `pipeline/episode_store.py` — Phase 7 episodic memory: `Episode`/`EpisodeStore`, load/save/add/retrieve/format
- `pipeline/semantic_store.py` — Phase 7 semantic memory: `Pattern`/`SemanticStore`, extract/summarise/inject
- `pipeline/memory_runner.py` — Phase 7 memory lifecycle: `MemoryContext`, `pre_run()`, `post_run()`
- `agents/triage/triage_agent.py` — main LLM orchestrator; uses `_provider` (Phase 8 agnostic interface)
- `agents/llm/base.py` — `LLMProvider` protocol, `LLMTurn`, `ToolCall`, `LLMProviderError` (Phase 8)
- `agents/llm/openai_provider.py` — `OpenAIProvider` — wraps OpenAI SDK (Phase 8)
- `agents/llm/anthropic_provider.py` — `AnthropicProvider` — wraps Anthropic Messages API; converts OpenAI-format tools/messages internally (Phase 9)
- `agents/llm/factory.py` — `get_llm_provider(settings)` — selects provider from `LLM_PROVIDER` (`anthropic` default, `openai` optional)
- `agents/triage/triage_agent.py` — main orchestrator; returns `RunLog`
- `agents/triage/tools/jira_tools.py` — Jira ticket creation tool (via `jira_mcp_session()`)
- `agents/triage/tools/slack_tools.py` — Slack posting tools + `_confirmation_ts_buffer` + `drain_confirmation_ts()`
- `config/settings.py` — all configuration (read from `.env`)
- `conftest.py` — loads `config/.env` before all tests
- `dashboard.py` — Streamlit observability dashboard (run history + quality trend)
- `tests/unit/` — 206 unit tests, all I/O mocked
- `tests/integration/` — integration tests for Jira + Slack MCP connections (read-only)
- `memory/ticket_embeddings.json` — embedding cache for open Jira tickets; refreshed at run start
- `memory/quality_store.json` — Phase 5 rolling quality metrics + pending reactions; safe on missing file
- `memory/episode_store.json` — Phase 7 episodic memory store; pruned to `MAX_EPISODES`; safe on missing file
- `memory/semantic_store.json` — Phase 7 semantic pattern store; safe on missing file

### External Dependencies (What to Mock in Tests)
- **Jira MCP** — `jira_mcp_session()` context manager → `uvx mcp-atlassian` stdio subprocess
- **Slack MCP** — `slack_mcp_session()` context manager → `npx @modelcontextprotocol/server-slack` stdio subprocess
- **LLM Provider** — patch `agents.triage.triage_agent._provider`; set `mock_provider.chat = AsyncMock(return_value=LLMTurn(...))` — do NOT patch `_client` (removed in Phase 8)
- **LLMProviderError** — use `LLMProviderError("msg")` as `side_effect` for Rule 6 tests (replaces `openai.APIConnectionError`)
- **OpenAI Embeddings** — `pipeline.duplicate_detector._embed_client` (patch at `pipeline.duplicate_detector._embed_client`)
- **`_execute_decisions()` dispatch** — no dict lookup anymore; it calls `create_jira_ticket`, `ask_for_clarification`, `post_slack_message` directly, so mock each at its `agents.triage.triage_agent.<name>` import site like any other direct-function mock
- **Phase 4 run() deps** — `fetch_open_tickets`, `load_embedding_cache`, `build_embedding_cache`, `embed_texts`, `find_duplicate`, `add_ticket_to_cache` — all added to `patch_run_deps()` helper in `tests/unit/test_triage_agent.py`
- **Phase 5 eval** — `pipeline.eval_runner.run_eval_step` (patch as `run_triage.run_eval_step` for run_triage tests); `pipeline.quality_metrics.load_quality_store`, `save_quality_store`, `add_pending_from_run`, `apply_collected`, `should_alert`; `pipeline.reaction_collector.fetch_reactions_for_pending`; `agents.triage.tools.slack_tools._confirmation_ts_buffer` (clear at test start)
- **Phase 7 memory** — `pipeline.episode_store.load_episode_store`, `save_episode_store`, `add_episode`, `retrieve_similar`, `format_episode_context`; `pipeline.semantic_store.load_semantic_store`, `save_semantic_store`, `extract_count_patterns`, `summarise_with_llm`, `build_semantic_injection`; `pipeline.memory_runner.pre_run`, `post_run`; patch `run_triage.memory_runner.pre_run` / `post_run` for run_triage tests. Do NOT mock `add_episode` (pure list-append, no I/O) — let it run.

### Coding Conventions
- All config via `config/settings.py` — never `os.getenv()` directly
- All Slack calls via `slack_mcp_session()` context manager
- All Jira calls via `jira_mcp_session()` context manager → `uvx mcp-atlassian` stdio subprocess
- All tool executors must be `async def` and return `str`
- New tools: `*_SCHEMA` dict + executor in the same file

### Commit Message Format
```
[Add]      — new capability
[Fix]      — bug fix
[Refactor] — structural improvement, no behaviour change
[Test]     — test-only change
[Docs]     — documentation only
[Kaizen]   — cleanup, entropy reduction
[Closeout] — session wrap-up docs
```

### E2E Verification Checklist
The scenarios to manually verify in `/audit` Part 3:
- Clear bug report in Slack → Jira Bug ticket created, link posted to Slack
- Vague message → ticket created + INVEST prompt posted in Slack
- Low confidence classification → ticket created + confidence flag posted
- Duplicate detected → match posted in Slack, human asked to confirm
- Jira API down → error posted in Slack, no silent failure
- OpenAI API down → error posted in Slack, team told to triage manually
- Same messages processed twice → no new tickets, duplicate rule applied

---

## Priority Rules

These are project-wide decisions made once. Every workflow, every feature, and every design decision must respect them. They are the tiebreakers — when two approaches conflict, these rules resolve it without re-debating.

**Rule 1 — Jira unavailable**
Transparency wins. Post in Slack so the team member knows the ticket was not created. Never fail silently.

**Rule 2 — Message is vague**
Ask, don't guess. Call `ask_for_clarification` and post a question in Slack asking for the missing details — no ticket is created for an Unclear message. This blocks on missing information by design: the agent would rather ask than file a low-quality ticket. (Note: this rule's wording previously said "create immediately, then prompt" — that was aspirational and never matched what `ask_for_clarification` actually does; corrected 2026-08-03 to describe the real behavior.)

**Rule 3 — Classification confidence is 0.65–0.90 (Phase 10)**
Create with flag. `route_confidence()` routes this band to `create_jira_ticket` with a `needs-review` label added and a confidence note appended to the tool's result (e.g. "flagged for review — confidence 0.78"), which the LLM then reports back to Slack via `post_slack_message`. Below 0.65, no ticket is created at all — see the confidence-based routing section in `docs/plans/2026-08-03-confidence-routing-design.md` for the full three-tier behavior. (Note: this rule's wording previously described a specific structured "INVEST prompt" message that was never implemented; corrected 2026-08-03 to describe the real behavior.)

**Rule 4 — Duplicate detected**
Human confirms. Post in Slack with the match found, the existing ticket link, and let the team member decide whether it's the same issue or a new one. Never silently skip.

**Rule 5 — Slack MCP fails mid-run**
Continue and report. Keep processing all remaining blocks. At the end, post one consolidated summary: what succeeded, what failed, the specific error detail, and which tickets need manual notification.

**Rule 6 — OpenAI API unavailable**
Fail loudly in Slack. Post the specific error and instruct the team to triage manually or retry. Never exit silently.

**Rule 7 — Multiple people report the same bug**
First ticket wins, second reporter gets notified. Apply Rule 4 (duplicate detection with human confirmation) for the second report. The first ticket stands.

**Rule 8 — Quality metrics cold start**
Never fire a quality alert until at least `MIN_REACTIONS_FOR_QUALITY` total reactions have been collected across all runs. A new deployment starts with zero signal — treat it as "warming up", not "bad quality".

**Rule 9 — Missing Slack `message_ts` in MCP response**
Silently skip. If the Slack MCP does not return a `ts` field in the `post_slack_message` response, the confirmation post is simply excluded from reaction tracking. No error is logged, no alert is fired. Missing ts ≠ bad ticket.

**Rule 10 — Semantic extraction LLM call fails**
Fall back silently to count-based patterns only. `summarise_with_llm` catches all exceptions, logs a warning, and returns the raw count-based `SemanticStore`. The system prompt injection continues with the count-based summary — the agent keeps working.

**Rule 11 — Episode retrieval returns no matches**
Continue with no episodic injection. If `retrieve_similar` returns an empty list (no episodes yet, or all below similarity threshold), `format_episode_context` returns an empty string and the user message is sent without the episode context block. This is the expected cold-start state.

---

## Running the Agent

```bash
cd /Users/pavanibayappu/JiraSlack
python run_triage.py
```

**What it does:**
1. `memory_runner.pre_run()` — load episodic + semantic stores, build `MemoryContext` (semantic injection string + episode store)
2. Runs Phase 5 eval pre-step: collects Slack reactions from prior runs, computes quality metrics, alerts if rate below threshold
3. Reads last `MAX_MESSAGES_TO_FETCH` messages from `SLACK_CHANNEL_ID`
4. Groups them into conversation blocks by `CONTEXT_WINDOW_MINUTES`
5. For each block: runs duplicate gate, then one structured LLM call (Claude by default) with memory context injected, returning a list of decisions
6. Deterministic code executes each decision, creates Jira tickets and posts confirmations to Slack, no further model involvement
7. Runs Phase 5 eval post-step: registers new confirmation posts for reaction polling next run; if `ENABLE_LLM_JUDGE=true`, runs LLM-as-Judge on each ticket created and appends scores to `memory/judge_store.json`
8. `memory_runner.post_run(run_log)` — writes new episodes, extracts/summarises semantic patterns when threshold reached

---

## Environment Variables Required

```
OPENAI_API_KEY              — GPT-4o access + text-embedding-3-small embeddings
SLACK_BOT_TOKEN             — Slack bot with channel read/write permissions
SLACK_CHANNEL_ID            — Channel to monitor
JIRA_URL                    — e.g. https://yoursite.atlassian.net
JIRA_EMAIL                  — Atlassian account email (must match token owner)
JIRA_API_TOKEN              — Generated at id.atlassian.com/manage-profile/security/api-tokens
JIRA_PROJECT_KEY            — e.g. SCRUM
DUPLICATE_SIMILARITY_THRESHOLD — (optional) cosine similarity cutoff, default 0.85
EMBEDDING_MODEL             — (optional) OpenAI embedding model, default text-embedding-3-small
EMBEDDING_CACHE_PATH        — (optional) path to embedding cache JSON, default memory/ticket_embeddings.json
JIRA_OPEN_TICKETS_LIMIT     — (optional) max open Jira tickets to fetch per page, default 100
JIRA_MAX_PAGES              — (optional) max pages to paginate for open tickets, default 10 (= up to 1000 tickets)
QUALITY_ALERT_THRESHOLD     — (optional) thumbs-up rate below which a quality alert fires, default 0.70
MIN_REACTIONS_FOR_QUALITY   — (optional) total reactions required before any alert fires, default 5
REACTION_WINDOW_HOURS       — (optional) how far back to look for reactions, default 48
REACTION_HISTORY_LIMIT      — (optional) max Slack messages to fetch for reaction polling, default 50
QUALITY_STORE_PATH          — (optional) path to quality store JSON, default memory/quality_store.json
LLM_PROVIDER                — (optional) LLM backend: "anthropic" (default) or "openai"
LLM_MODEL                   — (optional) model for the configured provider; default claude-sonnet-4-5-20250929
LLM_MAX_TOKENS              — (optional) max tokens for Anthropic Messages API, default 4096
ENABLE_LLM_JUDGE            — (optional) `true` / `1` / `yes` to score each created ticket after the run (extra LLM calls); default false
JUDGE_LLM_MODEL             — (optional) judge model when judge enabled, default gpt-4o-mini
JUDGE_LLM_PROVIDER          — (optional) judge backend, default `openai` (cross-family vs Claude triage); also supports `anthropic`
JUDGE_STORE_PATH            — (optional) append-only judge scores JSON, default memory/judge_store.json
EPISODE_STORE_PATH          — (optional) path to episodic memory JSON, default memory/episode_store.json
SEMANTIC_STORE_PATH         — (optional) path to semantic memory JSON, default memory/semantic_store.json
MAX_EPISODES                — (optional) max episodes to retain in episodic store, default 500
MAX_INJECTED_EPISODES       — (optional) max episodes to inject per block, default 3
SEMANTIC_EXTRACTION_THRESHOLD — (optional) min new episodes since last extraction before re-extracting, default 5
SEMANTIC_LLM_MIN_PATTERNS   — (optional) min patterns before triggering LLM summarisation, default 3
MAX_SEMANTIC_PATTERN_CHARS  — (optional) max chars for semantic injection string, default 800
ANTHROPIC_API_KEY           — Claude access when LLM_PROVIDER=anthropic (default)
OPENAI_API_KEY              — embeddings always; also triage when LLM_PROVIDER=openai; judge when JUDGE_LLM_PROVIDER=openai
```
