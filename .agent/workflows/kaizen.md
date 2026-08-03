---
description: Continuous improvement — remove entropy, reduce technical debt, improve clarity before closing out.
---

# /kaizen

> You are answering: **Is the codebase cleaner than when we started?**
> Input: passing /audit.
> Output: cleaner code, logged debt, green tests.

Use this workflow after `/audit` passes and before `/closeout`.

---

## What Kaizen Is
Small, targeted improvements that make the codebase easier to work with next session. Not a refactor. Not a rewrite. Just removing the mess that accumulates during /build.

---

## What to Look For

Review all files changed in this feature. Check for:

**Dead code**
- Functions defined but never called
- Imports that aren't used
- Config values set but never read

**Duplication**
- Logic that appears in two places and should be a shared utility
- Copy-pasted patterns that could be a helper function

**Clarity**
- Variable or function names that don't reveal intent
- Inconsistent naming with the rest of the codebase (check `CLAUDE.md → Coding Conventions`)
- Non-obvious functions with no docstring

**Test gaps**
- New code paths with no test coverage
- Tests that test implementation details instead of behaviour

**Convention drift**
- Any file that doesn't follow the patterns in `CLAUDE.md → Coding Conventions`

---

## Process

1. Run: `git diff --name-only HEAD~N` to see all files changed in this feature
2. Review each file against the checklist above
3. Fix only what is clearly better — don't refactor working code for the sake of it
4. After each fix, run the full unit test suite:
   ```
   [full suite command from CLAUDE.md → Workflow Contracts → Full suite]
   ```
5. Commit improvements separately from feature work:
   ```
   [commit format from CLAUDE.md → Commit Message Format → Kaizen]
   ```

---

## What NOT to Do
- Don't change working behaviour — kaizen is about clarity, not functionality
- Don't refactor things that aren't confusing to someone new
- Don't add abstractions for a single use case
- Don't gold-plate — leave the "nice to have" for a future feature

---

## Debt That's Out of Scope
If you find something real but too large to fix now:
- Log it to `docs/BUGS.md` as an Improvement item
- Include: what the problem is, which file and line, why it matters

---

## Output
- Codebase is cleaner than when /build started
- Unit tests still fully green
- Debt that's out of scope logged to `docs/BUGS.md`

## Next Step
Once done → use `/closeout`.
