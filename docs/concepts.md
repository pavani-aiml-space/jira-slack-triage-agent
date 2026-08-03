# What We Built and What We Learned — Phase by Phase

> A plain-English breakdown of every phase of JiraSlack: what the problem was,
> what we built to solve it, and the engineering concept it introduced.

---

## Phase 1 — The Core Pipeline
### What we built

A Python program that reads the last 20 messages from a Slack channel and, for each one, asks GPT-4o to decide what to do — create a Jira ticket, ask for clarification, or ignore it. When it creates a ticket, it posts the Jira link back to Slack.

That's it. A loop that reads, thinks, and acts.

### The concept: LLM Tool-Calling (Agentic Loop)

**What is tool-calling?**

A standard LLM conversation is one-way: you send a message, it sends text back. Tool-calling changes that. You tell the model: "You have access to these functions. When you want to call one, tell me which one and with what arguments — and I'll run it and give you the result."

The model doesn't execute code. It asks for things to be executed. You run them and report back. The model then decides what to do next.

**What the loop looks like:**

```
1. Send: [system prompt + conversation block]
2. GPT-4o responds: "I want to call create_jira_ticket with these args"
3. We call the Jira API with those args
4. We send the result back to GPT-4o
5. GPT-4o responds: "I want to call post_slack_message with these args"
6. We post to Slack
7. GPT-4o responds: "Done" (finish_reason = stop)
8. Loop ends
```

Each tool call is one iteration. The loop keeps going until the model says it's done or we hit the max iterations limit.

**Why not just use a classifier?**

A classifier returns a label — `"Bug"`, `"Story"`, `"Task"`. Then you'd need separate code to decide what to do with each label, handle edge cases, compose the Jira description, and so on.

The tool-calling loop lets GPT-4o handle all of it in one pass. It reads the message, decides the type, composes the ticket fields, calls the API, and handles the edge cases — without any additional rule code. When the decision space changes (e.g. "also ask for a component label"), you update the system prompt, not the code.

**What we defined as tools:**

| Tool | What it does |
|------|-------------|
| `create_jira_ticket` | Creates a Bug/Story/Task in Jira with title, description, priority |
| `post_slack_message` | Posts any message to the channel |
| `ask_for_clarification` | Posts a structured prompt asking the reporter for more detail |

Each tool has two parts: a JSON schema (so GPT-4o knows it exists and what arguments it takes) and a Python `async def` function that actually executes it.

**What "context blocks" are:**

Raw Slack messages arrive one at a time. If three people send three related messages in 5 minutes, they form one conversation. `context_builder.py` groups messages that are within 5 minutes of each other into a single block, then sends each block to the agent as one unit of work. This is why GPT-4o sees "Alice: the login page crashes. Bob: same for me, on Chrome too" as one coherent report rather than two separate unrelated messages.

---

## Phase 2 — Failure Transparency
### What we built

Seven explicit rules that define exactly what the agent does when something goes wrong: Jira is down, the message is vague, confidence is low, a duplicate is detected, Slack fails mid-run, or the LLM API is unavailable.

No silent failures. Every error path ends with either a Slack notification or a stdout log.

### The concept: Exception Handling as Policy

**The difference between exception handling and error policy**

Exception handling is the technical mechanism — `try/except`, error codes, fallback values. Error policy is the decision about *what the agent should believe and do* when the exception fires.

Most exception handling in traditional software is straightforward: if the database call fails, return an HTTP 500. There's one external dependency and one recovery path.

AI agents have multiple external dependencies (LLM API, Jira API, Slack API, embedding API), multiple partial failure modes (Jira down but Slack up, Slack MCP fails on block 3 of 7, embedding model returns empty vector), and they run unsupervised. When they fail silently, nobody finds out until a team member notices that no tickets appeared for two days.

**Why you write the rules before you write the code**

The seven rules were written as explicit decisions before any exception handling code was written. Each rule answers: "In this scenario, what should the agent do?"

Rule 1: Jira unavailable → post Slack notification, continue
Rule 6: LLM unavailable → post Slack notification, exit

Writing the rules first means every `try/except` block is a mechanical translation of an already-agreed decision. There's no "what should we do here?" during coding — that decision was already made.

**The two failure modes we distinguish:**

*Fatal* — the agent cannot continue this run at all. Example: OpenAI API is down. Without the LLM, there's nothing to do. Post a Slack alert and exit.

*Block-level* — this specific message failed, but other messages in the same run can still be processed. Example: Jira is temporarily rate-limited on block 3. Post a notification for block 3, continue with block 4, post a consolidated summary at the end.

**How we tell them apart — the single question:**

> "Can the remaining messages still be processed if we skip this one?"

If yes → block-level. Keep going, accumulate the error, report at the end.
If no → fatal. Stop immediately, alert, exit.

The test for each dependency:

| Dependency | If it fails | Why |
|------------|-------------|-----|
| OpenAI API | Fatal | The LLM is the decision engine for every block. Without it, there is nothing to process. |
| Jira API | Block-level | Jira failing on block 3 doesn't prevent the agent from processing blocks 4, 5, 6. The team can manually create the one failed ticket. |
| Slack MCP (posting) | Block-level | A failed confirmation post doesn't undo the Jira ticket. The ticket exists. Report the failure at the end. |
| Slack MCP (reading) | Fatal | If we can't read messages, there's no input. The run can't start at all. |

**How the code reflects this split:**

Fatal errors are caught at the top of the run, before the block loop begins:
```python
try:
    messages = await fetch_messages(...)   # if this fails → nothing to do
except Exception as e:
    await post_slack_message(f"Fatal: {e} — triage manually")
    sys.exit(1)
```

Block-level errors are caught inside the loop, per block. Failed blocks go into an accumulator:
```python
errors = []
for block in blocks:
    try:
        await _run_llm_loop(block)
    except Exception as e:
        errors.append(ErrorEntry(block_index=i, error_message=str(e)))
        continue   # ← keep going with the next block

if errors:
    await post_slack_message(f"⚠️ {len(errors)} block(s) failed: ...")
```

The `continue` is the key word. It means: "this block is done (badly), move on to the next one." A fatal error has no `continue` — it has a `sys.exit(1)`.

Distinguishing these two shapes the entire error handling architecture. The question "can we continue without this?" has to be answered once, deliberately, for every external call — not discovered at runtime.

---

## Phase 3 — Observability
### What we built

After every run, the agent writes a structured JSON file to `logs/`. It prints a summary to stdout. It posts a summary to Slack. A Streamlit dashboard reads the log files and displays run history, per-block outcomes, token counts, and cost.

### The concept: Observability

**What observability means**

Observability is the property of a system that lets you understand its internal state from its external outputs — without modifying the code.

There are three signals that make a system observable:

*Logs* — a timestamped record of what happened. "At 14:03:21, block 0 was classified as Bug:High. Ticket SCRUM-5 created. 2 iterations, 842 prompt tokens."

*Metrics* — quantitative measurements over time. "Tickets created per run. Tokens per run. Thumbs-up rate across last 10 runs."

*Traces* — the execution path through the system for a specific request. "For block 2 of run 2026-04-29T14:03, the agent called create_jira_ticket, then post_slack_message. Both succeeded."

**Why structured JSON, not plain text**

A text log like `"[14:03:21] Block 0: created SCRUM-5"` tells a human what happened. A structured JSON log tells both humans *and code* what happened.

```json
{
  "block_index": 0,
  "action": "ticket_created",
  "ticket_key": "SCRUM-5",
  "ticket_type": "Bug",
  "ticket_priority": "High",
  "llm": { "prompt_tokens": 842, "completion_tokens": 156 }
}
```

The dashboard doesn't parse strings — it reads fields directly. The eval runner doesn't grep logs — it loads dicts and accesses keys. Structured data is the only format that both humans and downstream code can use without a parsing layer.

**What the `RunLog` dataclass captures:**

```
RunLog
├── run_id          — ISO timestamp, used as filename
├── status          — "success" | "partial" | "fatal"
├── messages_fetched
├── blocks_processed
├── tickets_created_count
├── clarifications_asked_count
├── duplicates_flagged_count
├── error_count
└── blocks[]
    └── BlockResult
        ├── block_snippet   — first 60 chars of the block
        ├── action          — "ticket_created" | "clarification_asked" | "error" | "skipped"
        ├── ticket_key
        ├── ticket_type / priority
        ├── confirmation_ts — Slack timestamp of the bot's confirmation message
        └── llm
            └── LlmStats
                ├── iterations
                ├── tools_called
                ├── finish_reason
                └── prompt/completion tokens
```

**Why this matters before Phase 4**

The duplicate detection gate (Phase 4) needed to be verified. Was it firing? Was the threshold too aggressive? Without per-block logs showing `action: "skipped"` and the matching ticket key, you'd have no way to know.

Observability isn't a nice-to-have. It's the mechanism that lets you trust everything that comes after it.

---

## Phase 4 — Duplicate Detection
### What we built

Before the LLM loop runs for any message, the agent checks whether the message looks like something already in Jira. If it's more than 85% similar to an open ticket, it skips the LLM call entirely and posts the existing ticket link to Slack instead.

### The concept: Embeddings and Cosine Similarity

**What an embedding is**

An embedding is a list of numbers — typically 256 to 3072 numbers — that represents the meaning of a piece of text. Two texts that mean the same thing (even if worded differently) have embeddings that are close together in the numeric space. Two texts that mean different things have embeddings that are far apart.

```
"Login page crashes on empty password"    → [0.21, -0.43, 0.87, ...]
"App crashes when I submit empty password" → [0.19, -0.41, 0.84, ...]
"Dark mode toggle not working in settings" → [-0.31, 0.12, -0.54, ...]
```

The first two are semantically similar — close together. The third is about something different — far away.

**What cosine similarity measures**

Cosine similarity measures how aligned two vectors are, on a scale from -1 to 1. Two identical vectors have a cosine similarity of 1.0. Two completely unrelated vectors are closer to 0.

```
cosine_similarity(bug1_embedding, bug2_embedding) = 0.91   ← same issue
cosine_similarity(bug1_embedding, darkmode_embedding) = 0.23  ← different issue
```

You don't need to understand the math. The intuition is: the more similar the meaning, the closer to 1.0 the score.

**The threshold**

We use 0.85 as the duplicate gate. Above 0.85 → likely the same issue, flag as duplicate. Below 0.85 → different enough, let the LLM loop proceed.

This was calibrated by testing. 0.85 is high enough that "login crash" and "dark mode glitch" don't collide (they score ~0.2–0.4). It's low enough that "login crashes on empty password" and "app crashes when submitting empty password" do collide (they score ~0.88–0.92).

**The embedding cache**

Computing an embedding for every open Jira ticket on every run would be slow and expensive (100 tickets × API call each). Instead, embeddings are cached in `memory/ticket_embeddings.json`. At run start:

- New tickets (not in cache): compute and add
- Changed tickets (description updated): re-compute and update
- Closed tickets (no longer open): remove from cache

The cache is a diff operation. Only changed tickets cost an API call.

**Why this runs before the LLM loop**

An embedding call costs ~$0.00002. A GPT-4o tool-calling loop costs ~$0.01–0.05. The embedding gate is a 500–2500× cheaper pre-filter. For the common case (75% of messages are about new issues), the gate passes quickly. For the duplicate case, it saves the entire LLM call.

---

## Phase 5 — Eval and Feedback Loop
### What we built

After the agent posts a Jira ticket link to Slack, team members can react with 👍 or 👎. On the next run, the agent collects those reactions, stores them, and computes a rolling thumbs-up rate. If the rate drops below 70% across 5+ reactions, the agent fires a Slack alert.

### The concept: Human-in-the-Loop Feedback

**Why feedback loops matter for AI systems**

A traditional deterministic system (if message contains "crash" → create Bug ticket) either works or it doesn't. You can unit test it. It doesn't degrade over time.

An LLM-based system makes probabilistic decisions. It will be wrong sometimes. The question isn't *whether* it's wrong but *how often* and *in what direction*. Without a feedback signal, you have no way to know.

Feedback loops close the gap between "the agent ran" and "the agent made good decisions."

**Why Slack reactions specifically**

The team already uses Slack. A 👍 on a message takes one tap on a mobile phone. There's no separate review UI to log into, no form to fill out, no workflow to change.

Low-friction feedback is the only feedback you'll actually get. A high-friction feedback mechanism (even a good one) will be ignored.

**The warm-up gate**

One 👎 on the first run would drop the thumbs-up rate to 0%, firing an alert before there's any meaningful signal. The minimum 5-reaction gate prevents false alerts during the initial calibration period.

**What "quality degradation" signals**

A drop in thumbs-up rate below 70% can mean several things:
- The system prompt needs refinement (GPT-4o is classifying things incorrectly)
- A new pattern of messages appeared that the agent wasn't trained to handle
- The team's expectations shifted (what they call "High priority" changed)

The alert doesn't tell you *why* quality dropped. But it tells you to look. That's the signal.

---

## Phase 6 — Run Logger v2 and Action Tracking
### What we built

Extended the run log to capture exactly what action was taken per message (ticket created, clarification asked, duplicate flagged, or nothing), including the Slack timestamp of the bot's confirmation post — the link that ties a run log entry to a reaction.

### The concept: Data Modeling for Downstream Consumers

**Why `confirmation_ts` is the key field**

When the agent posts "✅ Created SCRUM-12: Login crash | High | Bug", Slack assigns that post a timestamp (`ts`). That timestamp is the message ID in Slack's model. When a team member reacts with 👍, Slack records which message timestamp was reacted to.

Without storing `confirmation_ts` in the run log at ticket-creation time, there's no way to connect "SCRUM-12 was created" (run log) with "this message got a 👍" (Slack reactions). The two datasets are disconnected.

This is a general principle: **log the identifiers your downstream consumers will need.** The run log was written for humans first (Phase 3). Phase 5 made it a consumer too — and it needed `confirmation_ts` to do its job.

**Extending the schema without breaking existing consumers**

The `BlockResult` dataclass gains new fields (`action`, `ticket_key`, `confirmation_ts`). The existing dashboard and test suite rely on existing fields. Adding new fields with `Optional` defaults means:
- Old data files (no `confirmation_ts`) still load correctly (field defaults to `None`)
- New consumers can use the new fields
- Old consumers ignore fields they don't know about

This is backward-compatible schema evolution — a discipline that matters whenever multiple things read the same data format.

---

## Phase 7 — Memory
### What we built

Three memory systems, each serving a different time horizon:
1. **Working memory** — the LLM message list within a single run (already existed; Phase 7 formalised it)
2. **Episodic memory** — a record of past decisions, stored on disk, searchable by embedding similarity
3. **Semantic memory** — patterns extracted from episodes, injected into the system prompt at run start

### The concept: Memory in AI Agents

Memory in AI agents is a design choice, not a given. A vanilla LLM has no memory across calls. Every invocation starts from scratch. For a system that's supposed to get better over time, this is a fundamental limitation.

The three types of memory address three different questions.

---

**Working Memory — what the agent knows right now**

Working memory is the LLM's context window — the `messages` list that grows during a single run.

```
messages = [
  {"role": "system", "content": system_prompt},
  {"role": "user",   "content": conversation_block},
  {"role": "assistant", "content": None, "tool_calls": [...]},
  {"role": "tool", "content": "Ticket SCRUM-12 created"},
  {"role": "assistant", "content": "Done — ticket created."}
]
```

Each tool call and result is appended. The model sees the full history of the current session when it generates each response. This is why the agent can say "I already created a ticket for the login crash" if the same block triggers a second tool call.

Working memory is ephemeral — it exists only within one run. When the run ends, it's gone.

---

**Episodic Memory — what the agent has done before**

Episodic memory stores concrete past decisions. Each episode captures one block-level outcome:

```
Episode {
  block_snippet:  "login page crashes on empty password"
  ticket_key:     "SCRUM-12"
  ticket_type:    "Bug"
  ticket_priority:"High"
  embedding:      [0.21, -0.43, ...]  ← vector of the block_snippet
  validation_status: "validated"      ← only after team 👍
}
```

When the agent encounters a new block, it embeds the block text and runs a cosine similarity search against all validated episodes. The top matching episodes are injected into the user message:

```
[Memory: similar past decision]
Block: "login page crashes on empty password"
→ Created SCRUM-12: Bug | High ✅ (team validated)
```

This gives the agent a concrete reference point. Instead of classifying "empty password crash" from scratch every time, it can see that the team approved "Bug:High" for almost-identical language last week.

**The validation gate:**

Episodes are stored as `unvalidated` first. They're only promoted to `validated` — and only returned by `search_memory()` — when the team reacts with 👍.

This is critical. Without the gate, the agent learns from its own mistakes. If it misclassified a bug as a story, and that episode gets injected as context for future similar messages, the misclassification reinforces itself. The validation step is what makes it safe to learn.

**Lazy retrieval:**

Episodes are not pre-injected for every block. The agent calls `search_memory(query)` only when uncertain. For the 80% of blocks that are clear-cut, this costs zero tokens. For the ambiguous 20%, it adds one embedding call and a small context injection.

---

**Semantic Memory — what the agent has learned**

Semantic memory stores general patterns extracted from accumulated episodes — not specific past decisions, but trends and heuristics:

```
## Learned Patterns (injected into system prompt)
- Bug tickets: 8× more common than Story tickets in this codebase
- High priority: 62% of all tickets
- Login-related messages: consistently classified as Bug:High
- "dark mode" messages: consistently classified as Task:Low
```

These patterns are generated by an LLM pass over accumulated validated episodes (triggered when episode count hits a threshold). They're injected once per run into the system prompt — not per block.

The distinction from episodic memory: episodic memory is specific ("last Tuesday's exact login crash → Bug:High"). Semantic memory is general ("login-related messages are always Bug:High in this codebase"). Injecting semantic patterns into the system prompt tunes the agent's priors for the whole run. Injecting episodic context into the user message gives it a concrete precedent for a specific block.

**Why three layers instead of one?**

Each layer operates at a different granularity and time horizon:

| Layer | Scope | When used | Token cost |
|-------|-------|-----------|------------|
| Working | Current run | Always | ~0 (already in context) |
| Episodic | Past decisions | On demand (uncertain blocks) | Small (top-K episodes) |
| Semantic | Learned patterns | Always (run-level injection) | Very small (short text) |

A single "memory" mechanism would either be too expensive (injecting all past episodes every time) or too sparse (injecting only summaries misses the specific precedents). The three layers let the agent be efficient and precise.

---

## Phase 8 — Model-Agnostic LLM Provider
### What we built

An abstraction layer between the triage agent and the OpenAI SDK. The agent no longer knows it's talking to OpenAI. It talks to an `LLMProvider` interface. The `OpenAIProvider` implements that interface. A factory function reads the `LLM_PROVIDER` environment variable and returns the right implementation.

### The concept: Abstraction Layers and the Protocol Pattern

**The problem with direct SDK coupling**

Before Phase 8, `triage_agent.py` had 9 direct references to OpenAI-specific code:
- `from openai import OpenAI`
- `_client.chat.completions.create()`
- `response.choices[0]`
- `choice.message`
- `json.loads(tool_call.function.arguments)`
- `except openai.APIError`

Swapping to Anthropic would require changing all of these — because Anthropic's SDK has a completely different response structure, different stop signals, different tool call format, and different error types. Business logic and infrastructure are tangled.

**What an abstraction layer does**

An abstraction layer defines a contract — a fixed interface that all implementations must honour — and hides the implementation details behind it.

```
triage_agent.py              (business logic — knows nothing about OpenAI)
    │
    │  turn = await _provider.chat(messages, tools, system_prompt)
    │  turn.finish_reason      ← always "stop" or "tool_calls"
    │  turn.tool_calls[n].args ← always a dict, never a JSON string
    │
    ▼
LLMProvider (Protocol)       (the contract — defines what chat() must return)
    │
    ▼
OpenAIProvider               (the implementation — wraps OpenAI SDK)
    → calls chat.completions.create()
    → normalises choices[0] → finish_reason
    → normalises json.loads(args) → dict
    → normalises openai.APIError → LLMProviderError
    → returns LLMTurn(finish_reason, content, tool_calls, tokens)
```

The agent talks to the contract. The contract talks to the implementation. Adding Anthropic means adding a new implementation — zero changes to the agent.

**The Protocol pattern in Python**

In Python, a `Protocol` is a structural type — it defines what methods and attributes a class must have, without requiring that class to explicitly inherit from anything.

```python
class LLMProvider(Protocol):
    async def chat(
        self, messages: list[dict], tools: list[dict], system: str = ""
    ) -> LLMTurn: ...
```

Any class with an `async def chat(...)` method that returns an `LLMTurn` satisfies this protocol. `OpenAIProvider` satisfies it. A future `AnthropicProvider` will satisfy it. Neither needs to `extend LLMProvider`.

**The factory pattern**

The factory function reads the `LLM_PROVIDER` environment variable and returns the right provider:

```python
def get_llm_provider(settings) -> LLMProvider:
    if settings.LLM_PROVIDER == "openai":
        return OpenAIProvider(api_key=settings.OPENAI_API_KEY, model=settings.LLM_MODEL)
    raise NotImplementedError(f"Provider '{settings.LLM_PROVIDER}' not implemented")
```

The caller doesn't know which provider they're getting. They just know they'll get something that can `chat()`.

**When to build abstractions**

Abstractions have a cost — more files, more indirection, more tests to write. The right time to build an abstraction is when:
1. You have a concrete second use case that the abstraction enables (Anthropic — Phase 9)
2. The coupling you're removing would require changing business logic to accommodate the new case

Building the abstraction speculatively (before any second use case exists) usually creates unnecessary complexity. Building it reactively (when you're mid-way through adding the second provider) is painful. The right moment is when the second case is designed and approved but not yet built.

---

## Phase 6 (Reliability) — Scheduling and Watermarks
### What we built

A watermark: after each run, the timestamp of the last processed Slack message is saved to disk. On the next run, only messages newer than that timestamp are fetched. A `--schedule N` flag that runs the agent in a continuous loop every N minutes.

### The concept: Idempotency and State Cursors

**The problem with polling without state**

Before this phase, every run fetched the last 20 messages from Slack. If you ran the agent twice within 10 minutes:
- Run 1: fetches messages 1–20, creates tickets
- Run 2: fetches messages 1–20 again (same 20), attempts to create the same tickets again

Without the duplicate gate, this would create duplicate tickets. With the duplicate gate, it would fire the "possible duplicate" flow on every re-run — polluting Slack with duplicate warnings. Either way, the agent is reprocessing messages it already handled.

**What a watermark is**

A watermark is a bookmark — the position you've processed up to. After processing messages 1–20, you save "I've processed up to ts=1714045900". On the next run, you ask Slack: "give me messages newer than 1714045900." Slack returns only messages 21, 22, 23... — the ones that arrived since your last run.

This is the same pattern used in:
- Database change-data-capture: "give me rows modified after updated_at > X"
- Event streaming (Kafka): "give me events after offset 12480"
- RSS feed readers: "give me posts published after last_checked"

The watermark advances on every successful run. If a run fails mid-way, the watermark doesn't advance — so the next run reprocesses from the last safe point.

**What idempotency means**

An operation is idempotent if running it twice produces the same result as running it once.

The Jira ticket creation API is not idempotent by default (two calls = two tickets). The duplicate detection gate makes the combined "check + create" operation idempotent: even if the same message is processed twice, the second pass detects the existing ticket and skips creation.

The watermark makes the fetch idempotent at the input level: the same messages are never fetched twice. Idempotency at the input + duplicate detection at the output = a system that's safe to re-run as often as needed.

**The scheduling loop**

```python
while True:
    oldest = load_watermark()        # what have we already processed?
    new_ts = await main(oldest)      # run with only new messages
    if new_ts:
        save_watermark(new_ts)       # advance the cursor
    await asyncio.sleep(interval)    # wait N minutes
```

This is the entire scheduler. No cron, no external job queue, no infrastructure. A single Python process that loops, sleeps, and loops again. For a single-machine deployment (a Mac, a cheap VPS), this is sufficient.

---

## Running Theme: What Each Phase Taught

| Phase | Concept | Core insight |
|-------|---------|-------------|
| 1 — Core Pipeline | LLM tool-calling | The loop is the agent. The tools are the actions. |
| 2 — Failure Transparency | Error policy | Decide what to do when things go wrong before writing error handling code. |
| 3 — Observability | Structured logging | You can't improve what you can't measure. Structured data > text logs. |
| 4 — Duplicate Detection | Embeddings + cosine similarity | Meaning can be measured. Close numbers = similar meaning. |
| 5 — Eval & Feedback | Human-in-the-loop signal | Low-friction feedback is the only feedback you'll actually get. |
| 6 — Action Tracking | Data modeling | Log the identifiers your downstream consumers will need. |
| 7 — Memory | Episodic / semantic / working memory | Memory is how an agent improves over time, not just runs correctly. |
| 8 — LLM Provider | Abstraction + Protocol pattern | Decouple business logic from infrastructure. Build the abstraction when the second use case is designed. |
| Reliability | Watermarks + idempotency | State cursors prevent reprocessing. Idempotency makes retries safe. |

---

*JiraSlack — built with Cursor + Claude Sonnet 4.6 | April 2026*
