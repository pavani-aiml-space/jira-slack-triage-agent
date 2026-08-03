# JiraSlack Triage Agent — Business Case & Design Document

> **Status:** In Progress
> **Last Updated:** 2026-04-25
> **Owner:** Pavani

---

## 1. Problem Statement

Software teams use Slack as their primary communication tool. When something breaks or a new feature is needed, team members send messages like:

> *"hey the checkout button isn't working"*
> *"same issue on mobile too"*
> *"it started after the 2pm deploy"*

**What happens today is painful:**
- Someone has to manually read Slack messages
- Decide if it's a bug, story, or task
- Copy-paste details into Jira manually
- Fill in priority, labels, description fields
- This takes 5–10 minutes per ticket
- Issues get missed at nights and weekends
- Multi-message context is often lost

---

## 2. Solution

An AI triage agent that:
- Listens to Slack DMs
- Groups related messages using a configurable time window
- Checks for duplicate Jira tickets
- Classifies the issue using Claude (LLM)
- Auto-creates a Jira ticket if confidence is high
- Replies in Slack with the ticket link

---

## 3. Who Uses It

**Anyone on the team** can DM the Slack bot:
- QA engineers reporting bugs during testing
- Developers flagging issues during development
- Managers requesting new features
- Non-technical users reporting problems

---

## 4. What It Handles

| Message Type | Example | Action |
|-------------|---------|--------|
| **Bug** | "checkout button not working after deploy" | Create Bug ticket, reply with link |
| **Story** | "can we add dark mode to the dashboard" | Create Story ticket, reply with link |
| **Task** | "update the README with new API docs" | Create Task ticket, reply with link |
| **Unclear** | "something feels off" | Ask for more info |

---

## 5. The Full Flow

```
Anyone DMs Slack bot
        ↓
Read last N messages (configurable time window)
        ↓
Group related messages into one conversation block
        ↓
Check Jira for duplicate open tickets
        ↓
Claude classifies → Bug / Story / Task / Unclear
        ↓
        ├── Confidence > 90%  → Auto-create Jira ticket
        │                     → Reply in Slack with ticket link
        │
        ├── Confidence 65-90% → Ask user to confirm
        │                     → On yes: create ticket + reply with link
        │
        └── Confidence < 65%  → Ask for more info
                              → Wait for follow-up messages
```

---

## 6. Concrete Example

**Monday 9:15 AM** — Pavani DMs the bot:
> *"payment gateway is timing out"*

**9:16 AM** — Pavani DMs the bot:
> *"users getting a blank screen after clicking Pay"*

**What the agent does:**
1. Groups both messages (within 5 min window → same context)
2. Checks Jira → no similar open ticket found
3. Claude classifies:
   ```
   Type       : Bug
   Priority   : Critical
   Summary    : Payment gateway timeout causes blank screen on Pay
   Confidence : 96%  → auto-act
   Duplicate  : None found
   ```
4. Creates `SCRUM-3` in Jira with full description
5. Replies in Slack:
   > *"Created SCRUM-3 — Bug, Critical: 'Payment gateway timeout causes blank screen on Pay' → https://pavaniaiml75.atlassian.net/browse/SCRUM-3"*

---

## 7. Configurable Settings

All behaviour is tunable via `config/.env`:

| Setting | Default | Description |
|---------|---------|-------------|
| `CONTEXT_WINDOW_MINUTES` | `5` | Group messages within this time window |
| `MAX_MESSAGES_TO_FETCH` | `20` | How many recent DM messages to read |
| `CONFIDENCE_AUTO_ACT` | `0.90` | Above this → auto-create Jira ticket |
| `CONFIDENCE_ASK_HUMAN` | `0.65` | Below this → ask user for more info |

**Tuning examples:**

- Fast-paced team (quick short messages): `CONTEXT_WINDOW_MINUTES=10`
- Cautious team (always review before creating): `CONFIDENCE_AUTO_ACT=0.95`
- Solo developer (trust agent more): `CONFIDENCE_AUTO_ACT=0.75`

---

## 8. System Architecture

```
config/.env              ← all credentials + tunable settings
        ↓
agents/triage/
  ├── tools/
  │   ├── mcp_client.py       ← connects to Atlassian MCP (HTTP + API token)
  │   ├── slack_reader.py     ← reads DMs via Slack MCP, groups by time window
  │   ├── context_builder.py  ← combines messages into one conversation block
  │   └── duplicate_checker.py← searches Jira for similar open tickets
  ├── classifier.py           ← Claude classifies Bug/Story/Task/Unclear
  └── triage_agent.py         ← orchestrates the full flow
run_triage.py                 ← entry point
```

---

## 9. MCP Servers Used

| MCP Server | Type | Purpose |
|-----------|------|---------|
| `https://mcp.atlassian.com/v1/mcp` | Remote (Atlassian cloud) | Create/search Jira tickets |
| `@modelcontextprotocol/server-slack` | Local (npm, runs on machine) | Read DMs, send replies |

---

## 10. Build Status

| Component | Status |
|-----------|--------|
| Jira MCP connection | ✅ Done — tested, SCRUM-2 created |
| Slack MCP config | ✅ Configured in `mcp.json` |
| `mcp_client.py` | ✅ Done |
| `config/settings.py` | ✅ Done |
| `config/.env` | ✅ Credentials filled in |
| `slack_reader.py` | 🔲 Next |
| `context_builder.py` | 🔲 Pending |
| `duplicate_checker.py` | 🔲 Pending |
| `classifier.py` | 🔲 Pending |
| `triage_agent.py` | 🔲 Pending |
| `run_triage.py` | 🔲 Pending |

---

## 11. Why This Matters

| Without Agent | With Agent |
|--------------|-----------|
| Manual copy-paste to Jira | Auto-created ticket |
| Bugs missed on nights/weekends | 24/7 monitoring |
| Context lost across messages | Multi-message grouping |
| Inconsistent priority setting | Claude applies rules consistently |
| 5–10 min per ticket | ~5 seconds |
| Duplicates created accidentally | Duplicate detection built in |
