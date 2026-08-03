---
description: System design — translate the approved brainstorm into a technical design doc. Covers architecture, data contracts, and failure modes. Does NOT produce the diagram — that is /diagram.
---

# /design

> Amazon framing: This is your **Technical Design Document (TDD)** session.
> You are answering: **How will we build it?**
> Input: approved brainstorm doc.
> Output: technical design doc only. The diagram comes next in `/diagram`.

Use this workflow after `/brainstorm` is approved and before `/diagram`.

---

## Prerequisites
- Brainstorm doc must exist: `docs/plans/YYYY-MM-DD-<feature>-brainstorm.md`
- Brainstorm must be explicitly approved by the user

---

## Rules
- **No code yet.** Design first, code later.
- **Reuse before building.** Search the codebase before proposing anything new.
- **Design for failure.** Every external call must have a documented failure mode.
- **One diagram per feature.** The diagram is the source of truth for /plan and /build.

---

## The Process

### Step 1 — Read All Inputs
Read in this order:
1. The approved brainstorm doc — understand the problem and success metrics
2. `CLAUDE.md` — understand the existing architecture, **Key Modules**, **External Dependencies**, **Coding Conventions**, and **Priority Rules**
3. `docs/diagrams/system-overview.md` — understand what already exists
4. `docs/LEARNINGS.md` — scan for any Gotchas relevant to the components this feature will touch
5. The key modules listed under `CLAUDE.md → Key Modules` — understand what can be reused

Summarise in 3 bullets before proceeding:
1. What are we building and what does success look like?
2. Which existing modules are relevant to this feature?
3. What external dependencies will this feature interact with?

---

### Step 2 — Reuse Audit
Before designing anything new, search the codebase against the **Key Modules** in `CLAUDE.md`:

- Does any existing module already do part of this?
- Can an existing interface be extended vs creating a new one?
- Is there an established pattern (listed under `CLAUDE.md → Coding Conventions`) that this should follow?

State explicitly for each component:
- "Reusing `X` from `Y` as-is"
- "Extending `X` in `Y` with new capability"
- "New component needed — no existing equivalent found"

---

### Step 3 — Propose Approaches
Present **2-3 technical approaches** with trade-offs:

| Approach | How it works | Pros | Cons |
|----------|-------------|------|------|
| Option A | ... | ... | ... |
| Option B | ... | ... | ... |

**Lead with a recommendation** and explain why it best fits the existing architecture.

Search for existing libraries before proposing custom code. Present a **Build vs Borrow** table if a library could help.

Verify each approach against the **Priority Rules** in `CLAUDE.md`. Flag if any approach would violate a rule.

Get explicit user approval on the approach before continuing.

---

### Step 4 — Design the Solution

Define each component clearly:

**New files to create:**
- File path and purpose

**Existing files to modify:**
- File path and what changes

**Data contracts:**
- Function signatures with arg names, types, and return types
- Shape of data passed between components

**External API interactions** (reference `CLAUDE.md → External Dependencies`):
- Which external service is called
- What is sent (payload shape)
- What is returned (response shape)
- Auth method

**Failure modes** — for every external call:
- What happens on timeout?
- What happens on auth failure?
- What happens on unexpected response?
- Verify each failure mode against the **Priority Rules** in `CLAUDE.md`

### Step 5 — Write the Technical Design Doc
Save to:
```
docs/plans/YYYY-MM-DD-<feature>-design.md
```

Structure:
```
# Technical Design: <feature>

## Problem (from brainstorm)
[One sentence linking back to the customer problem]

## Approach Chosen
[Which option and why — including which Priority Rules it satisfies]

## Components

### New Files
- path/to/file — purpose

### Modified Files
- path/to/file — what changes

## Data Contracts
[Function signatures with types]

## External Calls
[Service, endpoint, payload, response, auth]

## Failure Modes
[What happens when each external call fails — verified against Priority Rules]

## Out of Scope
[At least 2 explicit exclusions]

## Open Questions Resolved
[Questions from brainstorm and their answers]
```

---

### Step 6 — Human Approval Gate
Present the design doc summary and ask:
> "Does the technical design look correct? Ready to move to `/diagram`?"

Do NOT proceed until explicit approval on the design doc.
The diagram is produced separately in `/diagram` — do not start it here.

---

## Output
- Technical design doc: `docs/plans/YYYY-MM-DD-<feature>-design.md`
- `PROJECT_HISTORY.md` updated

## Next Step
Once approved → use `/diagram` to produce the code-level system diagram.
