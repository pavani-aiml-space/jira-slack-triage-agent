# Building JiraSlack — The Story

> **Format:** Living document. Each phase gets its own section.
> Update this after every `/closeout`.
> Purpose: LinkedIn posts, conference talks, portfolio narrative.
> Tone: First-person, non-technical, decisions and evolution — not code.

---

## How to Use This Document

- **For LinkedIn:** Each phase section is a self-contained post. Add the "What's next" callout and publish.
- **For talks/portfolio:** The full document tells the arc from idea → production-grade agent.
- **Not included:** Code snippets, API details, test counts. See `docs/interview-prep.md` for that.

---

# Part 1 — Starting Simple, Then Getting Honest

> **Phase 1 complete. Phases 2–7 in progress.**

---

## The Problem I Was Trying to Solve

If you've worked on an engineering team that uses Slack and Jira, you've lived this:

Someone posts in the team channel — *"hey, the login page is crashing for users on mobile"* — and then nothing happens for an hour because nobody's sure whose job it is to open Jira. When someone finally does, they spend 5 minutes writing a ticket that could have been auto-filled from the Slack message that was already sitting there, fully formed.

The waste isn't the 5 minutes. The waste is the gap — the time between "someone noticed a problem" and "the team committed to fixing it." That gap is where bugs fall through the cracks.

I wanted to close it.

---

## My First Instinct Was Wrong

My first thought: "I'll write a classifier. It reads the message, returns Bug/Story/Task/Unclear, and I'll call the Jira API."

So I built that. A standalone Python function that called GPT-4o, gave it the Slack message, and got back a structured JSON: `{type: "Bug", priority: "High", summary: "...", confidence: 0.87}`.

It worked for clear messages. But it immediately broke on the real world:

- A message was a Bug AND needed a clarification ask — the classifier could only return one thing
- Confidence was 0.72 — should I create the ticket or not? The threshold felt arbitrary
- I created the ticket but then had to write separate code to post back to Slack — another integration, another place to fail
- The classifier had no sense of *what to do next* — it just labelled. I still had to write all the decision logic myself

I had built a labeller, not an agent.

---

## The Shift: From "Classify Then Act" to "Give It a Goal and Tools"

The insight that changed the design: instead of asking GPT-4o *"what is this message?"*, I should ask it *"here's the situation, here are the tools available to you — what do you do?"*

This is the difference between a classifier and an agent.

I rewrote the core around three ideas:

**1. Give the LLM a goal, not a task list.**
The system prompt doesn't say "Step 1: classify. Step 2: if Bug, call create_ticket." It says: "You are a software triage agent. Your job is to identify actionable issues and handle them. Here are the rules." The LLM decides the sequence.

**2. Give the LLM tools, not a form to fill in.**
Instead of extracting fields from GPT-4o's JSON response, I gave it three callable tools: `create_jira_ticket`, `post_slack_message`, and `ask_for_clarification`. The LLM writes all the arguments itself — summary, description, priority — and my code just executes them. GPT-4o went from being a classifier to being the decision-maker.

**3. Loop until done.**
A single LLM call can't handle "create ticket, then post to Slack." So the agent loops: call GPT-4o → if it wants to use a tool, run it → feed the result back → call GPT-4o again → repeat until it says stop. This is the agentic loop. Each iteration the LLM sees the full history of what it did and decides what's next.

This pattern — goal + tools + loop — is the foundation of every production AI agent. I didn't fully appreciate how different it was from "call GPT-4o and parse the response" until I built both.

---

## The Pieces, Without the Code

Here's how the full pipeline works, at the level you'd explain it to a PM:

```
Slack channel
    ↓
Read last 20 messages (via Slack MCP — handles auth, rate limits)
    ↓
Group into conversation blocks (5-minute time windows)
Why: one bug report often spans 3–4 messages in quick succession.
     The LLM needs the full context, not fragments.
    ↓
For each block → Agentic Loop
    - LLM reads the block + the rules
    - LLM calls create_jira_ticket → ticket appears in Jira
    - LLM calls post_slack_message → confirmation posted in Slack
    - Loop ends when LLM says "stop"
    ↓
Jira ticket created. Slack confirmation posted. Done.
```

What makes it "agentic" (and not just a pipeline):
- The LLM decides whether to create a ticket, ask for clarification, or flag a duplicate — it's not hardcoded
- The LLM writes the ticket content (summary, description, priority, labels) from context — not a template
- The LLM can call multiple tools in sequence and adapts if a tool returns an error
- The LLM knows when it's done — it returns "stop" when no more action is needed

---

## The Integration Choice That Unlocked Everything

Both Slack and Jira are external services. I could have called their HTTP APIs directly, but I chose a different approach: **Model Context Protocol (MCP)**.

MCP is a standard for connecting AI agents to external tools via a local subprocess. Instead of calling `https://api.slack.com/...` directly, I spin up a small stdio server that handles auth, rate limiting, and error formatting — and the agent just calls `session.call_tool("slack_post_message", {...})`.

Why this mattered:
- Same integration pattern for every external service — Slack, Jira, and any future tools all look identical to the agent
- Auth credentials never touch the LLM — they stay in environment variables on the server process
- Errors come back as structured tool results — the LLM can read "Jira unavailable" as a tool response and decide what to do

This one decision — MCP for all external services — made the architecture extensible. Adding a new integration is the same 10-line pattern every time.

---

## The Hard Decision Nobody Talks About: What Happens When It Fails?

Building the happy path is easy. Building trust is hard.

An AI agent that silently does nothing when Jira is down is worse than no agent at all. Your team assumes the ticket was created. It wasn't. The bug gets missed.

So I defined explicit rules for every failure scenario before writing any error-handling code:

| If this fails | The agent does this |
|---|---|
| Jira unavailable | Post to Slack: "Could not create ticket — please file manually." Keep processing other messages. |
| OpenAI unavailable | Post to Slack: "Agent stopped — please triage manually." Exit. |
| Slack MCP dies mid-run | Keep processing. At the end, post one consolidated summary of everything that failed. |
| Slack itself is down | Write the error to terminal output. Exit with failure code. |

These aren't edge cases — they're the difference between an agent your team trusts and one they eventually ignore.

The key insight: **transparency wins over correctness.** It's better to tell the team "I failed to create this ticket" than to say nothing and let them assume everything worked.

---

## What I Learned Building Phase 1

**1. Start with the failure modes, not the happy path.**
I spent a session writing the happy path and then realized I'd have to rewrite most of it when I actually defined what should happen on errors. Design the error contract first.

**2. The agentic loop is simple. Trust in the loop is not.**
The actual loop — call LLM, get tool call, execute it, feed result back — is maybe 30 lines of code. But knowing *when to trust the LLM's decision* — whether to act, when to ask, how to handle low confidence — required me to write explicit priority rules and test them case by case.

**3. Mocking external services is the only way to test an agent.**
You can't run Jira and Slack in a unit test. So every test replaces those calls with controlled fakes that return known responses. This forced clarity: what exactly should the agent do when Jira returns a 503? When Slack MCP exits early? Testing isn't just coverage — it's the specification.

**4. The classifier I threw away taught me more than the agent I kept.**
Building the wrong thing first gave me a concrete understanding of why the agentic pattern is better. If I'd read about tool-calling loops in a blog post, I might have implemented them mechanically. Building the wrong approach first gave me the *why*.

---

## Where This Is Going

Phase 1 is a working agent. But it has known gaps:

- **No error handling** — if Jira or OpenAI fails, the agent crashes silently *(Phase 2 — in progress)*
- **No duplicate detection** — if the same bug is reported twice, two tickets get created *(Phase 4)*
- **No observability** — after a run, you don't know what happened without reading logs *(Phase 3)*
- **No memory** — the agent starts from zero every run, with no knowledge of past decisions *(Phase 7)*

Each of these gaps has a designed solution. The sequence matters — failure transparency first, then observability, then intelligence (duplicates), then reliability, then eval and memory.

The goal isn't features. The goal is an agent your team actually relies on.

---

*Next update: Phase 2 — Failure Transparency complete.*

---

# Part 2 — Making Failures Visible

> *Coming after Phase 2 build + audit.*

---

# Part 3 — Seeing What the Agent Did

> *Coming after Phase 3 (Observability) build + audit.*

---

# Part 4 — Never Creating the Same Ticket Twice

> *Coming after Phase 4 (Duplicate Detection) build + audit.*

---

# Part 5 — Running on a Schedule, Not on Demand

> *Coming after Phase 5 (Reliability) build + audit.*

---

# Part 6 — Measuring Whether the Agent Is Actually Good

> *Coming after Phase 6 (Eval & Feedback) build + audit.*

---

# Part 7 — Giving the Agent Memory

> *Coming after Phase 7 (Memory) build + audit.*
