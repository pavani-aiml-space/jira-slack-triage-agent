# Building JiraSlack — A Learning Journey

> A reflection on building a production-grade AI agent from scratch,
> phase by phase, using a structured SDLC with AI pair programming.

---
Sharing the AI agentic application I built to understand what it actually takes to go from a working prototype to something production-ready.

I picked a deliberately simple use case — automating the Slack → Jira triage workflow — and evolved it phase by phase: starting with a basic LLM tool-calling loop, then layering in duplicate detection, observability, a quality feedback loop, episodic and semantic memory, and finally a model-agnostic provider abstraction. 

## The Use Case

When someone reports a bug in Slack, and someone else has to read it, decide if it's real, classify it, write up a Jira ticket, and post back a link. It takes 5–10 minutes per message. It accumulates. It gets missed.

This workflow automates this entirely. It monitors a Slack channel, reads every message, decides whether it's a bug / story / task / unclear / duplicate — and takes action without being asked.

There's a human-in-the-loop concept where bug asks for more info in slack, if there's not enough information/

---

## The Architecture at a Glance

```
Slack channel
    │
    ▼
[Slack MCP]  ──── fetch last N messages ────────────────────────────────────►
                                                                              │
    [Context Builder]  ──── group into 5-min conversation blocks ────────────►
                                                                              │
    [Duplicate Gate]  ──── embed + cosine similarity vs open Jira tickets ───►
                             (skip if similarity ≥ 0.85)                      │
                                                                              │
    [Triage Agent]  ──── GPT-4o tool-calling loop ─────────────────────────►
         │   ├── create_jira_ticket  →  Jira REST API
         │   ├── post_slack_message  →  Slack MCP
         │   ├── ask_for_clarification  →  Slack MCP
         │   └── search_memory  →  Episode Store (vector search)
         │
    [Memory Layer]
         ├── Working memory: LLM message list (in-flight context)
         ├── Episodic memory: past decisions + embeddings (persisted JSON)
         └── Semantic memory: extracted patterns injected into system prompt
         │
    [Eval Layer]
         ├── 👍/👎 reaction collector  →  Quality Store
         └── Quality metrics  →  Slack alert if thumbs-up rate < 70%
         │
    [Run Logger]  ──── structured JSON per run  ──── Streamlit dashboard
```

---

## The Evolution — Eight Phases

Each phase followed a full SDLC cycle: `/brainstorm → /design → /diagram → /plan → /build → /audit → /kaizen → /closeout`.

---

### Phase 1 — Core Pipeline
**Milestone:** First Jira ticket automatically created from a Slack message

**What I was solving:** Could I get an AI agent to read a Slack message and create a well-formed Jira ticket — end to end — without hardcoding any classification logic?

**What shipped:**

| Component | What it does |
|-----------|-------------|
| `pipeline/slack_reader.py` | Fetches the last N messages via Slack MCP subprocess |
| `pipeline/context_builder.py` | Groups messages into 5-minute conversation windows |
| `agents/triage/triage_agent.py` | GPT-4o tool-calling loop — the brain |
| `agents/triage/tools/jira_tools.py` | `create_jira_ticket` — Jira REST API v3 (Basic Auth) |
| `agents/triage/tools/slack_tools.py` | `post_slack_message`, `ask_for_clarification` |
| `run_triage.py` | Entry point: reads Slack → groups → loops |
| `config/settings.py` | All config from `.env` — no hardcoded secrets |

**How the LLM loop works:**
```
messages = [system_prompt, user_block]
while iterations < MAX:
    response = gpt-4o(messages, tools)
    if finish_reason == "stop": break
    if finish_reason == "tool_calls":
        for each tool_call:
            result = execute_tool(tool_call.name, tool_call.args)
            messages.append(tool_result)
```

GPT-4o receives the Slack conversation and chooses between four actions: create a ticket, ask for clarification, post a message, or do nothing. It writes the Jira title, description, type, and priority from natural language.

**First ticket created:** SCRUM-3 — *"Fix login crash on empty password"* — ~15 seconds from Slack message to Jira.

**Key decision:** Tool-calling loop over a classifier. A standalone `classify()` function would need to be updated whenever the decision space changed. The tool-calling approach lets GPT-4o reason about edge cases (duplicate signals, vague messages) without any rule updates.

**What I learned:** GPT-4o is surprisingly good at structured extraction from messy Slack messages. The hard part isn't the happy path. It's every other path.

---

### Phase 2 — Failure Transparency
**Milestone:** The agent never fails silently — every external service failure results in a Slack notification or stdout log

**What I was solving:** The agent runs unsupervised. If it fails, who finds out? When?

**What shipped — the 7 Priority Rules:**

| Rule | Scenario | Agent behaviour |
|------|----------|----------------|
| Rule 1 | Jira unavailable | Post to Slack: "Could not create ticket — please create manually" |
| Rule 2 | Message is vague | Create with available info, then post INVEST prompt asking for details |
| Rule 3 | Confidence is 65–90% | Create with best-guess type + flag: "I'm not fully confident — please review" |
| Rule 4 | Duplicate detected | Post existing ticket link + ask human to confirm it's the same issue |
| Rule 5 | Slack MCP fails mid-run | Continue all remaining blocks; post consolidated error summary at end |
| Rule 6 | OpenAI API unavailable | Alert Slack with specific error + "please triage manually"; exit cleanly |
| Rule 7 | Two people report the same bug | First ticket stands; second reporter gets a duplicate notice |

**Technical implementation:**
- `except openai.APIError` → Slack alert + `sys.exit(1)` (fatal, Rule 6)
- `except Exception` per-block → accumulate errors, continue loop, post summary at end (Rule 5)
- All Slack posts retry-safe: if the alert post itself fails → write to stdout (belt and suspenders)

**Key decision:** Rules before code. Writing the seven rules as explicit policy decisions before touching the error handling made every `try/except` block a mechanical translation of an already-agreed decision. There was no "what should we do here?" during coding.

**What I learned:** Exception handling in AI agents is a policy design problem, not a coding problem. The code is easy. Deciding *what the agent should believe* when things go wrong is the real work.

---

### Phase 3 — Observability
**Milestone:** I can see exactly what the agent did, what it skipped, and why — without reading logs manually

**What I was solving:** The agent was doing things but I had no visibility into what. Was it creating duplicates? Was it asking for clarification too often? How many tokens per run?

**What shipped:**

| Component | What it does |
|-----------|-------------|
| `pipeline/run_logger.py` | `RunLog`, `BlockResult`, `LlmStats` dataclasses + JSON writer |
| `logs/run_<ISO>.json` | One structured file per run: blocks processed, tickets created, token counts, errors, per-block outcomes |
| Stdout summary | Printed after every run: block count, ticket count, clarification count, error count, status |
| Slack ✅/⚠️ summary | Posted to channel after every run (suppressed on fatal error — Phase 2 already alerted) |
| `dashboard.py` | Streamlit app: run history, per-block detail, LLM cost graph, "Run Agent" button |

**Example log entry (one block):**
```json
{
  "block_index": 0,
  "block_snippet": "login page crashes on empty password",
  "action": "ticket_created",
  "ticket_key": "SCRUM-5",
  "ticket_type": "Bug",
  "ticket_priority": "High",
  "llm": {
    "iterations": 2,
    "finish_reason": "stop",
    "prompt_tokens": 842,
    "completion_tokens": 156
  }
}
```

**Key decision:** Structured JSON, not plain text logs. The dashboard reads the same JSON the agent writes. No parsing, no grep. Every field queryable.

**What I learned:** You can't improve what you can't measure. Adding observability immediately surfaced patterns I didn't expect — like the agent asking for clarification on messages that seemed clear to me, which told me the system prompt needed refinement.

---

### Phase 4 — Duplicate Detection
**Milestone:** Zero duplicate tickets created across any number of runs

**What I was solving:** The same login crash was reported by three different team members in one hour. Without deduplication, the agent would create three SCRUM tickets.

**What shipped:**

| Component | What it does |
|-----------|-------------|
| `pipeline/duplicate_detector.py` | Embeds incoming message + all open Jira tickets; cosine similarity gate |
| `memory/ticket_embeddings.json` | Cache of open-ticket embeddings — refreshed at run start |
| `asyncio.gather()` in `run()` | Fetches Slack messages and open Jira tickets in parallel (halved latency) |

**How it works:**
```
At run start:
  → fetch all open Jira tickets (paginated, up to 1,000)
  → load embedding cache
  → re-embed tickets that changed, add new ones, prune closed ones

For each block:
  → embed the incoming Slack text
  → cosine_similarity(block_embedding, each_ticket_embedding)
  → if max_similarity ≥ 0.85:
       post "this looks like SCRUM-X — is it the same issue?"
       skip LLM loop entirely
  → else: proceed to triage
```

**Threshold calibration:** 0.85 was chosen after testing — high enough that semantically different issues (login crash vs. login page UI glitch) don't collide; low enough that the same issue described differently by two people triggers the gate.

**Key decision:** Embedding gate before LLM loop. Checking for duplicates with the LLM would cost tokens and latency for every block. The embedding gate is a cheap pre-filter — it only costs an embedding call, which is ~50× cheaper than a GPT-4o call.

**What I learned:** Caching is non-trivial even for a simple embedding store. The first version of the cache grew indefinitely (closed tickets never pruned) and mutated the caller's dict in-place. Both bugs were caught in code review, not at runtime. The test suite is what made them safe to fix.

---

### Phase 5 — Eval & Feedback Loop
**Milestone:** The operator can see agent quality metrics; the agent fires a Slack alert when accuracy degrades

**What I was solving:** How do I know if the agent is making good decisions? I can read individual tickets, but I needed a signal that scaled.

**What shipped:**

| Component | What it does |
|-----------|-------------|
| `pipeline/reaction_collector.py` | Polls Slack for 👍/👎 reactions on agent confirmation messages |
| `pipeline/quality_metrics.py` | Stores reactions in `memory/quality_store.json`; computes thumbs-up rate (rolling + per-run) |
| `pipeline/eval_runner.py` | Orchestrates pre/post eval hooks around each triage run |
| Quality alert | When thumbs-up rate < 70% AND total reactions ≥ 5 → Slack alert: "Agent accuracy may be degrading" |

**The reaction flow:**
```
Run N:
  → agent creates SCRUM-12 and posts "✅ Created SCRUM-12: Login crash | High | Bug"
  → team member reacts with 👍

Run N+1 (next time run_triage.py runs):
  → reaction_collector polls for reactions on that message
  → 👍 stored: {ticket_key: SCRUM-12, reaction: +1, ts: ...}
  → quality_metrics computes rolling thumbs-up rate
  → if rate < 0.70 and total_reactions ≥ 5: post quality alert
```

**Warm-up gate:** The 5-reaction minimum prevents false alerts on the first few runs (when one 👎 would drop the rate to 0%).

**Key decision:** Use Slack reactions, not a separate review UI. The team already uses Slack. Adding a 👍 to a message is frictionless. A separate review tool would require a behaviour change; reactions don't.

**What I learned:** The feedback loop changed how I thought about the entire system. Once I could see the thumbs-up rate, I started thinking about *what the agent is optimising for* — not just whether it works, but whether it's making decisions the team agrees with.

---

### Phase 6 — Run Logger v2 + Action Tracking
**Milestone:** The run log captures exactly which action was taken per block and whether it was confirmed

**What I was solving:** The Phase 3 logs captured token counts but not outcomes. I couldn't answer: "How many of last week's tickets got a 👍?"

**What shipped:**
- `BlockResult` dataclass extended with `action`, `ticket_key`, `ticket_type`, `ticket_priority`, `confirmation_ts`
- `LlmStats` captures per-block iteration count, final finish reason, token totals
- `drain_confirmation_ts()` polls the confirmation message ts after each ticket creation — links the run log to the reaction log so Phase 5 eval can match them
- `RunLog` extended with `duplicates_flagged_count`, `clarifications_asked_count`, `blocks_skipped_count`
- Dashboard updated to show per-block action outcomes

**Key decision:** Log the `confirmation_ts` at creation time. The reaction collector needs the message timestamp to find reactions. Storing it in the run log at ticket creation is the only reliable way to link them — no separate lookup needed.

---

### Phase 7 — Memory
**Milestone:** The agent accumulates knowledge across runs — episodic, semantic, and working memory all explicit and testable

**What I was solving:** The agent started fresh every run. It had no memory of that Bug:High ticket it created last Tuesday that the whole team 👍'd. Could it learn?

**Three memory layers, each serving a different purpose:**

**Working Memory** — the LLM message list, formalised.

The GPT-4o loop already has implicit memory: the `messages` list grows with each tool call and result within a single run. Phase 7 made this explicit: the system prompt is the long-term slot, the user message is the episodic slot. Semantic patterns go in the system prompt (small, stable, run-level). Episode context goes in the user message (specific, dynamic, block-level).

**Episodic Memory** — what happened, stored.

```
Episode {
  run_id, block_index, block_snippet,
  ticket_key, ticket_type, ticket_priority, ticket_summary,
  embedding,         ← vector of the block text
  validation_status  ← "unvalidated" → "validated" when 👍 received
}
```

Stored in `memory/episode_store.json`. Pruned to MAX_EPISODES (200) using FIFO. Only validated episodes (team-confirmed with 👍) are returned by `search_memory()`.

**Semantic Memory** — patterns extracted from episodes.

After enough episodes accumulate, an LLM pass extracts count-based patterns:
```
## Learned Patterns (injected into system prompt)
- Bug tickets: 8× more common than Story tickets
- High priority: 62% of all tickets
- Login-related messages: always Bug:High in this codebase
```

This is injected once per run into the system prompt — not per block. It gives the agent a compact view of the team's history without token cost on every block.

**Lazy retrieval strategy:** Episodes are NOT pre-injected for every block. The agent calls `search_memory(query)` only when uncertain. This costs zero tokens for the 80% of blocks that are clear-cut.

**Key decision:** Store the SDK object in `LLMTurn.raw_message`. The OpenAI SDK returns a `ChatCompletionMessage` object that must be appended to `messages` for the next iteration. Re-serialising it to a dict would require knowing every provider's exact format. Storing the SDK object (`raw_message: Any`) lets each provider own its own message history format.

**Memory quality gates:** Episodes are stored as `unvalidated` first. They're only promoted to `validated` — and only returned by `search_memory()` — when the team reacts with 👍. A wrong decision never reinforces itself.

**What I learned:** Memory is the mechanism by which the agent improves over time, not just runs correctly. But without a quality gate, the agent would learn from its own mistakes. The validation step is what makes it safe to learn from.

---

### Phase 8 — Model-Agnostic LLM Provider
**Milestone:** The LLM backend can be swapped via a single config change with zero business-logic changes

**What I was solving:** `triage_agent.py` had 9 direct coupling points to the OpenAI SDK: the client import, `chat.completions.create()`, response parsing, `choices[0]`, `tool_call.function.arguments`, error handling. Swapping to Anthropic would require touching business logic.

**What shipped:**

```
agents/llm/
├── base.py           ← LLMProvider (Protocol), LLMTurn, ToolCall, LLMProviderError
├── openai_provider.py ← wraps OpenAI SDK; normalises response → LLMTurn
├── factory.py        ← get_llm_provider(settings) reads LLM_PROVIDER env var
└── __init__.py       ← public API re-exports
```

**The abstraction boundary:**

```
triage_agent.py (knows nothing about OpenAI)
    │
    │  turn = await _provider.chat(messages, tools, system_prompt)
    │  finish = turn.finish_reason         ← not choices[0]
    │  for tc in turn.tool_calls:
    │      args = tc.args                  ← not json.loads(function.arguments)
    │
    ▼
LLMProvider protocol
    │
    ▼
OpenAIProvider.chat()
    → asyncio.to_thread(self._client.chat.completions.create, ...)
    → normalise: choices[0] → finish_reason, tool_calls → list[ToolCall], args parsed
    → return LLMTurn(finish_reason, content, tool_calls, tokens, raw_message)
```

**To add Anthropic / swap providers:** `AnthropicProvider` ships by default. Set `LLM_PROVIDER=openai` + `LLM_MODEL=gpt-4o` to use OpenAI for triage. Embeddings stay on OpenAI either way.

**Test impact:** 19 existing tests migrated from patching `_client` to patching `_provider`. New `make_llm_turn()` and `make_tool_call_turn()` helpers make multi-turn test setup one line. 17 new tests for the provider package.

**What I learned:** Abstractions have a cost. This one was worth it because we know a provider change is coming. But the right time to build the abstraction is when the second use case exists — not speculatively. We built it when we had a concrete reason (Anthropic Phase 9 is designed and approved).

---

## What Surprised Me

### The planning phase feels heavy — until it doesn't

The process I used: `/brainstorm → /design → /diagram → /plan → /build → /audit → /kaizen → /closeout` for every phase.

The first time through it feels bureaucratic. You're producing three documents before writing a line of code.

By Phase 3, I understood why it works. The brainstorm answers *what and why*. The design answers *how*. The diagram shows *exactly where the code changes*. By the time I run `/build`, every decision is already made. Building becomes mechanical execution of a clear plan.

**The insight:** The process front-loads thinking. The cost is upfront. The savings compound — because you never have to re-read three files to remember why something was built a certain way.

---

### Production prompts that changed how I design

When designing, I'd ask: *"Design this as if it handles 1M messages per day. Now 10K. Now 1K."*

The 1M version reveals the architecture — what would need to be queued, cached, or parallelised. The 1K version reveals what's actually needed now. The gap between them is where the technical debt lives. Building for 1K with the 1M architecture in mind means you never design yourself into a corner.

---

### Building for production is not a different skill level — it's a different mindset

I started thinking: "AI makes building faster." That's true for the first 20%. The rest is the same questions that have always mattered:

- What happens when the API is down?
- What happens when the same message arrives twice?
- How do I know if the system is working?
- How does the system get better over time?
- What does "correct" even mean here, and how do I measure it?

These aren't AI questions. They're engineering questions. The AI generates the code faster. The questions don't go away.

---

### What AI pair programming actually changed

The bottleneck used to be implementation velocity — boilerplate, wiring, debugging syntax. AI handles all of that.

The new bottleneck is *decision quality* — knowing what to build, in what order, with what tradeoffs. That requires the same system design expertise as always. Possibly more, because you can move faster into bad architecture.

The SDLC process addressed this directly. Every phase gate requires a decision, not just a deliverable. You can't close out without a passing audit. The process ensures you're making decisions, not just generating code.

---

## The Numbers

| Metric | Value |
|--------|-------|
| Phases completed | 8 of 9 planned |
| Unit tests | 228 (0 at Phase 1 start) |
| Integration tests | 11 |
| Files in codebase | ~40 |
| Lines of production code | ~2,500 |
| Time from Slack to Jira | ~15 seconds |
| Duplicate ticket rate | 0% (embedding gate) |

---

## Tools

| Tool | Role |
|------|------|
| Cursor | IDE with AI pair programming |
| Claude Sonnet 4.6 | Code generation, design review, SDLC execution |
| OpenAI GPT-4o | Agent brain — triage decisions |
| OpenAI text-embedding-3-small | Duplicate detection embeddings |
| Slack MCP | Read/write Slack messages via stdio subprocess |
| Jira REST API v3 | Create and search tickets (Basic Auth) |
| pytest + pytest-asyncio | Test suite |
| Streamlit | Operations dashboard |

---

## What Made the Difference

**The SDLC.** I could go from idea → implementation before this project. What I lacked was a structured process that enforced *understanding at each step*.

The workflow files define exactly what each phase must produce before the next starts. This prevents the classic failure mode: building fast, building wrong, discovering it at the end.

**The test suite.** 228 unit tests. Every external dependency mocked. Every failure mode covered. Phase 8 touched 10 files. The suite caught every regression before it shipped.

**The LEARNINGS file.** Every gotcha, process insight, and hard decision captured after each phase. Read at the start of every new phase. Three sessions in, it started paying back — the same mistakes stopped happening twice.

**The feedback loop.** Connecting Slack reactions back to the run log changed how I thought about the system entirely. Once I could see quality metrics, I started thinking about whether the agent was making the *right* decisions — not just decisions.

---

## Where It Goes Next

- **Phase 9** — Add Anthropic Claude: one new file, one config line
- **Phase 10** — Scheduled runs + message watermarking (never reprocess a message)
- **Phase 11** — Auto-tune confidence thresholds from the quality feedback loop
- **Phase 12** — Labeled dataset + F1/precision/recall regression testing

The system gets smarter with each run, not just with each phase.

---

## The Honest Summary

Building AI agents for production is genuinely accessible now. The tools are good enough.

What separates a prototype from something you'd trust with your team's workflow is the same thing that always separated good software from bad:

**Discipline about decisions, not just speed of execution.**

The AI makes you faster. The process makes you right.

---

*Built with Cursor + Claude Sonnet 4.6 | April 2026*
*Source: JiraSlack (private)*
ry