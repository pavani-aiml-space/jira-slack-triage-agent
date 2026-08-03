---
description: Break an approved technical design into an executable, TDD-first implementation plan — blocks, chunks, and test-first tasks.
---

# /plan

> Amazon framing: This is your **sprint planning** session.
> You are answering: **What exactly will we build, in what order, verified how?**
> Input: approved brainstorm doc + design doc + system diagram.
> Output: implementation plan with TDD chunks ready to execute.

Use this workflow after `/diagram` is approved and before `/build`.

---

## Prerequisites
- Brainstorm doc: `docs/plans/YYYY-MM-DD-<feature>-brainstorm.md` ✅ approved
- Design doc: `docs/plans/YYYY-MM-DD-<feature>-design.md` ✅ approved
- Feature diagram: `docs/diagrams/YYYY-MM-DD-<feature>.md` ✅ approved

---

## Core Principles
- **TDD-First:** Every chunk starts with a failing test. No exceptions.
- **Bite-sized:** Each chunk should take 2-5 minutes to implement.
- **Zero ambiguity:** Use exact file paths. Specify create vs modify.
- **Reuse first:** Check the Key Modules in `CLAUDE.md` before creating anything new.
- **Unit tests only in /build:** Integration and e2e tests are for /audit, not per-chunk.

---

## Terminology
- **Block:** A logical grouping of related chunks (e.g. "Data Layer", "Core Logic", "Integration")
- **Chunk:** One bite-sized unit of work = one RED→GREEN→REFACTOR→COMMIT cycle

---

## Test Layers — Defined Here, Enforced in /audit

| Layer | What it tests | Mocking | File location | When it runs |
|---|---|---|---|---|
| **Unit** | One function in isolation | All external calls mocked | `tests/unit/` | Every chunk in /build |
| **Integration** | Components working together | Boundaries mocked | `tests/integration/` | /audit Part 2 |
| **E2E** | Full pipeline end-to-end | None — real services | Manual | /audit Part 3 only |

Each chunk in this plan must declare its test layer. **E2E is never a chunk.**

---

## Process

### Step 1 — Read All Three Input Docs
1. `docs/plans/YYYY-MM-DD-<feature>-brainstorm.md` — success metrics to satisfy
2. `docs/plans/YYYY-MM-DD-<feature>-design.md` — technical approach and data contracts
3. `docs/diagrams/YYYY-MM-DD-<feature>.md` — exact call chain and components

Also read from `CLAUDE.md`:
- **Key Modules** — which files are involved
- **External Dependencies** — what to mock in unit tests
- **Coding Conventions** — patterns every chunk must follow
- **Commit Message Format** — exact format for Step 5 of each chunk

Summarise in 3 sentences:
1. What the feature does and what success looks like
2. Which components are new vs modified vs unchanged
3. What the biggest technical risk is

---

### Step 2 — Reuse Audit
Before writing the plan, check `CLAUDE.md → Key Modules`:
- Is there an existing function that already does part of this?
- Can an existing pattern be followed vs inventing something new?

State explicitly: "Reusing X" or "No existing equivalent found"

---

### Step 3 — Conventions Check
Read `CLAUDE.md → Coding Conventions` and confirm the plan respects them.
Flag any chunk that would require deviating from conventions and explain why.

---

### Step 4 — Write the Plan

Save to: `docs/plans/YYYY-MM-DD-<feature>-plan.md`

#### Plan Structure

**Header**
- Goal: one sentence
- Architecture: 2-3 sentences on the approach
- Files affected: all files to create or modify (reference the design doc)

**Blocks and Chunks**

For each chunk:
```
Chunk N.M — <name>
Test layer: UNIT | INTEGRATION
            (e2e never appears here — it runs once in /audit Part 3)
Files:
  Create: path/to/new_file
  Modify: path/to/existing_file
Test file: tests/unit/test_<module>.py  OR  tests/integration/test_<feature>.py

Step 1 (RED)    — Write this failing test:
  [minimal test code that will fail because the function doesn't exist yet]
  Run: [test command from CLAUDE.md → Workflow Contracts → Unit tests]
  Expect: FAILED — [exact error message]

Step 2 (GREEN)  — Write this minimal implementation:
  [smallest code that makes the test pass — nothing more]
  Run: [same test command]
  Expect: PASSED

Step 3 (REFACTOR) — Clean up:
  [what to improve — naming, duplication, docstring]
  Run: [same test command]
  Expect: still PASSED

Step 4 (COMMIT):
  [exact commit command using format from CLAUDE.md → Commit Message Format]
```

**Success Criteria** (map directly to brainstorm metrics)
- [ ] [metric from brainstorm] — verified by [how]
- [ ] All unit tests pass
- [ ] All integration tests pass
- [ ] E2E checklist passes (verified in /audit)

**Known Technical Debt**
List any shortcuts and why they're acceptable now.

---

### Step 5 — Human Approval Gate
Present the plan and ask:
> "Ready to build? Use `/build` to execute this plan."

Do NOT start building until explicit approval.

---

## Output
- Plan saved to `docs/plans/YYYY-MM-DD-<feature>-plan.md`

## Next Step
Once approved → use `/build` to execute it chunk by chunk.
