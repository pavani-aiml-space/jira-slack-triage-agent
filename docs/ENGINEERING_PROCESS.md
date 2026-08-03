# Engineering Process — JiraSlack

This doc covers how this project was built, not what it does. For the plain-English overview, architecture, and setup instructions, see the main [README](../README.md).

---

## My approach

I didn't start by opening an editor. Before any code existed, I worked through this in order:

1. **Wrote down what I actually wanted this to do** — the goal, the scope, and how it would create value — before deciding on any implementation. This became the customer-problem framing in `docs/plans/*-brainstorm.md`: who the actors are, what "done" looks like, and what's explicitly out of scope.
2. **Broke it into tasks.** The roadmap isn't one big build — it's phases (core pipeline → failure transparency → observability → duplicate detection → eval/feedback → memory → provider abstraction), each with its own milestone and its own definition of done.
3. **Wrote the golden dataset and evals before writing the feature.** `tests/eval/label_fixtures.json` and the classification playbook in `tests/eval/FIXTURES_GUIDE.md` define what "correct" means — Bug vs. Story vs. Task, High vs. Medium vs. Low — *before* any classification code was scored against it. Judge calibration (gold + mismatch runs) came before the judge was trusted for anything.
4. **Used a structured process instead of vibecoding.** Every feature went through the same gate: `/brainstorm` (what/why) → `/design` (how) → `/plan` (exact files, tests, order) → `/build` (red/green/refactor/commit) → `/audit` (tests pass + behavior verified) → `/kaizen` (cleanup, debt logged) → `/closeout` (docs + history written). Hard rule: no `/design` without an approved brainstorm, no `/build` without an approved plan, no `/closeout` without a passing audit. Slower per feature, close to zero rework.

---

## What I learned building this

- **Writing the eval before the feature forces you to define "correct" up front.** The tricky-case table in `FIXTURES_GUIDE.md` (a missing safety guard is a Bug, not a Story; a wrong doc is a Task, not a Bug) only exists because I had to write down the rule *before* I had code to rationalize around.
- **Mock only at real process boundaries.** I broke four tests by mocking a pure in-memory list-append (`add_episode`) — the mock made a threshold check silently pass because the state it depended on never actually mutated. Disk, network, and subprocess calls are mock targets. Plain Python state mutation is not.
- **Explicit state beats a side channel, even when the side channel looks simpler.** Passing memory into the agent as an explicit `MemoryContext` object (rather than a module-level dict another module writes into) cost one extra parameter and paid for itself immediately — every test could construct it directly instead of patching hidden global state.
- **The moment a function starts managing "before" and "after" a core step, split it out.** Eval logic (pre-run reaction collection, post-run judge scoring) started inside the main run loop. Pulling it into its own `eval_runner.py` turned a fragile order-of-operations test into a ten-line one.
- **A running "learnings" log compounds.** Every session ends with three questions answered in `docs/LEARNINGS.md`: what broke and why, what took longer than expected, what I'd tell myself at the start. Reading that file at the start of the *next* brainstorm caught at least two repeat mistakes before they happened again.

---

## Development process (the part that isn't the agent)

This repo was built using a portable 7-step SDLC — `/brainstorm → /design → /plan → /build → /audit → /kaizen → /closeout` — defined in `.agent/workflows/` and referenced from `CLAUDE.md`. It's project-agnostic: copying the `.agent/workflows/` folder and filling in the "Workflow Contracts" section of `CLAUDE.md` (test runner, key modules, mocking conventions) is enough to reuse it on a different codebase.
