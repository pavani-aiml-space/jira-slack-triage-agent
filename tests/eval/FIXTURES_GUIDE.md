# Eval Fixture Guide — Classification Principles

> Companion to `tests/eval/label_fixtures.json`.
> These are the rules we use to label examples. Share with the team before running evals
> so everyone agrees on what "correct" means before measuring it.

---

## The Two Questions That Determine Every Label

**Question 1 — Type: Bug, Story, or Task?**

| If... | Then |
|-------|------|
| Something that worked before is now broken | Bug |
| Something new needs to be built that doesn't exist | Story |
| Work needs to be done that isn't a user-facing feature or a broken behaviour | Task |

The easy test: **"Was this ever working correctly?"**
- Yes → Bug
- No, it's new → Story
- It's internal/operational work → Task

**Question 2 — Priority: High, Medium, or Low?**

| Signal | Priority |
|--------|----------|
| Production is down / nobody can use a core feature | High |
| Feature is broken for many users but there's a workaround | Medium |
| Cosmetic, partial, edge-case, or single-user | Low |
| Explicit business signal: "blocker", "enterprise deal", "launch date" | Bump up one level |

---

## Where People Get It Wrong — The Tricky Cases

### "Sounds like a feature request but it's a Bug"

> *"The app should warn me before I permanently delete a project. I just deleted the wrong one."*

The word "should" makes this look like a Story. But the missing warning guard caused **unrecoverable data loss**. A missing safety control that causes harm is a defect, not a feature gap. **Bug:High.**

The test: *"Did the absence of this thing cause damage that the user didn't expect?"* If yes → Bug.

---

### "Sounds like a Bug but it's a Task"

> *"The API docs are completely wrong for the /orders endpoint."*

"Completely wrong" triggers the Bug instinct. But the API itself is correct — the documentation is a separate artefact that's outdated. The product isn't broken. A task needs to be done. **Task:Medium.**

The test: *"Is the code/product behaving incorrectly, or is an external artefact (docs, README, config) out of date?"* Out-of-date artefacts → Task.

---

### "Sounds like a Task but it's a Story"

> *"We need to add two-factor authentication before the enterprise launch."*

Adding 2FA sounds like a work item (Task). But 2FA is a **new user-facing capability** that doesn't exist today. Users will experience it. It has acceptance criteria. It's a Story. The business context ("enterprise launch", "blocker for the deal") makes it **Story:High**.

The test: *"Will a user experience this change directly?"* If yes → Story, not Task.

---

### "Task with High priority"

> *"We need to rotate the AWS API keys — they were committed to the public repo."*

Not a Bug (the product isn't broken), not a Story (no new feature). It's an operational Task. But it's **High** because it's a time-sensitive security action. Priority and Type are independent axes.

Common mistake: agents classify security incidents as Bug because "something went wrong." The product didn't break — a human made an error that requires remediation. **Task:High.**

---

### Multi-person blocks → one ticket, not many

> *"Alice: 404 on /api/users. Bob: yeah same. Charlie: all GET requests failing."*

Three people reporting the same issue in one conversation block should produce **one** Bug:High ticket, not three separate tickets. The block is the unit of work, not the message. Multiple reporters confirming the same symptom is evidence that bumps priority up, not a signal to create multiple tickets.

---

### "Low priority" vs "Medium priority" — the distribution matters

A broken link is Low. *Unless* it's in the onboarding email that goes to every new user — then it's Medium, because the reach is broader.

Priority isn't just about severity. It's severity × reach. The same defect in a core user flow (login, checkout, signup) is one priority level higher than the same defect in an admin panel or settings page.

---

### When to ask for clarification instead of creating a ticket

Ask when you can't answer **both** of these:
1. What is broken / what needs to exist?
2. What is the impact / who is affected?

> *"Something's wrong with the dashboard"* → can't answer either. Ask.
> *"The export button is broken for PDF files"* → can answer both. Create.

If you have enough to create a useful ticket, create it. Asking for clarification when you have sufficient information delays value for the team.

---

## Coverage Map — What the 25 Fixtures Cover

| Category | Count | Tricky |
|----------|-------|--------|
| Bug:High | 4 | 1 (multi-reporter) |
| Bug:Medium | 4 | 0 |
| Bug:Low | 2 | 0 |
| Story:High | 1 | 1 (2FA looks like Task) |
| Story:Medium | 2 | 1 (new behaviour looks like Bug) |
| Story:Low | 1 | 0 |
| Task:High | 1 | 1 (security incident) |
| Task:Medium | 3 | 0 |
| Task:Low | 1 | 0 |
| Clarification needed | 3 | 0 |
| **Total** | **22** | **5** |

The 5 tricky cases are the ones the agent is most likely to get wrong and the most valuable for calibration. When adding new examples, prioritise cases where the correct label is non-obvious — easy cases don't improve the regression suite.

---

## How to Add New Examples

When a team member clicks 👎 on a ticket and provides the correct label via DM, add it here:

```json
{
  "id": "validated-001",
  "source": "validated",
  "slack_text": "...",
  "correct_type": "Bug",
  "correct_priority": "Medium",
  "correct_action": "create_jira_ticket",
  "notes": "Team confirmed correct label via 👎 DM reply",
  "tricky": false
}
```

`source: "validated"` means the label came from a real team reaction, not manual curation. These are more valuable than manually-curated examples because they represent actual team judgments on real messages.

Target: **50 validated examples** before running precision/recall/F1 against the full dataset.

---

## Simple steps: fixtures, gold judge, mismatch judge

This section is the **how-to** for eval fixtures and for checking the LLM judge. The judge always answers one plain question: **“Does this ticket match this Slack message?”** It never opens Jira; it only sees the text we give it.

### Part A — Build and maintain `label_fixtures.json`

1. **Pick a Slack message** (or a short thread) that you want to be a reference example.
2. **Decide the right answer** using the rules earlier in this guide (Bug vs Story vs Task, High vs Medium vs Low).
3. **Add one JSON object** to the `labels` array in `tests/eval/label_fixtures.json` with at least:
   - `id` — unique string  
   - `slack_text` — the message(s)  
   - `correct_type`, `correct_priority`, `correct_action` (`create_jira_ticket` or `ask_for_clarification`)  
   - `notes` — why this label is correct (helps future you and the team)  
   - `tricky` — `true` if the case is easy to mis-label  
4. **Save the file** and commit when you are happy.  
5. **Clarification-only rows** (`ask_for_clarification`) are kept for human rubric and future triage tests; the judge calibration script **skips** them because there is no single “correct ticket” to score.

### Part B — Gold calibration (judge agrees with humans)

**What it is:** For each `create_jira_ticket` row, we build a **fake ticket that matches your labels** (same type and priority as `correct_*`, description from `slack_text` + notes). We send that plus the full Slack text to the judge. If the judge usually gives **high** type and priority scores, it is **aligned** with your rubric.

**Steps:**

1. Fill in `config/.env` with `OPENAI_API_KEY` and judge settings if needed (`JUDGE_LLM_MODEL`, etc.).  
2. From the repo root run:  
   `python run_judge_calibration.py`  
   (default **gold** mode.)
3. Read the printed table: **PASS** means type and priority scores are both at or above `--threshold` (default 4).  
4. Read the **agreement rate** at the bottom — share of non-error rows that passed.  
5. If many **LOW** rows or a low agreement rate, a human should spot-check those fixtures and the judge prompt/model — the fixtures might be wrong, or the judge might not match your intent.  
6. Optional: `python run_judge_calibration.py --json-out logs/judge_calibration_gold.json` to save results.

**Useful flags:** `--fixtures PATH`, `--threshold 4`, `--concurrency 5`, `--only-tricky`, `--json-out FILE`.

### Part C — Mismatch calibration (judge notices wrong type)

**What it is:** Same Slack text and same **correct priority**, but we **deliberately set the wrong issue type** (Bug→Story, Story→Task, Task→Bug). The judge should usually give a **low type score** because the ticket type does not fit the message. If it still gives high type scores, the judge may be **too lenient** on bad type choices.

**Steps:**

1. Same env as Part B.  
2. Run:  
   `python run_judge_calibration.py --mode mismatch`  
3. Read the table: **CATCH** means type score ≤ `--mismatch-max-type` (default 3) — we treat that as “the judge noticed the bad type.” **MISS** means the score stayed high anyway.  
4. Read the **catch rate** — how often the judge penalised the wrong type.  
5. Optional: run **both** passes in one go:  
   `python run_judge_calibration.py --mode both`  
   You get gold results first, then a divider, then mismatch results. JSON with `--json-out` contains `gold` and `mismatch` keys when mode is `both`.

**Why do gold and mismatch?** Gold checks “does the judge **like** the right answer?” Mismatch checks “does the judge **dislike** an obviously wrong type?” Together they give a simple sanity check before you trust judge scores on real runs.

### Part D — After you trust the judge (real bot runs)

When `ENABLE_LLM_JUDGE=true` in `config/.env`, each **`python run_triage.py`** run can append judge scores to `memory/judge_store.json` for **real** tickets the bot created (Slack snippet + fields from that run — not by re-reading the log file from disk). That is separate from fixture calibration; fixtures are for **practice and measurement**, live judge is for **ongoing signal**.
