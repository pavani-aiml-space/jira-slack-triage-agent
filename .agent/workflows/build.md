---
description: Execute an approved implementation plan using TDD — RED, GREEN, REFACTOR, COMMIT per chunk.
---

# /build

> You are answering: **Execute the plan, one chunk at a time, with a failing test first.**
> Input: approved implementation plan.
> Output: working, tested, committed code.

Use this workflow to execute a plan from `docs/plans/`.

---

## Prerequisites
- Plan must exist: `docs/plans/YYYY-MM-DD-<feature>-plan.md` ✅ approved

---

## Core Principles
- **No production code without a failing test.** The Iron Law.
- **One chunk at a time.** Complete, verify, and commit before moving on.
- **Never assume it works.** Run the test. Read the output.
- **Unit tests only here.** Integration and e2e run in /audit — not per chunk.
- **Follow conventions.** Read `CLAUDE.md → Coding Conventions` before writing any code.

---

## Test Layers — What Runs Here vs /audit

| Layer | Runs in /build? | Runs in /audit? |
|---|---|---|
| Unit (mocked, fast) | ✅ Every chunk | ✅ Part 1 |
| Integration | ❌ Never | ✅ Part 2 |
| E2E (real services) | ❌ Never | ✅ Part 3 only |

---

## The Process

### Step 1 — Setup
- Read the plan: `docs/plans/YYYY-MM-DD-<feature>-plan.md`
- Read `CLAUDE.md → Workflow Contracts` for test commands, conventions, commit format
- Identify the first unchecked chunk and start there

---

### Step 2 — Execute Each Chunk

For every chunk, follow this exact sequence:

**RED** — Write the failing test as specified in the plan
```
Run: [unit test command from CLAUDE.md → Workflow Contracts → Unit tests]
Expect: FAILED — [error from the plan]
```
If the test passes before you write any code → the test is wrong. Fix it.

**GREEN** — Write the minimal code to make the test pass
```
Run: [same test command]
Expect: PASSED
```
Write the minimum needed. Do not add anything not required by the test.

**REFACTOR** — Clean up while keeping tests green
- Remove duplication
- Improve naming clarity
- Add docstring if the function's intent is non-obvious
- Follow all conventions in `CLAUDE.md → Coding Conventions`
```
Run: [same test command]
Expect: still PASSED
```

**COMMIT** — Using the format from `CLAUDE.md → Commit Message Format`
```
git add .
git commit -m "[Type] scope: description"
```

**Check the box** in the plan file for this chunk.

---

### Step 3 — After Every Block
Run the full unit test suite (not just the chunk test) to catch regressions:
```
Run: [full test suite command from CLAUDE.md → Workflow Contracts → Full suite]
Expect: all passing
```

---

### Step 4 — Debug Protocol
If a chunk fails more than once with the same error — **stop**:

1. Create `docs/debug-log-<feature>.md`:
   ```
   | Attempt | What was tried | Why it failed |
   ```
2. Read the log before every new attempt
3. Delete the log once resolved

Do not keep trying random variations. The log forces systematic thinking.

---

### Step 5 — Mock Correctly
When writing unit tests, mock everything listed under `CLAUDE.md → External Dependencies`.
Nothing in a unit test should cross a process boundary (no real HTTP, no real MCP, no real LLM calls).

---

## Output
- All chunks checked off in the plan file
- All unit tests passing
- `PROJECT_HISTORY.md` updated with what was built this session

## Next Step
Once all chunks complete → use `/audit` to run integration tests and e2e verification.
