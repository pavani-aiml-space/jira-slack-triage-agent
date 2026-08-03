---
description: Quality gate — unit tests, integration tests, then e2e in strict sequence. All three must pass before closeout.
---

# /audit

> You are answering: **Did we build it correctly, completely, and safely?**
> Input: completed /build with all chunks checked off.
> Output: PASS or FAIL with all issues documented.

Use this workflow after `/build` completes.

---

## The Non-Negotiable Rule

```
Unit tests → Integration tests → E2E
     ↓              ↓               ↓
  must pass      must pass      must pass
```

**E2E only runs if unit AND integration are both fully green.**
If either fails, stop, fix, and re-run from the failed layer.

---

## Part 1 — Unit Tests

Run the unit test suite:
```
[unit test command from CLAUDE.md → Workflow Contracts → Unit tests]
```

**Pass criteria:** All tests pass, 0 failures, 0 errors.

If any fail:
- Audit is **FAILED**
- Fix the failing test or the code
- Re-run Part 1 before moving to Part 2
- Do NOT proceed to integration or e2e with red unit tests

Also check:
- [ ] Every new function has at least one unit test
- [ ] All external dependencies are mocked (reference `CLAUDE.md → External Dependencies`)
- [ ] No test makes a real HTTP call, real MCP call, or real LLM call

---

## Part 2 — Integration Tests

Run the integration test suite:
```
[integration test command from CLAUDE.md → Workflow Contracts → Integration tests]
```

**Pass criteria:** All tests pass, 0 failures, 0 errors.

If any fail:
- Audit is **FAILED**
- Fix and re-run Part 2
- Do NOT proceed to e2e

---

## Part 3 — End-to-End (ONLY after Parts 1 and 2 are green)

Run the project entry point:
```
[entry point from CLAUDE.md → Workflow Contracts → Entry point]
```

Verify every scenario in `CLAUDE.md → Workflow Contracts → E2E Verification Checklist`:

| Scenario | Expected | Actual | Pass? |
|----------|----------|--------|-------|
| [copy each row from CLAUDE.md E2E checklist] | | | |

Also verify the success metrics from the brainstorm doc:
```
docs/plans/YYYY-MM-DD-<feature>-brainstorm.md → Success Metrics
```

**All rows must be Pass before the audit is complete.**

If any row fails:
- Fix the code
- Re-run unit tests (Part 1) to confirm nothing regressed
- Then re-run e2e

---

## Part 4 — Draft Learnings (While Context Is Fresh)

Before the code review, draft this session's entries for `docs/LEARNINGS.md`.
Answer these questions now, while everything is still in your head:

1. **What broke and why?** → `[GOTCHA]`
2. **What took longer than expected?** → `[PROCESS]`
3. **Was any design decision harder than expected?** → `[DECISION]`
4. **What would you tell yourself at the start of this session?**

Don't write the final entry yet — just capture the raw notes. You'll write the formatted entry in `/closeout`.

---

## Part 5 — Code Review (Fresh Eyes)

Read every file changed in this feature as if seeing it for the first time. Ask:

1. Does any function do more than one job? (Single Responsibility)
2. Is any logic duplicated that should be shared? (DRY)
3. Is anything built that isn't needed yet? (YAGNI)
4. Does every external call have a failure mode that matches the Priority Rules in `CLAUDE.md`?
5. Does every new component follow the conventions in `CLAUDE.md → Coding Conventions`?
6. Will the next developer understand this without asking?

Triage findings:

| Bucket | Definition | Action |
|---|---|---|
| **Blocking** | Violates conventions, DRY, SRP, or a Priority Rule | Fix before closeout |
| **Improvement** | Valid, non-urgent | Log to `docs/BUGS.md` |
| **Nitpick** | Style preference | Acknowledge, skip |

Fix all Blocking issues. Re-run unit tests after any fix.

---

## Audit Result

- ✅ **PASS**: Parts 1-3 green, no blocking code issues, success metrics verified
- ❌ **FAIL**: Fix blocking issues, re-run the failed layer, repeat

Document the result in `PROJECT_HISTORY.md`.

---

## Output
- All blocking issues resolved
- Improvements logged to `docs/BUGS.md`
- Audit result recorded in `PROJECT_HISTORY.md`

## Next Step
Once audit passes → use `/kaizen` then `/closeout`.
