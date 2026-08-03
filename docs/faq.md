# JiraSlack — Interview Prep Guide

> Read this before every practice session.
> The goal is to own the story — not memorise it word for word.
> Sections are ordered: pitch → deep dive → hard questions.

---

## THE PITCH (4 minutes)

### Opening — The Problem (30 seconds)

> "I built an AI agent called JiraSlack that eliminates the manual overhead of converting Slack messages into Jira tickets.
>
> The problem was simple: engineers report bugs in Slack, but someone has to manually read them, decide if they're worth a ticket, open Jira, fill everything in, and post the link back. That process takes 5 to 10 minutes per report, happens inconsistently, and gets skipped entirely when people are busy. Bugs fall through the cracks.
>
> With JiraSlack, you post in Slack as usual. The agent reads it, classifies it, creates the Jira ticket with a structured description and correct priority, and posts the confirmation back — all in under 30 seconds. No human in the loop."

---

### The Goal of the Agent (30 seconds)

> "I want to be specific about what the agent was designed to achieve — because this shaped every decision we made.
>
> The goal was not just 'create tickets faster.' The goal was to close the gap between a team member reporting something and the team committing to act on it. Those are different problems.
>
> So I defined four success criteria upfront: classify messages correctly at least 90% of the time, act without asking for clarification on at least 80% of messages, never create a duplicate ticket, and get from Slack message to Jira ticket in under 30 seconds.
>
> And critically — the agent had to know when it didn't know. Asking for clarification on a vague message is a correct outcome. Silently doing nothing is not."

---

### How It Works — The Pipeline (90 seconds)

> "The pipeline has five steps.
>
> First, the agent reads the last 20 messages from a Slack channel using the Model Context Protocol — MCP — which is a stdio-based integration that handles auth and rate limiting. We never touch HTTP directly.
>
> Second, messages are grouped into conversation blocks using a 5-minute time window. So if someone posts 'login is broken' and a minute later adds 'it started after yesterday's deploy', those two messages are treated as one conversation — the LLM sees the full context, not isolated fragments. This is pure Python — no LLM involved. Time is objective. Grouping is a structural problem, not a reasoning problem.
>
> Third — and this is the core — each conversation block goes into an agentic loop with GPT-4o. The LLM is sent three things: a system prompt with the rules, the Slack conversation block, and three tool schemas. The system prompt says: classify this message, decide if it's actionable, and use the right tool to handle it. The LLM is not told step 1, step 2, step 3 — it reads the situation and decides.
>
> The three tools are: `create_jira_ticket`, `post_slack_message`, and `ask_for_clarification`. The LLM decides which tool to call and writes all the arguments itself — the summary, description, issue type, priority, labels. My code executes it. Then the result is fed back into the conversation, and the LLM decides what to do next. This loops until it returns 'stop'.
>
> Fourth, if the LLM calls `create_jira_ticket`, the agent connects to Jira via MCP — `mcp-atlassian` via `uvx` — and the ticket appears in the real Jira project.
>
> Fifth, the agent posts a confirmation back to Slack: ticket key, title, and a direct link. The engineer who reported the bug sees immediate acknowledgement."

---

### Why It's an Agent — Not Just an API Call (30 seconds)

> "I want to be precise about why this qualifies as an agent.
>
> An agent has three properties: autonomy, tool use, and multi-step reasoning. We have all three.
>
> The LLM is not answering a question. It reads the situation and decides which tool to call — across multiple iterations — until it determines the goal is met. Each iteration, I append two things to the message list: what the LLM decided to call, and what the result was. GPT-4o has no memory between calls — the accumulated message list is its working memory. When it returns 'stop', that list is discarded and the next conversation block starts fresh.
>
> The LLM provides the judgement. My code provides the execution."

---

### The Hard Decisions (45 seconds)

> "Three decisions I'm particularly proud of.
>
> First: failure modes before the happy path. I defined seven priority rules upfront — what the agent does when Jira is down, when a message is vague, when the same bug is reported twice, when OpenAI is unavailable. For example: if a message is vague, don't wait for clarification — create the ticket with what you have, then post an INVEST prompt in Slack asking for the missing details. These rules were written before any code. They're the contract between the agent and the team.
>
> Second: grouping stayed in Python. I could have let the LLM group messages semantically. But grouping is a structural problem — which messages are temporally related — not a reasoning problem. Python does it free, deterministically, and testably. The LLM's job is reasoning about meaning, not sorting data by timestamp.
>
> Third: both integrations use MCP. Slack and Jira both run as stdio subprocesses. The pattern is identical — spawn the server, call a tool, read the result. Adding a third integration tomorrow means one new context manager, not a new HTTP client."

---

### Testing (30 seconds)

> "I built a three-layer test pyramid.
>
> Unit tests — 119 of them — mock all I/O. The MCP sessions, the OpenAI client, the tool executors — all mocked. They run in under a second and cover every branch.
>
> Integration tests spawn the real MCP subprocesses but only read — they never create tickets or post messages.
>
> E2E runs the full pipeline against live Slack and Jira.
>
> The unit tests caught a real bug: I was patching the function name in the module, but the tool dispatcher stores function references in a dict at import time. Patching the name doesn't update the dict. You need `patch.dict`. Without the test pyramid, that would have been a silent false-green forever."

---

### Closing (15 seconds)

> "Phase 1 through Phase 4 are complete and verified — Slack messages are classified, structured Jira tickets are created automatically, duplicates are caught before they reach the LLM, and the agent never fails silently. Phase 5 is Eval & Feedback: capturing 👍/👎 reactions on confirmation messages, computing quality metrics per run, and auto-tuning confidence thresholds based on real team feedback. Phase 6 is Reliability: watermark tracking so the agent never reprocesses old messages, and scheduled execution so it runs unattended."

---

---

## DEEP DIVE — What Gets Passed to the LLM

*Use this if the interviewer asks how the loop actually works.*

Every API call to GPT-4o sends exactly three things:

```
1. messages  — the full conversation so far
2. tools     — the 3 JSON schemas
3. model     — "gpt-4o"
```

The `messages` list grows with every iteration:

**Iteration 1 — what GPT-4o sees:**
```
{ role: "system",    content: "You are a triage agent. Classify messages, act, never fail silently..." }
{ role: "user",      content: "Slack messages: Login button crashes on empty password" }
```

**After GPT-4o calls create_jira_ticket — Iteration 2:**
```
{ role: "system",    content: "..." }
{ role: "user",      content: "Slack messages: Login button crashes..." }
{ role: "assistant", tool_calls: [{ name: "create_jira_ticket", arguments: { summary: "Fix login crash...", issue_type: "Bug", priority: "High", ... } }] }
{ role: "tool",      content: "Created SCRUM-5: Fix login crash → https://yoursite.atlassian.net/browse/SCRUM-5" }
```

**After GPT-4o calls post_slack_message — Iteration 3:**
```
[all previous messages...]
{ role: "assistant", tool_calls: [{ name: "post_slack_message", arguments: { message: "✅ Created SCRUM-5..." } }] }
{ role: "tool",      content: "Message posted: ✅ Created SCRUM-5..." }
```

GPT-4o reads the full history and returns `finish_reason: "stop"`. Loop ends.

**Key point:** The message list is the agent's working memory. GPT-4o has no memory between calls — only what's in the list. When the loop ends, the list is discarded. The next block starts fresh.

---

## DEEP DIVE — The System Prompt

*Use this if asked "what instructions does the LLM follow?"*

The system prompt encodes the rules:

- You are a triage agent monitoring a Slack channel
- For each conversation block: decide if it's a Bug, Story, Task, or too unclear to act on
- If clear enough → call `create_jira_ticket`
- After creating a ticket → call `post_slack_message` to notify the channel
- If unclear → call `ask_for_clarification`
- If it looks like a duplicate → call `post_slack_message` explaining why you skipped it
- Priority guide: Critical (production down), High (significant feature broken), Medium (partial), Low (minor)
- Always reply in the Slack channel after taking action

The system prompt never changes between iterations. The conversation history grows. The LLM reads both on every call.

---

## LIKELY INTERVIEW QUESTIONS — SHARP ANSWERS

---

**"Isn't this just GPT-4o with function calling?"**

> "Function calling alone is a one-shot decision. What makes this an agent is the loop with accumulated state. The result of each tool call is appended to the message list and sent back to the LLM on the next call. GPT-4o reads what already happened and decides what to do next — across multiple iterations. That's reasoning across time, not a single API call."

---

**"What goal is the LLM given?"**

> "The goal is encoded in the system prompt — not hardcoded in Python. It says: read this Slack conversation, classify it, decide if it needs a ticket, and use the right tool to handle it. The LLM is not given a sequence of steps. It reads the situation and the rules, then decides the path. My code only provides the tools and executes whatever the LLM chooses."

---

**"What exactly gets passed to GPT-4o?"**

> "Three things: the messages list, the tool schemas, and the model name. The messages list has a system message with the rules, a user message with the Slack conversation, and then — as the loop progresses — assistant messages recording what the LLM decided to call, and tool messages recording what came back. The LLM has no memory — only what's in that list. That list is its working memory for one conversation block."

---

**"Why MCP instead of direct REST?"**

> "Consistency and separation of concerns. Both integrations — Slack and Jira — use the same pattern: spawn a subprocess, call a tool, read the result. I'm not writing HTTP clients, handling auth renewal, or managing rate limits. Adding a third integration means one new context manager, not a new HTTP client. The subprocess overhead is negligible for a bot that runs periodically."

---

**"Could the LLM group the messages instead of Python?"**

> "Yes, and for semantic grouping — two messages about the same bug 10 minutes apart — the LLM would actually do better. But for Phase 1, temporal proximity is a good enough proxy for relatedness. The Python approach is deterministic, free, and easy to test. The LLM's job is reasoning about meaning, not sorting data by timestamp. I'd revisit semantic grouping in Phase 2 if grouping accuracy became a measured problem."

---

**"How did you handle failure cases?"**

> "Seven priority rules, written before any code. For example: if Jira is down, post to Slack immediately — never fail silently. If a message is vague, create the ticket with what's available and post an INVEST prompt asking for the missing details — never block on missing information. If the same bug is reported twice, surface the match to the human and let them decide — never auto-skip. These rules are the contract between the agent and the team."

---

**"How did you test the AI component?"**

> "I mock the OpenAI client at the `_client` level and control exactly what `chat.completions.create()` returns — a MagicMock with a specific `finish_reason` and `tool_calls`. This lets me test 'agent calls tool then stops', 'agent loops twice', 'agent hits max iterations' — deterministically, in milliseconds, without ever calling OpenAI. The actual OpenAI behaviour is verified in E2E only."

---

**"What would you do differently?"**

> "Two things. Write `pytest.ini` with `asyncio_mode = auto` on day one — async tests silently skip without `pytest-asyncio` installed, and I discovered that after the fact. And I'd define the failure mode rules even earlier — before the happy path — which I did here, but in past projects I've retrofitted them and it's always harder than designing for them upfront."

---

**"Can you say you built an agent?"**

> "Yes — precisely. An agent has three properties: autonomy, tool use, and multi-step reasoning. The LLM decides what to do without being told step by step. It takes real actions in external systems — Jira tickets, Slack messages. And it reasons across multiple iterations, where each action informs the next decision. The loop with accumulated state is what separates an agent from a single API call."

---

**"How does the agent detect duplicate tickets?"**

> "Three steps. First, at run start we fetch all open Jira tickets — in parallel with the Slack fetch using `asyncio.gather()`, so it adds zero wall-clock time. We paginate in batches of 100 up to a configurable page cap, so the gate works correctly even on large projects with hundreds of open tickets. Second, each ticket summary is embedded using OpenAI `text-embedding-3-small` and cached locally — closed tickets are pruned from the cache automatically each run. Third, before creating any ticket, we embed the Slack conversation block and compute cosine similarity against every cached ticket vector. If similarity exceeds the threshold — default 0.85 — we flag it as a duplicate and post a match to Slack for human confirmation instead of creating a new ticket."

---

**"Why embeddings and not keyword matching for duplicate detection?"**

> "Keyword matching breaks on paraphrase. 'Login crash' and 'login button not working' share minimal word overlap but are the same bug — keyword matching would miss it and create a duplicate anyway. Embeddings capture semantic meaning, not word overlap. Both sentences produce similar vectors because they mean the same thing. That drops the false positive rate from ~10% for keyword matching to under 3% for embeddings — and it's one OpenAI API call, same vendor we're already using for GPT-4o."

---

**"What about the latency of hitting Jira and embedding on every run?"**

> "Two mitigations. First, the Jira ticket fetch runs in parallel with the Slack message fetch using `asyncio.gather()` — both are async MCP calls, neither depends on the other. The Jira fetch is completely hidden inside the Slack fetch time we were already paying. Second, ticket embeddings are cached in `memory/ticket_embeddings.json`. Once a ticket is embedded, we never embed it again. Each run also prunes the cache — entries for closed tickets are removed before new ones are added. First run pays full cost — one batch embedding call for all open tickets. Every subsequent run only embeds genuinely new tickets — usually zero to two calls. Net latency after the first run is under 500ms total."

---

**"How do you keep the embedding cache fresh? What if tickets are closed or new ones are added?"**

> "At run start we fetch just the ticket keys and updated timestamps from Jira — a lightweight call. Then we diff that against the cache: new keys get embedded and added, keys no longer in the open list get pruned. The agent also writes directly to the cache immediately after creating a new ticket, so there's no lag for agent-created tickets. The cache is always consistent with Jira's open ticket state by the time we start processing blocks."

---

**"What happens if a project has hundreds of open tickets?"**

> "The gate paginates. Each call to `jira_search` returns up to 100 tickets. We loop with a `start_at` offset until we receive fewer results than the page size — that's the last page — or we hit a configurable cap, currently 10 pages, so up to 1000 open tickets. If a network error fires mid-pagination we return whatever we already accumulated rather than discarding it. And the embedding cache means the overhead is almost entirely front-loaded — second run onwards only embeds new tickets, not the full set."

---

**"Isn't calling the OpenAI SDK synchronously inside an async function a problem?"**

> "Yes — that was a bug we fixed. The original code called `_client.chat.completions.create(...)` directly inside `_run_llm_loop`, which is an `async def`. A synchronous call blocks the entire event loop for the duration of the round trip — nothing else can progress while it's waiting. The fix is `await asyncio.to_thread(_client.chat.completions.create, ...)`, which runs the synchronous call on a thread pool executor and gives the event loop back while it waits. The `embed_texts` function had this right from the start. The LLM call didn't — catching it during the debt review was worth doing before adding any scheduled or concurrent workload in Phase 5."

**"Why does semantic memory matter — couldn't you just hardcode team-specific rules in the system prompt?"**

> "You could — and that's exactly the problem. The system prompt today is generic — it doesn't know anything about our specific team or codebase. So GPT-4o has to guess from first principles every time. It might pick High priority for a login bug this week and Medium next week just because the wording is slightly different. No consistency, no learning.
>
> Semantic memory solves this by automatically extracting patterns from past decisions — things like 'login bugs in this project are High priority 87% of the time' — and injecting them into the system prompt at runtime. GPT-4o gets the same model, but now it has institutional knowledge about the team baked into its context.
>
> The alternative — hardcoding patterns in the prompt — means I have to manually edit the prompt every time the team's patterns change. Semantic memory does that automatically from real data.
>
> The key insight: the model stays general. Your data makes it specific."

---

**"Why does episodic memory run before the LLM call instead of passing it in as context?"**

> "Because episodic memory is a hard check, not input for reasoning. If I've already created SCRUM-7 for this exact message, the answer is definitive — don't create it again. I don't need the LLM to reason about whether this is a duplicate. I already know.
>
> If I passed it into the prompt as context — 'by the way, this looks similar to a past message' — I'm asking GPT-4o to make a judgement call on something I have a ground truth answer for. That introduces unnecessary LLM latency and the possibility of the LLM getting it wrong.
>
> The rule is: use the LLM for decisions that require reasoning. Use deterministic code for decisions you already know the answer to. Episodic duplicate detection is the second kind."

---

**"What's the difference between episodic and semantic memory in your system?"**

> "Episodic is specific: 'On April 29, I saw this exact Slack message and created SCRUM-7 as a High priority Bug.' One event, timestamped, tied to a specific decision.
>
> Semantic is general: 'Login bugs are almost always High priority Bugs.' A pattern extracted from many episodes.
>
> The relationship between them is deliberate — semantic patterns are built from episodic rows. I need enough specific experiences before I can reliably generalize. That's why semantic extraction runs after all blocks are processed each run, and patterns need at least 5 supporting episodes before being injected.
>
> You could think of episodic as the agent's diary and semantic as its handbook — the diary is raw and specific, the handbook is the lessons extracted from it."

---

**"How do you ensure memory quality — couldn't the agent just learn from its own mistakes?"**

> "That was exactly my concern. And it's why I built memory quality controls around five principles. Let me walk through each one plainly.
>
> **1. Scope what gets saved.**
> Not every decision should be remembered. If the agent misclassified a ticket and the team reacted with a 👎, we already capture that signal through Phase 5 eval. The next step is connecting that signal back to memory: episodes are stored as 'unvalidated' first. When `pre_run` checks the eval store and finds a 👍 on that episode's Slack confirmation, the episode is promoted to 'validated' — and only validated episodes are returned by `search_memory`. A wrong decision never reinforces itself.
>
> **2. Source where facts came from.**
> Every episode already stores the Jira ticket it created, the timestamp, the confidence score, and the first 60 characters of the original Slack message. When the LLM calls `search_memory`, it sees: 'SCRUM-42 — login crash on empty password — Bug, High (2026-04-28)' — not an abstract pattern. It knows exactly which real decision it's drawing from and how old that decision is.
>
> **3. Age out stale context.**
> Memory evicts by count today — oldest 200 episodes kept — but a six-month-old decision about a rewritten module can still pollute current classifications. The fix is a `MAX_EPISODE_AGE_DAYS` setting (default 90 days). On every `pre_run`, episodes outside that window are filtered before retrieval. Old patterns fade out naturally within a quarter without any manual cleanup.
>
> **4. Review before high-stakes actions.**
> `search_memory` is a lazy tool — the LLM calls it only when uncertain, which is the whole point. But for Critical priority tickets — where getting the type wrong has real consequences — we don't leave it to the LLM's discretion. One line in the system prompt: 'Before creating a Critical priority ticket, always call `search_memory` first.' Routine bugs stay zero-token. Critical decisions always check history.
>
> **5. Retire what no longer matters.**
> Two mechanisms work together: TTL from principle 3 ages out anything older than 90 days, and the existing 200-episode FIFO cap means newer decisions crowd out older ones as the agent runs. If you decommission a feature and want to clear its patterns immediately, `memory/episode_store.json` is plain JSON — readable and editable directly. Fully automatic semantic retirement would require the agent to understand your product roadmap, which is too complex to automate reliably at this stage.
>
> The most important of the five is the quality gate — principle 1. The rest are operational hygiene. But without it, the agent is learning from its own errors and compounding them over time."

```
run_triage.py
     │
     ├── fetch_messages()         ←── Slack MCP (npx subprocess, stdio)
     │
     ├── build_context_blocks()   ←── pure Python, 5-min time window, no LLM
     │
     └── for each block:
              │
              ▼
         ┌─────────────────────────────────────────────┐
         │              _run_llm_loop()                 │
         │                                              │
         │  messages = [system prompt, slack block]     │
         │                                              │
         │  ┌──────────────────────────────────────┐    │
         │  │  GPT-4o                              │    │
         │  │  Input:  messages + 3 tool schemas   │    │
         │  │  Output: tool_call OR stop           │    │
         │  └──────────────────────────────────────┘    │
         │         │                                    │
         │    tool_calls?                               │
         │    ├── YES → execute tool (Python code)      │
         │    │         append [assistant msg + result] │
         │    │         loop again ──────────────────►  │
         │    └── NO (stop) → exit loop                 │
         └─────────────────────────────────────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     Jira MCP                  Slack MCP
  (uvx mcp-atlassian)   (@modelcontextprotocol/server-slack)
  creates the ticket        posts the message
```

---

## REFERENCE — The 7 Priority Rules

Memorise these. They show you thought beyond the happy path.

| Rule | Situation | Agent behaviour |
|---|---|---|
| 1 | Jira is down | Post to Slack — never fail silently |
| 2 | Message is vague | Create ticket with what's available + post INVEST prompt |
| 3 | Confidence 65–90% | Create ticket + flag "not fully confident — please review" |
| 4 | Duplicate detected | Post match in Slack — human decides, never auto-skip |
| 5 | Slack MCP fails mid-run | Continue processing remaining blocks, post consolidated error at end |
| 6 | OpenAI is down | Post exact error + instruct team to triage manually |
| 7 | Same bug reported twice | Apply Rule 4 for second report — first ticket stands |

---

## ONE-LINER (for networking / elevator pitch)

> "I built an AI agent that reads a Slack channel for bug reports and automatically creates structured Jira tickets — using GPT-4o's tool-calling loop and the Model Context Protocol for both integrations — with explicit handling for every failure mode and a full test pyramid."

---

*Practice tip: record yourself once. The parts where you slow down or lose confidence are exactly where to spend more time.*
