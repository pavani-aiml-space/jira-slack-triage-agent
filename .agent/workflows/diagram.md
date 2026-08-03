---
description: Code-level system diagram — translate the approved technical design into an exact diagram showing files, functions, call chains, and data flow. Must be completed before /plan.
---

# /diagram

> Amazon framing: This is your **Architecture Visualization** session.
> You are answering: **Exactly how does the code flow — from entry point to external call and back?**
> Input: approved technical design doc (`docs/plans/YYYY-MM-DD-<feature>-design.md`).
> Output: code-level diagram linked from the design doc.

Use this workflow after `/design` is approved and before `/plan`.

---

## Prerequisites
- Technical design doc must exist and be explicitly approved: `docs/plans/YYYY-MM-DD-<feature>-design.md`
- Do NOT create the diagram if the design is still open for debate

---

## Rules
- **No invented components.** Every box in the diagram must correspond to a real or planned file in the codebase.
- **Trace the actual code.** Walk existing source files to confirm real function names, signatures, and call chains — do not guess.
- **Link back to design.** The diagram file must reference the design doc, and the design doc must be updated to link to the diagram.
- **Update the master diagram.** Every feature diagram must be reflected in `docs/diagrams/system-overview.md`.

---

## The Process

### Step 1 — Read All Inputs
Read in this order:
1. The approved design doc — understand the chosen approach, components, and data contracts
2. `CLAUDE.md` — understand the entry point, key modules, and external dependencies
3. `docs/diagrams/system-overview.md` — understand the existing system boundaries
4. The actual source files listed in the design doc — confirm real function names and call chains

Summarise before proceeding:
- Entry point (exact command or function call)
- Key call chain (module → function → module → function)
- External services touched

---

### Step 2 — Build the Feature Diagram

Create a diagram file at:
```
docs/diagrams/YYYY-MM-DD-<feature>.md
```

**The diagram MUST include all of the following:**

#### A. ASCII Overview (always first)
A plain-text box diagram showing the top-level components and their relationships.
Render this first — it should be readable in any terminal, email, or PR comment without any rendering engine.

Format:
```
┌────────────────┐     ┌─────────────────┐     ┌──────────────┐
│  run_triage.py │────▶│ triage_agent.py │────▶│ jira_tools.py│
│  (entry point) │     │  run()          │     │  create_jira │
└────────────────┘     └────────┬────────┘     └──────────────┘
                                │
                         ┌──────▼──────┐
                         │ slack_tools │
                         │ post_reply  │
                         └─────────────┘
```

Rules for the ASCII diagram:
- Use box-drawing characters: `┌ ─ ┐ │ └ ┘ ├ ┤ ┬ ┴ ┼ ▶`
- Each box = one real file or external service
- Arrows show call direction, not data direction
- Label each arrow with the function name being called
- Mark `[NEW]` or `[MOD]` inside boxes that are being added or changed
- Keep it to one screen width (max ~80 chars wide)

#### B. Sequence Diagram (primary detail)
Shows the full request/response call chain using `sequenceDiagram`.

Required elements:
- Entry point — exact command (from `CLAUDE.md → Workflow Contracts → Entry point`) and first function
- Every module → function → next module hop with real names
- Data shape passed at each hop (type, key fields)
- Every external API call with: protocol, endpoint, auth, payload shape, response shape
- All decision branches with the exact condition (`if confidence < threshold`, etc.)
- Error/failure paths, not just the happy path

Mark components:
- `🆕` new file or function being added
- `✏️` existing file being modified
- no marker for unchanged

#### C. Flowchart (for decision-heavy logic)
If the feature has branching logic (priority rules, retry, fallback), add a `flowchart TD` showing:
- Each decision point with its condition
- The outcome of each branch

#### D. Data Flow Summary
A short table or bullet list showing:
- What data enters the system (source, shape)
- What data exits the system (destination, shape)
- What is stored/mutated internally (file or service, schema)

#### E. Flow Plain-English
A bullet list — one bullet per named flow — written so anyone can read it without knowing the code.

Format for each bullet:
```
- **<flow name>** (`file.py → function()`)
  - **Purpose:** one sentence
  - **Input:** what goes in (type + key fields)
  - **Output:** what comes out (type + key fields)
```

Rules:
- Name every flow that appears in the sequence diagram
- Keep each bullet self-contained — no assumed knowledge
- Use plain language, not code syntax, in the Purpose line
- This section must appear after Data Flow Summary and before the end of the file

---

### Step 3 — Add a Header Block to the Diagram File

Every diagram file must start with:

```markdown
# Code Diagram: <feature>

> Generated from: [Technical Design](../plans/YYYY-MM-DD-<feature>-design.md)
> Last updated: YYYY-MM-DD
> Status: draft | approved

## ASCII Overview
[ASCII box diagram here — readable without any rendering engine]

## Sequence Diagram
[Mermaid sequenceDiagram here]

## Flowchart (if applicable)
[Mermaid flowchart TD here]

## Data Flow Summary
[Table or bullets here]

## Flow Plain-English
[One bullet per named flow — What runs, Purpose, Input, Output]
```

---

### Step 4 — Link the Diagram into the Design Doc

Update `docs/plans/YYYY-MM-DD-<feature>-design.md` and add a section:

```markdown
## Code Diagram
See: [docs/diagrams/YYYY-MM-DD-<feature>.md](../diagrams/YYYY-MM-DD-<feature>.md)
```

Place this section immediately after **Components**, before **Data Contracts**.

---

### Step 5 — Update the Master Diagram

Update `docs/diagrams/system-overview.md`:
- Add or update the section for this feature
- Reflect any new modules, external calls, or changed data paths introduced by this feature

---

### Step 6 — Human Approval Gate

Present the diagram and ask:
> "Does this accurately show the code flow for this feature? Any missing components, wrong function names, or data shapes that look off?"

Do NOT proceed to `/plan` until the diagram is explicitly approved.

---

## Output
- Feature diagram: `docs/diagrams/YYYY-MM-DD-<feature>.md`
- Design doc updated with link: `docs/plans/YYYY-MM-DD-<feature>-design.md`
- Master diagram updated: `docs/diagrams/system-overview.md`
- `PROJECT_HISTORY.md` updated with diagram created + approved

## Next Step
Once approved → use `/plan` to break the design and diagram into executable work items with tests.
