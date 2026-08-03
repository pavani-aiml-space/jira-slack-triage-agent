---
description: Wrap up a development session — update all documentation, verify tests, commit everything, update the roadmap.
---

# /closeout

> You are answering: **Is this session complete, documented, and safe to hand off?**
> Input: passing /audit + /kaizen.
> Output: committed, documented, roadmap updated.

Use this workflow to end a development session cleanly.

---

## Prerequisites — All Must Be True Before Starting

- [ ] `/audit` Part 1 passed — unit tests fully green
- [ ] `/audit` Part 2 passed — integration tests fully green
- [ ] `/audit` Part 3 passed — e2e checklist verified against real services
- [ ] `/kaizen` completed — codebase clean, debt logged
- [ ] `docs/BUGS.md` has no unresolved Blocking items for this feature

**If e2e has not been run and confirmed — you cannot closeout. No exceptions.**

---

## The Process

### Step 1 — Final Test Run
Before touching any documentation, confirm tests are still green after kaizen:
```
[full suite command from CLAUDE.md → Workflow Contracts → Full suite]
```
Must be fully green. If not, fix and re-run before continuing.

---

### Step 2 — Write Learnings to docs/LEARNINGS.md

> **First time on a new project?** If `docs/LEARNINGS.md` doesn't exist yet, create it using this bootstrap template:
>
> ```markdown
> # Learnings & Takeaways — <Project Name>
>
> > Newest entries at the top.
> > Written during /closeout after every session.
> > Read at the start of every /brainstorm and /design session.
> > Purpose: prevent the same mistake twice and improve the process over time.
>
> ---
>
> ## How to Use This File
>
> **When reading:** Before starting /brainstorm or /design, scan the Gotchas
> section relevant to your feature area.
>
> **When writing:** At /closeout, answer these three questions:
> 1. What broke and why? (Gotcha)
> 2. What took longer than expected and why? (Process learning)
> 3. What would you tell yourself at the start of this session? (Takeaway)
>
> ---
>
> ## Categories
>
> - **[GOTCHA]** — A specific technical trap. Includes symptom, root cause, fix, rule.
> - **[PROCESS]** — A workflow or SDLC improvement discovered during the session.
> - **[DECISION]** — A design decision that was harder than expected, with reasoning.
> ```
>
> Then add your first session entry below the header.

---

Now prepend a new session entry at the top of `docs/LEARNINGS.md`:
Before updating any other doc, capture what was learned this session.
Prepend a new session entry at the top (below the header):

```
## Session: YYYY-MM-DD — <feature name>

### [GOTCHA] <title>
Symptom: [what you observed]
Root cause: [why it happened]
Fix: [what resolved it]
Rule going forward: [one sentence to remember]

### [PROCESS] <title>
What happened: [what the workflow friction was]
Impact: [how it slowed things down]
Fix applied: [what was changed]
Rule going forward: [one sentence]

### [DECISION] <title>
What happened: [what was harder than expected]
Why: [the competing options]
Decision: [what was chosen and why]
```

Write at least one entry per session. If nothing broke and nothing was hard, write one [PROCESS] entry about what went smoothly and why.

---

### Step 3 — Update PROJECT_HISTORY.md
Prepend a new dated entry at the top of the Session Log table:

```
| YYYY-MM-DD | [What was built — be specific] | [Key decisions and why] | [What's next] |
```

Include:
- What shipped (specific feature, function names if relevant)
- Key decisions made this session and why
- Any lessons learned or gotchas discovered

---

### Step 3 — Update PROJECT_ROADMAP.md
- Mark the completed user story / epic with ✅
- Check off all completed items under it
- Confirm the next user story is clearly listed as the next priority

---

### Step 4 — Update CLAUDE.md (if anything changed)
If any of these changed, update `CLAUDE.md → Workflow Contracts`:
- New files added → update **Key Modules**
- New external service added → update **External Dependencies**
- New environment variable → update the env vars table
- New convention established → update **Coding Conventions**
- New project-wide Priority Rule → update **Priority Rules**

---

### Step 5 — Commit Everything
```
[commit format from CLAUDE.md → Commit Message Format → Closeout]
```

Stage all documentation changes:
```
git add .
git commit -m "[Closeout] <feature-name>: session wrap-up"
```

---

### Step 6 — Closing Summary
Provide a concise summary (5-8 bullets max):
- What shipped
- What was deferred (and where it's tracked in `docs/BUGS.md`)
- Test count before vs after
- Next session should start with: `/brainstorm` for [next user story from roadmap]

---

## Output
- `PROJECT_HISTORY.md` updated
- `PROJECT_ROADMAP.md` updated
- `CLAUDE.md` updated (if needed)
- `docs/BUGS.md` clean of Blocking items
- All changes committed
- Session is fully closed — next session can start by reading `CLAUDE.md` and `PROJECT_ROADMAP.md`
