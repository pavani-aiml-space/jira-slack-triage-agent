# Feature: Agent Memory

> Status: Draft — pending approval
> Date: 2026-04-29
> Phase: 6

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| Agent | Makes triage decisions | Access to past decisions (episodic), learned patterns (semantic), and current conversation (working) to make better decisions | Primary |
| Operator | Runs the agent, wants quality to improve | Confidence the agent learns from past runs and doesn't repeat mistakes | Secondary |
| Developer | Implements and demonstrates each memory type | Production-quality, clearly structured code for each type — explainable in an interview | Secondary |

---

## Priority Rule

When agent correctness needs conflict with developer clarity needs: **agent correctness wins** — implementation must work correctly in production first. Clarity is achieved through structure and comments, not by simplifying the logic.

---

## Customer Problem

Every run is stateless. The agent has no awareness of what it decided last time, no sense of learned patterns, and no way to avoid repeating past mistakes. A bug reported again next week looks brand new. A pattern like "login component bugs are always Critical" is rediscovered from scratch every time. There is no accumulation of knowledge.

---

## What We're Building

Production-grade implementation of all four memory types relevant to an agentic system:

1. **Working memory** — the live LLM message list within a single run. Already exists (`messages` list in `_run_llm_loop()`). Phase 6 formalizes, documents, and tests it as an explicit named layer.
2. **Episodic memory** — the agent's record of specific past events: which Slack messages it saw, what ticket it created, when. Used to prevent duplicate tickets across runs.
3. **Semantic memory** — generalizations extracted from many episodes: "login bugs trend Critical", "deploy messages are always Tasks". Injected into the system prompt at runtime to improve classification.
4. **Procedural memory** — how the agent does things (classification rules, when to ask for clarification). Already lives in the system prompt. Not a new module — but explicitly named so its role is understood.

---

## Memory Type Definitions

| Type | What it stores | JiraSlack example | Scope | Implemented as |
|---|---|---|---|---|
| **Working** | Current LLM conversation for one block | `[{role:system,...}, {role:user,...}, {role:tool,...}]` | RAM, one block, cleared after | `messages` list in `_run_llm_loop()` — already exists |
| **Episodic** | Specific past agent decisions with time + context | "On Apr 29, saw message abc123, created SCRUM-7 as Bug/High, confidence 0.92" | Persists across runs in DB | `memory/episodic_store.py` — new module |
| **Semantic** | General patterns extracted from many episodes | "Login messages → Bug, High priority, 87% of past cases" | Persists, grows over time in DB | `memory/semantic_store.py` — new module |
| **Procedural** | How the agent classifies, when to ask for clarification | System prompt rules: "If confidence < 0.65, call ask_for_clarification" | Static, evolves via human prompt edits | System prompt in `triage_agent.py` — already exists |

> **Key distinction between episodic and semantic:**
> Episodic = "I remember *that specific conversation* on Tuesday." (raw event)
> Semantic = "Login bugs are *generally* High priority." (generalization from many events)
> Semantic is built *from* episodic — you need enough specific memories before you can reliably generalize.

---

## Storage

**One file: `memory/agent_memory.db`** — SQLite database, two tables.

SQLite because: file-based (no external service or server), queryable (unlike a flat JSON file), production-familiar, zero dependencies.

```sql
-- episodic: one row per ticket decision the agent made
CREATE TABLE episodic (
    id           INTEGER PRIMARY KEY,
    slack_ts     TEXT,    -- original Slack message timestamp
    text_hash    TEXT,    -- hash of normalized message text (for duplicate matching)
    ticket_key   TEXT,    -- e.g. "SCRUM-7"
    ticket_type  TEXT,    -- Bug / Story / Task
    priority     TEXT,    -- Critical / High / Medium / Low
    confidence   REAL,    -- GPT-4o confidence at time of decision
    run_ts       TEXT     -- ISO timestamp of the run that made this decision
);

-- semantic: one row per learned pattern (minimum 5 supporting episodes)
CREATE TABLE semantic (
    id               INTEGER PRIMARY KEY,
    condition        TEXT,    -- keyword trigger e.g. "login", "deploy", "auth"
    ticket_type      TEXT,    -- most common type for this condition
    typical_priority TEXT,    -- most common priority
    episode_count    INTEGER, -- how many episodes support this pattern
    confidence       REAL     -- episode_count / total matching episodes
);
```

Working memory is **RAM only** — the `messages` list in `_run_llm_loop()`. Intentionally never written to disk. You don't want GPT-4o's tool-calling conversation from last week influencing today's run.

---

## Runtime Flow — When Each Memory Is Read and Written

```
python run_triage.py
│
├── 1. fetch_messages()                  [no memory involved]
│
├── 2. build_context_blocks()            [no memory involved]
│
├── 3. for each block:
│   │
│   ├── SEMANTIC READ ◄──────────────── semantic_store.get_relevant_patterns(block_text)
│   │   └── inject top patterns into system_prompt before calling GPT-4o
│   │       "Learned from past: login bugs → typically Bug/High (87% of cases)"
│   │
│   ├── WORKING MEMORY INIT
│   │   └── messages = [system_prompt (+ injected patterns), user: block_text]
│   │
│   ├── _run_llm_loop(block_text, enriched_system_prompt)
│   │   └── GPT-4o loops, appending tool calls + results to messages list
│   │       [this IS working memory — the live conversation in RAM]
│   │
│   ├── EPISODIC READ ◄──────────────── episodic_store.find_duplicate(text_hash)
│   │   ├── match found → post existing ticket link to Slack, skip creation
│   │   └── no match → proceed to create ticket
│   │
│   ├── create_jira_ticket(...)          [Jira ticket created]
│   │
│   └── EPISODIC WRITE ─────────────►  episodic_store.save(
│                                           slack_ts, text_hash, ticket_key,
│                                           ticket_type, priority, confidence)
│
└── 4. after all blocks processed:
    └── SEMANTIC WRITE ─────────────►  semantic_store.extract_patterns()
        └── scans new episodic entries
            groups by keyword condition
            updates pattern counts + confidence
            only writes patterns with episode_count >= 5
```

---

## How Semantic Injection Works

Today the system prompt is static text. With semantic memory, it becomes dynamically enriched at runtime:

```python
base_prompt = "You are a Jira triage agent..."

patterns = semantic_store.get_relevant_patterns(block_text)
# e.g. block_text contains "login" →
# returns: [{"condition": "login", "ticket_type": "Bug",
#            "typical_priority": "High", "confidence": 0.87}]

if patterns:
    learned = "\n".join(
        f"- '{p['condition']}' messages: typically {p['ticket_type']}, "
        f"{p['typical_priority']} priority ({p['confidence']:.0%} of past cases)"
        for p in patterns
    )
    system_prompt = base_prompt + f"\n\nLearned from past decisions:\n{learned}"
else:
    system_prompt = base_prompt
```

GPT-4o receives the base rules *plus* evidence from actual team history. The procedure (how to classify) is in `base_prompt`. The learned context (what this team's patterns are) is injected on top.

---

## What GPT-4o Actually Receives Per Call

**Today (Phase 1 — static):**
```python
chat.completions.create(
    model="gpt-4o",
    tools=[CREATE_JIRA_TICKET_SCHEMA, POST_SLACK_MESSAGE_SCHEMA, ASK_FOR_CLARIFICATION_SCHEMA],
    messages=[
        {"role": "system", "content": system_prompt},   # procedural memory — static
        {"role": "user",   "content": block_text},      # current Slack messages
    ]
)
```

**Phase 6 (with memory):**
```python
chat.completions.create(
    model="gpt-4o",
    tools=[CREATE_JIRA_TICKET_SCHEMA, POST_SLACK_MESSAGE_SCHEMA, ASK_FOR_CLARIFICATION_SCHEMA],
    messages=[
        {"role": "system",    "content": system_prompt              # procedural memory
                                       + "\n\nLearned from past:"   # semantic memory injected
                                       + learned_patterns},
        {"role": "user",      "content": block_text},               # current Slack messages
        # ↓ working memory grows below during the tool-calling loop ↓
        {"role": "assistant", "tool_calls": [...]},
        {"role": "tool",      "content": "Created SCRUM-7..."},
        {"role": "assistant", "content": "Done."},
    ]
)
```

| What GPT-4o receives | Memory type | Changes per run? |
|---|---|---|
| `tools=[...]` | — | Never — fixed tool schemas |
| `system_prompt` base rules | Procedural | No — static, edited by humans |
| `+ learned_patterns` appended to system | Semantic | Yes — grows as agent learns |
| `messages[1]` user content | — | Yes — current Slack block |
| `messages[2..n]` tool call/result pairs | Working | Yes — built live during loop, RAM only |

**Episodic memory never enters the prompt.** It is a hard gate *before* GPT-4o is called — if the episodic store finds a duplicate, the LLM loop is skipped entirely for that block. You don't ask the LLM whether something is a duplicate when you already know from your own records.

---

## Out of Scope

- Vector database or embedding-based semantic retrieval (keyword matching first; embeddings are Phase 6b)
- Memory decay or TTL (memories do not expire in Phase 6; TTL added in Phase 6b)
- Cloud-hosted memory store (single SQLite file only)
- Memory sharing across multiple agent instances
- Procedural memory as a separate module (system prompt is the correct implementation)

---

## Must-Haves vs Nice-to-Haves

| Category | Item |
|---|---|
| Must-have | `memory/episodic_store.py` — write on every ticket decision, read before every creation |
| Must-have | `memory/semantic_store.py` — extract patterns post-run, inject into system prompt pre-run |
| Must-have | Episodic duplicate check: exact text_hash match blocks ticket creation |
| Must-have | Semantic minimum gate: only inject patterns with episode_count ≥ 5 |
| Must-have | Working memory: document + unit test the `messages` list lifecycle in `_run_llm_loop()` |
| Must-have | Procedural memory: explicitly named in system prompt docstring so its role is understood |
| Nice-to-have | `python run_triage.py --show-memory` — print what the agent has learned |
| Nice-to-have | Memory invalidation: operator can clear or correct a wrong semantic pattern |
| Nice-to-have | Embedding-based semantic retrieval (Phase 6b upgrade path) |

---

## Success Metrics

| Metric | Target | How Measured | Whose need |
|---|---|---|---|
| Episodic recall accuracy | 100% of previously seen messages matched on repeat | Test with known past hashes | Agent |
| Duplicate prevention | 0 duplicate tickets when episodic memory is active | Count tickets across repeat runs | Operator |
| Semantic pattern extraction | ≥ 3 meaningful patterns per 20 messages | Count entries in semantic store | Agent |
| Semantic injection latency | < 500ms per run | Timing | Operator |
| Working memory test coverage | Full lifecycle tested (init, append, tool result, clear) | Unit tests | Developer |
| Code clarity | Each memory type in a separate, independently readable module | Interview walkthrough | Developer |

---

## Risks & Open Questions

- **Risk:** Episodic store grows unbounded — no TTL in Phase 6. Mitigation: warn when store exceeds 1000 entries; TTL added in Phase 6b.
- **Risk:** Semantic patterns capture wrong generalizations from a bad data week. Resolved: minimum 5-episode gate before any pattern is injected.
- **Risk:** Too many semantic patterns injected → system prompt exceeds context window. Open question: cap at top 5 by confidence? Top 5 by keyword relevance to current block?
- **Risk:** Exact text hash is brittle — same bug described differently won't match. Phase 6 starts with exact hash; Phase 6b upgrades to embedding similarity.
- **Dependency:** Phase 3 (state tracking watermark) — episodic store and watermark both persist run state; share the same `agent_memory.db` to avoid two separate state files.
- **Dependency:** Phase 5 (Eval) — semantic patterns validated against thumbs-up feedback. Phase 5 + Phase 6 together form the self-improving loop.

---

## New Priority Rules (feature-specific only)

- **Episodic memory overrides LLM:** If episodic store says this message was already processed, the agent posts the existing ticket link and skips creation — regardless of what GPT-4o wants to do. Memory is a hard check, not a hint.
- **Sparse patterns not injected:** Semantic patterns with fewer than 5 supporting episodes are stored but not injected into the system prompt. Sparse patterns are noise, not knowledge.

---

## Decisions Made This Session

- Four memory types, not three: working + episodic + semantic + procedural — all four named explicitly even though working and procedural already exist
- **Storage: single SQLite file** (`memory/agent_memory.db`) — not JSON files, not cloud DB. File-based, queryable, no external service
- Working memory = RAM only (`messages` list) — never persisted. Intentional: stale LLM conversations should not influence future runs
- Procedural memory = system prompt — not a separate module. Named explicitly so its role is understood, but no new code
- Semantic injection enriches the system prompt dynamically at runtime — base prompt (procedural) + learned patterns (semantic) combined before each GPT-4o call
- Semantic patterns built from episodic rows — episodic is the raw experience, semantic is the generalization. Semantic extraction runs after all blocks are processed each run
- **Episodic check is a pre-LLM gate** — if a duplicate is found, GPT-4o is never called for that block. Rationale: you don't ask the LLM whether something is a duplicate when you already have the definitive answer from your own records. Memory is a hard check, not input to the LLM's reasoning.
- **What GPT-4o receives = tools + (procedural + semantic in system message) + (current block as user message) + (working memory growing as tool calls happen).** Episodic never enters the prompt — it gates before the prompt is constructed.
- Phase 6 starts with exact hash matching for episodic duplicates; embedding similarity is Phase 6b
- Phase 3 is a hard prerequisite — episodic store and watermark share `agent_memory.db`
