# Project Roadmap — JiraSlack

---

## Vision

Eliminate the manual overhead of converting Slack conversations into Jira tickets so that engineering teams can focus entirely on building — not on administrative triage.

---

## Goals

1. **Auto-triage** — Classify and create Jira tickets from Slack messages without human intervention when the message is clear enough
2. **Speed** — Reduce the time from Slack message to Jira ticket from hours to seconds
3. **Quality** — Produce well-structured tickets with correct type, priority, labels, and description every time
4. **Trust** — The agent should know when it doesn't know — ask for clarification rather than guess

---

## Expectations

- Agent runs reliably on demand (`python run_triage.py`)
- Creates well-formed Jira tickets for bugs, stories, and tasks
- Never creates duplicate tickets for the same issue
- Asks for clarification when a message is too vague to act on
- Posts a confirmation back to Slack after every action

---

## Success Metrics

| Metric | Target | Current |
|--------|--------|---------|
| Classification accuracy | ≥ 90% of messages correctly typed | Untested |
| Auto-action rate | ≥ 80% of messages acted on without clarification | Untested |
| Duplicate ticket rate | 0% | ✅ Enforced — embedding gate (0.85 threshold) |
| Time to ticket | < 30 seconds from run to Jira | ~15s ✅ |
| Clarification quality | Agent asks useful, specific questions | Qualitative |

---

## Capabilities

What the system needs to be able to do to achieve the goals:

| # | Capability | Status |
|---|-----------|--------|
| C1 | Read Slack channel messages | ✅ Done |
| C2 | Group messages into conversation context | ✅ Done |
| C3 | Classify messages (Bug / Story / Task / Unclear) | ✅ Done |
| C4 | Create Jira tickets with structured content | ✅ Done |
| C5 | Post confirmations back to Slack | ✅ Done |
| C6 | Ask for clarification when unclear | ✅ Done |
| C7 | Detect and skip duplicate tickets | ✅ Done |
| C8 | Track last-processed message (avoid reprocessing) | ⬜ Planned |
| C9 | Run continuously / on a schedule | ⬜ Planned |
| C10 | Observability — logs, summary reports, error alerts, dashboard | ✅ Done |
| C11 | Capture 👍/👎 Slack reactions as quality signal | ✅ Done |
| C12 | Compute agent quality metrics (accuracy, thumbs-up rate) | ✅ Done |
| C13 | Auto-tune confidence thresholds based on feedback | ⬜ Planned |
| C14 | Episodic memory — remember past decisions across runs | ✅ Done |
| C15 | Semantic memory — extract and inject learned patterns | ✅ Done |
| C16 | Working memory — formalize existing LLM message list as memory layer | ✅ Done |
| C17 | Labeled dataset + regression testing — ground truth eval with precision/recall/F1 | ⬜ Planned |

---

## Workstreams

| Workstream | Description | Capabilities |
|-----------|-------------|--------------|
| **WS1 — Core Pipeline** | Slack → AI → Jira end-to-end flow | C1, C2, C3, C4, C5, C6 |
| **WS2 — Intelligence** | Smarter classification, duplicate detection | C7 |
| **WS3 — Reliability** | Scheduling, state tracking, no reprocessing | C8, C9 |
| **WS4 — Observability** | Logging, summaries, alerting | C10 |
| **WS5 — Eval & Feedback** | Quality scoring, reaction capture, threshold auto-tuning, labeled dataset, regression testing | C11, C12, C13, C17 |
| **WS6 — Memory** | Episodic, semantic, and working memory layers | C14, C15, C16 |

---

## Phases & Milestones

> **Revised ordering (2026-04-29):** Original Phase 2 split and reordered.
> Rationale: Failure transparency is reactive + highest trust impact → build first.
> Observability gives run visibility → build second so Phase 4 duplicate detection can be verified.
> Reliability moved to Phase 5 — prerequisite for Eval (Phase 6), not earlier phases.

### ✅ Phase 1 — Core Pipeline
**Milestone:** First Jira ticket automatically created from a Slack message

- [x] C1 — Fetch Slack channel messages via MCP
- [x] C2 — Group messages into time-window conversation blocks
- [x] C3 — GPT-4o classifies message type and priority
- [x] C4 — Create Jira tickets via REST API (Bug, Story, Task)
- [x] C5 — Post ticket confirmation back to Slack
- [x] C6 — Ask for clarification when message is too vague

**First ticket created:** SCRUM-3 "Fix login crash on empty password"

---

### ✅ Phase 2 — Failure Transparency
**Milestone:** The agent never fails silently — every external service failure results in a Slack notification or stdout log

> Extracted from original Phase 2 E2. No prerequisites. Highest trust impact.

#### Epic E2 — Failure Transparency (Priority Rule Gaps)
> As an operator, I should always know when the agent fails — never through silence.
> Closes known gaps vs Priority Rules 1, 5, and 6 from the Phase 1 design doc.

- [x] **US2.1** — As the agent, when Jira is unavailable I catch the error, post a Slack notification ("Jira ticket could not be created — please create manually"), and continue processing remaining blocks *(Rule 1)*
- [x] **US2.2** — As the agent, when the OpenAI API is unavailable I catch the error, post a Slack notification with the specific error and instructions to triage manually, then exit cleanly *(Rule 6)*
- [x] **US2.3** — As the agent, when the Slack MCP fails mid-run I catch the error, continue processing all remaining blocks, and post one consolidated error summary at the end *(Rule 5)*

---

### ✅ Phase 3 — Observability
**Milestone:** I can see exactly what the agent did, what it skipped, and why — without reading logs manually

> Moved up from original Phase 4. Builds on Phase 2 error handling infrastructure.
> Provides run visibility needed to verify Phase 4 duplicate detection is working.

#### Epic E3 — Logging & Reporting
> As an operator, I want a summary after every run so I know what the agent did.

- [x] **US3.1** — As the agent, I write structured logs to a file after each run (`logs/run_<ISO>.json`, one file per run)
- [x] **US3.2** — As the agent, I print a run summary: X tickets created, Y clarifications asked, Z errors + per-block outcome lines to stdout
- [x] **US3.3** — As an operator, I receive a Slack summary after every run (✅/⚠️ with ticket count, clarification count, error count); fatal errors suppressed (Phase 2 already alerts)
- [x] **US3.4** — As an operator, I can view run history, per-block detail, LLM token stats, and cost in a Streamlit dashboard (`streamlit run dashboard.py`)
- [x] **US3.5** — As an operator, I can trigger the agent from the dashboard with a "Run Agent" button
- [x] **US3.4** — As an operator, I can view run history, per-block detail, LLM token stats, and cost in a Streamlit dashboard (`streamlit run dashboard.py`)
- [x] **US3.5** — As an operator, I can trigger the agent from the dashboard with a "Run Agent" button

---

### ✅ Phase 4 — Duplicate Detection
**Milestone:** Zero duplicate tickets created across any number of runs

> Extracted from original Phase 2 E1. Placed after Observability so run logs verify the duplicate gate.
> Embedding cache migrates to agent_memory.db in Phase 7.

#### Epic E4 — Duplicate Detection
> As a team member, I should never see two Jira tickets for the same Slack message.

- [x] **US4.1** — As the agent, I fetch open Jira tickets in parallel with Slack messages using asyncio.gather()
- [x] **US4.2** — As the agent, I cache ticket embeddings in memory/ticket_embeddings.json and refresh at run start (add new, re-embed changed, remove closed)
- [x] **US4.3** — As the agent, I compute cosine similarity using text-embedding-3-small; if similarity ≥ DUPLICATE_SIMILARITY_THRESHOLD (default 0.85) I flag as duplicate
- [x] **US4.4** — As the agent, when a duplicate is detected I post the existing ticket link and ask the human to confirm instead of creating a new ticket *(Rule 4)*
- [x] **US4.5** — As the agent, I write newly created tickets to the embedding cache immediately after creation

---

### ✅ Phase 5 — Eval & Feedback Loop (Core Scope)
**Milestone:** The operator can see agent quality metrics; the agent fires a Slack alert when thumbs-up rate drops below threshold

> Core scope (US7.1–7.3) complete. Auto-tune (US7.4) and F1/precision/recall (US7.5) deferred to Phase 5b.
> Moved up from original Phase 6. No hard dependency on state tracking — run logs from Phase 3 already provide the run_id + ticket_key linkage needed for reactions. Phase 6 (Reliability) is a prerequisite for Phase 7 Memory (shared agent_memory.db) but not for Eval.

#### Epic E7 — Quality Feedback
> As an operator, I should never have to manually audit Jira to know if the agent is doing a good job.

- [x] **US7.1** — As the agent, I capture 👍/👎 reactions on my Slack confirmation messages and store them with the associated ticket key and run timestamp
- [x] **US7.2** — As the agent, I compute quality metrics after each run: thumbs-up rate per run, rolling rate across all runs
- [x] **US7.3** — As the agent, when thumbs-up rate drops below threshold I post a quality alert in Slack (warm-up gate: minimum 5 reactions before any alert fires)
- [ ] **US7.4** — As the agent, I auto-tune CONFIDENCE_AUTO_ACT and CONFIDENCE_ASK_HUMAN based on rolling feedback trends *(deferred to Phase 5b)*
- [ ] **US7.5** — As the agent, I evolve eval metrics to standard measures: precision, recall, F1 by ticket type *(deferred to Phase 5b)*

---

### Phase 5b — Eval Layer v2: LLM-as-Judge + Labeled Dataset + Regression Testing
**Milestone:** Every prompt change can be proved to improve agent accuracy; automated scoring surfaces regressions before humans see them

> Prerequisite: Phase 5 ✅ — reactions are the raw material; labels and judge scores build on top.

#### How it fits into the existing run flow

```
Current flow (run_triage.py):
  1. memory_runner.pre_run()
  2. run_eval_step(None)        ← pre-triage: collect reactions → quality metrics → alert
  3. triage_run(oldest)         ← main triage: messages → LLM loop → tickets
  4. run_eval_step(run_log)     ← post-triage: register confirmation posts
  5. memory_runner.post_run()

Phase 5b additions (bold = new):
  1. memory_runner.pre_run()
  2. run_eval_step(None)        ← + collect labels from pending 👎 DM replies
  3. triage_run(oldest)         ← unchanged
  4. run_eval_step(run_log)     ← + run LLM judge on each ticket created this run (parallel)
  5. run_regression_step()      ← NEW: score against ground-truth dataset if ≥ 30 labels exist
  6. memory_runner.post_run()
```

The judge runs in step 4, parallel across all tickets created in the run:
```python
judge_scores = await asyncio.gather(*[
    run_judge(block)
    for block in run_log.blocks if block.action == "ticket_created"
])
```
This adds one LLM call per ticket per run, parallel. No change to triage latency — judge runs after the run completes.

#### Epic E7b — Label Collection
> Convert binary 👎 signals into labeled ground-truth examples automatically.

- [ ] **US7b.1** — As the agent, when I detect a 👎 reaction I send the reporter a Slack DM: *"Thanks for the feedback on [TICKET]. What should it have been? Reply: Bug / Story / Task / Priority: High|Medium|Low"*
- [ ] **US7b.2** — As the agent, I parse DM replies and store them as labeled examples in `memory/label_store.json` with the original Slack text, agent output, and correct label
- [ ] **US7b.3** — As an operator, I can also manually label examples via a CLI: `python label.py --ticket SCRUM-12 --type Bug --priority High`

#### Epic E7c — LLM-as-Judge
> Automated per-ticket, per-dimension scoring using a different model family as judge.

- [ ] **US7c.1** — As the agent, after each run I score every created ticket using Claude (judge) against four dimensions: type accuracy, priority accuracy, title clarity, description completeness — returning JSON `{"type": 1-5, "priority": 1-5, "title": 1-5, "description": 1-5, "reason": "..."}`
- [ ] **US7c.2** — As the agent, I store judge scores in `memory/judge_store.json` keyed by `run_id + block_index`
- [ ] **US7c.3** — As the agent, I alert in Slack when the rolling average judge score drops > 0.5 points from the previous week's baseline (delta signal, not absolute threshold)
- [ ] **US7c.4** — As a developer, I calibrate the judge against the labeled dataset: judge agreement rate must be ≥ 70% with human labels before the judge alert is trusted

#### Epic E7d — Regression Testing
> An offline test suite that catches prompt regressions before they ship.

- [ ] **US7d.1** — As the agent, I compute precision, recall, and F1 per ticket type against `memory/label_store.json` (requires ≥ 30 labeled examples)
- [ ] **US7d.2** — As a developer, I can run `pytest tests/eval/` — a regression suite that fails if F1 drops > 5% from the stored baseline
- [ ] **US7d.3** — As a developer, prompt changes are safe: run the eval suite, see the delta, commit only if F1 holds
- [ ] **US7d.4** — As an operator, I can optionally connect OpenAI Evals or LangSmith once the labeled dataset reaches 50+ examples

#### Design decisions for this phase
- **Use Claude as judge, GPT-4o for triage.** Different model families break self-consistency bias — GPT-4o is unlikely to catch its own systematic errors; Claude is not subject to the same priors.
- **Judge scores are a delta signal, not absolute truth.** Alert when score drops from baseline, not when it crosses a fixed threshold. Absolute scores vary by domain; trends are reliable.
- **Labels are ground truth; judge scores are indicators.** The regression suite (`tests/eval/`) runs against human labels, not judge scores. Judge scores catch regressions between labeled runs; they do not replace ground truth.
- **DM label collection is opt-in.** Team member ignores the DM → no label collected, no penalty. Every label collected is a gift, not a requirement.

---

### Phase 6 — Reliability
**Milestone:** Agent runs automatically without manual trigger and never reprocesses old messages

> Moved from original Phase 5. Prerequisite for Phase 7 Memory (episodic store shares agent_memory.db).

#### Epic E5 — State Tracking
> As an operator, I want the agent to remember which messages it has already processed.

- [ ] **US5.1** — As the agent, I record the timestamp of the last-processed Slack message after each run
- [ ] **US5.2** — As the agent, I only fetch messages newer than the last-processed timestamp on subsequent runs

#### Epic E6 — Scheduled Execution
> As an operator, I want the agent to run on a schedule without me manually triggering it.

- [ ] **US6.1** — As an operator, I can configure a polling interval (e.g. every 5 minutes)
- [ ] **US6.2** — As the agent, I run in a continuous loop with the configured interval
- [ ] **US6.3** — As an operator, I can run the agent as a background process

---

### ✅ Phase 7 — Memory
**Milestone:** The agent accumulates knowledge across runs — episodic, semantic, and working memory all explicit and testable

> Prerequisite: Phase 5 (state tracking) — episodic store and watermark share agent_memory.db
> Phase 6 (Eval) — semantic patterns validated against thumbs-up feedback

#### Epic E8 — Memory Layers
> As the agent, I should get smarter with every run — not start from zero each time.

- [x] **US8.1** — As the agent, I persist episodic memory: {block_snippet, ticket_summary, embedding} for every `ticket_created` decision
- [x] **US8.2** — As the agent, I retrieve the top-K most similar past episodes per block and inject them into the user message
- [x] **US8.3** — As the agent, I extract and store semantic patterns from accumulated episodes (count-based + LLM-summarised)
- [x] **US8.4** — As the agent, I inject relevant semantic facts into my system prompt at run time (minimum `SEMANTIC_EXTRACTION_THRESHOLD` episodes before extraction)
- [x] **US8.5** — As a developer, the LLM message list is formally documented and tested as the working memory layer (semantic injection → SYSTEM_PROMPT; episode context → user message)
- [ ] **US8.6** — As an operator, I can inspect what the agent has learned: python run_triage.py --show-memory *(deferred to backlog)*

## Backlog (No Phase Yet)

- Auto-assign tickets to the right person based on keywords/component labels
- Link Slack thread URL to Jira ticket description
- Sprint assignment for Stories
- Support multiple Slack channels

---

## ✅ Phase 8 — Model-Agnostic LLM Provider
**Milestone:** The LLM backend (OpenAI ↔ Anthropic) can be swapped via a single config change with no business-logic changes
**Status: ✅ COMPLETE — 2026-04-29**

### What shipped
- `agents/llm/base.py` — `LLMProvider` Protocol, `LLMTurn`, `ToolCall`, `LLMProviderError`
- `agents/llm/openai_provider.py` — `OpenAIProvider` wraps OpenAI SDK, normalises to `LLMTurn`
- `agents/llm/factory.py` — `get_llm_provider(settings)` reads `LLM_PROVIDER` env var
- `agents/llm/__init__.py` — public API re-exports
- `config/settings.py` — `LLM_PROVIDER` setting (default `"openai"`)
- `agents/triage/triage_agent.py` — `_client` removed; `_provider: LLMProvider` injected; `_run_llm_loop()` calls `_provider.chat()`; `openai.APIError` → `LLMProviderError`
- 17 new unit tests; 19 existing tests migrated to `_provider` mock pattern
- **228 unit tests passing, 11/11 integration tests passing**

### How to add Anthropic
1. Create `agents/llm/anthropic_provider.py` implementing the `LLMProvider` protocol
2. Add one `elif` branch in `agents/llm/factory.py`
3. Set `LLM_PROVIDER=anthropic` in `config/.env`
4. Note: embeddings stay on OpenAI (`EMBEDDING_PROVIDER` = `"openai"` always — Anthropic has no embeddings API)

### Deferred (tracked in docs/BUGS.md)
- `DEBT-016` — tool schemas still passed as OpenAI `{"type":"function",...}` dicts; `AnthropicProvider` will need internal conversion. Consider neutral `ToolSchema` dataclass when a second provider is added.
- `pipeline/semantic_store.py` and `pipeline/duplicate_detector.py` still use OpenAI SDK directly — not in scope for Phase 8 (triage_agent.py was the only coupling point targeted)

---

## ✅ Phase 9 — Anthropic Claude Provider

**Milestone:** Run the entire triage pipeline on Claude by default; swap to OpenAI via `LLM_PROVIDER=openai`.

**Status: ✅ COMPLETE**

### What shipped
- `agents/llm/anthropic_provider.py` — `AnthropicProvider` implements `LLMProvider`; converts OpenAI-format tool schemas and multi-turn tool messages to Anthropic format
- `agents/llm/factory.py` — dispatches `anthropic` | `openai` for triage and judge
- `config/settings.py` — defaults: `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-sonnet-4-5-20250929`; judge defaults to OpenAI for cross-family scoring
- Embeddings remain on OpenAI regardless of triage provider

---

## Phase 10 — Event-Driven Trigger (Slack Socket Mode)

**Milestone:** The agent reacts to Slack messages in real time — no polling, no scheduled runs, sub-second latency from post to ticket.

> Prerequisite: Phase 6 (scheduling + watermark) ✅ complete.

### Why
The `--schedule N` loop polls Slack on a fixed interval. Between runs, messages sit unprocessed. Socket Mode flips the model: Slack pushes events to a persistent WebSocket connection your process holds open — latency drops from minutes to under a second and no API calls are wasted on quiet periods.

### What this phase covers
- `run_socket.py` — new entry point; replaces `run_triage.py` for real-time mode
  - `slack_bolt` App with Socket Mode transport (`AsyncSocketModeHandler`)
  - `@app.event("message")` handler filters bot messages and system events
  - Short debounce window (e.g. 3 s) to group burst posts into one context block before handing off to the triage pipeline
- `config/settings.py` — `SLACK_APP_TOKEN` (`xapp-...`) for Socket Mode handshake; `SOCKET_DEBOUNCE_SECONDS`
- Watermark not required — events arrive in order, real-time; no catch-up needed
- `run_triage.py` retained as-is for scheduled / one-shot mode

### What stays the same
- `agents/triage/triage_agent.py` — unchanged; accepts a list of messages as before
- All tool files, memory, observability, duplicate detection — untouched

### Effort estimate
- 1 new file (`run_socket.py`), 1 modified (`settings.py`), ~8–12 new unit tests
- Slack App config: enable Socket Mode + generate App-level token in Slack dashboard
