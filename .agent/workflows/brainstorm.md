---
description: Strategic discovery — align on the customer problem, vision, goals, and success metrics before any design or code decisions are made.
---

# /brainstorm

>  This is your **PR/FAQ + Working Backwards** session.
> You are answering: **What are we building and why?**
> You are NOT answering: How will we build it? (That is /design.)

Use this workflow before starting any new feature, capability, or significant change.

---

## Rules
- **One question at a time.** Never ask more than one thing at once.
- **Customer first.** Every decision traces back to a customer or operator problem.
- **No implementation details here.** No file names, no code, no architecture. That comes in /design.
- **Don't re-litigate what's already decided.** Read CLAUDE.md and the roadmap first.

---

## The Process

### Step 1 — Read Context First
Before asking anything, read:
- `CLAUDE.md` — understand what's already built, the conventions, and the **Priority Rules**
- `PROJECT_ROADMAP.md` — understand the Vision, Goals, Capabilities, and which phase/epic/user story we're working on
- `PROJECT_HISTORY.md` — understand past decisions so you don't repeat them
- `docs/LEARNINGS.md` — scan for any Gotchas relevant to the feature area you're about to work on

Summarise in 3 bullets:
1. What phase and user story are we working on?
2. What does the roadmap and CLAUDE.md already say about this? (Don't re-ask these things.)
3. What is genuinely undecided and needs to be explored?

---

### Step 2 — Define the Who

**First:** Read the `Priority Rules` section in `CLAUDE.md`. These are already-decided project-wide tiebreakers. Do not re-ask questions that these rules already answer. Only surface conflicts that are genuinely NEW and not covered by an existing rule.

Before discussing the problem, get clear on every person involved. Ask:

**Primary customer** (who has the pain this feature relieves):
- What is their role?
- What are they doing when they encounter this problem?
- What do they care most about — speed? accuracy? reliability?

**Secondary actors** (everyone else who touches this flow):
- Who triggers the feature?
- Who receives the output?
- Who is affected if the system gets it wrong?
- Who is responsible for running and maintaining this?

**Priority rule** — when two actors' needs conflict, whose wins?

Document as a **Who Table**:
```
| Actor | Role | What they need | Priority |
|-------|------|----------------|----------|
| ...   | ...  | ...            | Primary  |
| ...   | ...  | ...            | Secondary|
```

Do not move to the next step until every actor is named and their need is stated.

---

### Step 3 — Working Backwards: Customer Problem
Now that we know who, nail down their specific pain:

- **What is the problem today?** What pain are they experiencing without this feature?
- **What does success look like for them?** What can they do after this that they couldn't before?
- **What is the cost of NOT building this?** What keeps breaking or stays manual?
- **Is this the real problem or a symptom?** Ask "why" at least once to check.

---

### Step 4 — Scope Alignment
Establish clear boundaries:

- **What are we building?** (one crisp sentence)
- **What are we explicitly NOT building?** (list at least 2 things out of scope)
- **What are the must-haves vs nice-to-haves?**
- **What assumptions are we making?**

---

### Step 5 — Success Metrics
Define how we'll know this worked. Every metric must be measurable:

| Metric | Target | How Measured | Whose need it satisfies |
|--------|--------|--------------|------------------------|

Do not proceed without at least 2 measurable success criteria tied to specific actors.

---

### Step 6 — Risks & Open Questions
Before closing brainstorm:

- What could go wrong with this approach?
- What do we not know yet that could change the design?
- Are there dependencies on other systems or people?

List these as open questions. They will be answered in /design.

---

### Step 7 — Write the Brainstorm Output Doc
Save to:
```
docs/plans/YYYY-MM-DD-<feature>-brainstorm.md
```

Structure:
```
# Feature: <name>

## Actors
| Actor | Role | What they need | Priority |

## Priority Rule
When actor needs conflict: [whose need wins and why]

## Customer Problem
[The primary actor's pain, in their words]

## What We're Building
[One sentence]

## Out of Scope
- [item]
- [item]

## Success Metrics
| Metric | Target | How Measured | Whose need it satisfies |

## Risks & Open Questions
- [question]

## New Priority Rules (feature-specific only)
[Conflicts NOT covered by CLAUDE.md Priority Rules and how they were resolved]
[If none: "All conflicts covered by project-wide Priority Rules in CLAUDE.md"]

## Decisions Made This Session
[What was agreed and why]
```

---

### Step 8 — Human Approval Gate
Present the brainstorm doc and ask:
> "Does this capture the right problem and success criteria? Ready to move to `/design`?"

Do NOT proceed until explicit approval.

---

## Output
- Brainstorm doc: `docs/plans/YYYY-MM-DD-<feature>-brainstorm.md`
- `PROJECT_HISTORY.md` updated with session entry

## Next Step
Once approved → use `/design` to produce the system design and code-level diagrams.
