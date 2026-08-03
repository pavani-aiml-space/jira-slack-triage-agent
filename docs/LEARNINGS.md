# Learnings & Takeaways — JiraSlack

> Newest entries at the top.
> Written during /closeout after every session.
> Read at the start of every /brainstorm and /design session.
> Purpose: prevent the same mistake twice and improve the process over time.

---

## Session: 2026-04-30 — Eval fixtures & LLM judge calibration

### [PROCESS] Fixtures, gold judge, mismatch judge — single playbook

**Where:** `tests/eval/FIXTURES_GUIDE.md` (steps **Part A–D**).

**Summary:** (1) Add rows to `label_fixtures.json` with human-correct labels. (2) Run **gold** calibration (`python run_judge_calibration.py`) so the judge scores tickets that **match** those labels — high scores mean alignment. (3) Run **mismatch** (`--mode mismatch`) so the judge scores tickets with a **deliberately wrong** issue type — low type scores mean the judge is discriminating. (4) Use `--mode both` or `--json-out` when you want both reports saved. (5) Live triage can append judge scores when `ENABLE_LLM_JUDGE=true` — that path is documented in the same guide (Part D).

**Rule going forward:** Treat fixture labels as ground truth; treat the judge as a second instrument you validate with gold + mismatch before using it for alerts or dashboards.

---

## How to Use This File

**When reading:** Before starting /brainstorm or /design, scan the Gotchas section relevant to your feature area. If you're touching Jira auth, read the Jira gotchas. If you're touching Slack MCP, read those.

**When writing:** At /closeout, answer these three questions:
1. What broke and why? (Gotcha)
2. What took longer than expected and why? (Process learning)
3. What would you tell yourself at the start of this session? (Takeaway)

---

## Categories

- **[GOTCHA]** — A specific technical trap that caused a bug or wasted time. Includes the symptom, root cause, and fix.
- **[PROCESS]** — A workflow or SDLC improvement discovered during the session.
- **[DECISION]** — A design decision that was harder than expected, with the reasoning for what was chosen.

---

## Session: 2026-04-29 — Phase 8: Model-Agnostic LLM Provider

### [GOTCHA] Removing a module-level variable before updating its patches causes `AttributeError` at test collection

**Symptom:** After removing `_client = OpenAI(...)` from `triage_agent.py`, running pytest immediately produced `AttributeError: <module 'agents.triage.triage_agent'> does not have the attribute '_client'` — not a test failure, a collection error that blocked all other tests in the file.

**Root cause:** `patch("agents.triage.triage_agent._client")` does a strict attribute lookup at the `with patch(...)` call site. If the attribute no longer exists at that moment, patch raises before the test body runs.

**Fix:** Update production code and every test that patches the old name in the same chunk, not across separate chunks. The plan already bundled Chunk 3.1 (remove `_client`) and Chunk 4.1 (update test mocks) closely together for exactly this reason.

**Rule going forward:** When renaming or removing a module-level variable that tests patch directly, update all `patch()` call sites in the same commit as the removal. Never leave a session with a deleted attribute that existing patches reference.

---

### [PROCESS] Write test helpers before migrating 19 tests — mechanical beats creative

**What happened:** Phase 8 required migrating 19 existing `test_triage_agent.py` tests from the old `_client` mock pattern to the new `_provider` + `LLMTurn` pattern. Each test needed a different combination of `LLMTurn` fields.

**Impact:** Without `make_llm_turn()` and `make_tool_call_turn()` helpers, each migration would have required constructing a full `LLMTurn(finish_reason=..., content=..., tool_calls=..., prompt_tokens=..., completion_tokens=..., raw_message=...)` inline — verbose and error-prone.

**Fix applied:** Added both helpers at the top of the test file before starting the migration. The helpers give each test a clean one-liner for the common case, with inline construction only when a test specifically needs non-default values (e.g. `raw_message=MagicMock()` for multi-turn capture tests).

**Rule going forward:** When migrating tests to a new mock pattern, write the helpers first. The migration becomes mechanical — search and replace with intent — rather than creative writing for every test.

---

### [DECISION] `raw_message: Any` — store the SDK object, not a re-serialised dict

**What happened:** `LLMTurn` needs to carry the LLM's response back to `_run_llm_loop` so it can be appended to the `messages` list for the next multi-turn iteration. The question was: should `raw_message` hold the provider-specific SDK object (e.g. OpenAI's `ChatCompletionMessage`) or a re-serialised dict?

**Why it was hard:** The OpenAI SDK `ChatCompletionMessage` object is not JSON-serialisable and contains `tool_calls` as SDK objects. Re-serialising to a dict would require knowing the exact fields each provider expects — defeating the point of the abstraction layer.

**Decision:** Store the SDK object (`raw_message: Any`). Each provider's `chat()` method stores its own message format in `raw_message`. `_run_llm_loop` appends it to `messages` unchanged. This means the messages list stays in the format the provider expects — no cross-provider translation needed. The `Any` type is intentional: it signals "provider-owned, do not inspect here."

**Rule going forward:** When a provider-neutral interface needs to preserve provider-specific state for multi-turn continuity, `raw_message: Any` is the correct pattern. Do not attempt to normalise it into a shared format — that couples all providers to a single schema.

---

## Session: 2026-04-29 — Memory Quality: Why We Chose This Approach

### [DECISION] Five memory quality principles and why each matters

When building agent memory, the natural instinct is: save everything, inject everything. We chose not to, for five specific reasons. Each principle and the rationale for it:

**1. Scope what gets saved — quality gate via 👍/👎**
Saving every decision means the agent learns from its own mistakes, which compounds errors over time. We store episodes as 'unvalidated' first and promote them to 'validated' only when the team reacts with a 👍 to the Slack confirmation (captured in Phase 5 eval). Only validated episodes are returned by `search_memory`. This connects the feedback loop to the learning loop — the agent gets smarter from right decisions only.

**2. Source where facts came from — provenance**
Every episode already stores the Jira ticket key, timestamp, confidence score, and the first 60 characters of the original Slack message. We chose not to store abstract summaries — instead, the LLM sees "SCRUM-42 — login crash on empty password — Bug, High (2026-04-28)" so it can reason about recency and origin, not just pattern matches.

**3. Age out stale context — TTL setting**
A count-based eviction cap (oldest 200 kept) isn't enough — a 6-month-old decision about a module that no longer exists can still be returned. We added `MAX_EPISODE_AGE_DAYS` (default 90). `pre_run()` filters episodes outside the window before retrieval. This is better than a count cap because it's time-aware, not volume-aware. Old patterns fade out in a quarter without manual intervention.

**4. Review before high-stakes actions — forced recall for Critical tickets**
`search_memory` is lazy by design — the LLM calls it only when uncertain. But for Critical priority tickets the cost of getting it wrong is high, so we add one mandatory system prompt instruction: "Before creating a Critical priority ticket, always call `search_memory` first." This is the minimal intervention that covers the high-stakes case without taxing every routine decision with unnecessary retrieval.

**5. Retire what no longer matters — TTL + FIFO together**
We don't attempt automatic semantic retirement ("this pattern is now obsolete because of a product change") because that requires the agent to understand the product roadmap — too complex to automate reliably. Instead, TTL (90 days) and FIFO cap (200 episodes) handle it passively. For immediate manual cleanup, `memory/episode_store.json` is plain JSON. The two passive mechanisms together mean stale patterns are gone within a quarter with zero maintenance.

**Why not just hardcode rules in the system prompt instead?**
The system prompt is static — it doesn't adapt to the team's actual patterns. Semantic memory is built from real decisions and injected at runtime, so the prompt stays general but the context is team-specific. If patterns change, memory updates automatically. A hardcoded prompt requires manual editing every time.

---

## Session: 2026-04-30 — Phase 7: Agent Memory (Episodic + Semantic + Working)

### [GOTCHA] Patching a pure list-append function breaks downstream state-dependent assertions
Symptom: Four tests in `test_memory_runner.py` that verified the semantic extraction threshold failed with "Expected mock called once, called 0 times." — even though the surrounding test logic looked correct.
Root cause: The tests patched `add_episode` as a `MagicMock` (no-op). Since the episode was never actually appended to `ep_store.episodes`, `len(ep_store.episodes)` remained at its pre-add value, making the threshold delta (`total - last_extracted`) always below 5. The threshold check silently passed "not triggered" — no error was raised.
Fix: Removed the `patch("pipeline.memory_runner.add_episode")` from those four tests and let the real `add_episode` run. It's a pure list mutation — no I/O, no API calls — so there's nothing to mock.
Rule going forward: Only mock functions that cross a process boundary (disk I/O, network, subprocess). Pure in-memory mutations like list.append are not mock targets — patching them creates false isolation and breaks any downstream assertion that depends on the mutated state.

### [GOTCHA] Extending a private function's signature silently breaks test side_effect mocks
Symptom: `test_run_slack_error_continues_to_next_block` started reporting `call_count == 0` instead of 2. The error message was "Errors : 2", not a mock assertion error.
Root cause: `_run_llm_loop`'s signature was extended with `episode_context=""` and `effective_system_prompt=""`. A test that used `side_effect=llm_fail_first` where `llm_fail_first` had the old three-argument signature `(block_text, block_index=0, block_snippet="")` now received two unexpected kwargs. Python raised `TypeError: got unexpected keyword argument`. This TypeError was silently swallowed by `run()`'s `except Exception` handler — incrementing `error_count` instead of calling the side effect function.
Fix: Added `**kwargs` to the `llm_fail_first` function signature in the test.
Rule going forward: Any test mock function used as `side_effect=` must use `**kwargs` to absorb any kwargs the production code passes. When a production function signature is extended with optional kwargs, all `side_effect=` wrappers must be updated to match or use `**kwargs`.

### [DECISION] `MemoryContext` as explicit dataclass vs. module-level side-channel
What happened: Three architectural options for passing memory into `triage_agent.run()`: (A) explicit `MemoryContext` dataclass parameter, (B) module-level dict that memory_runner writes into, (C) both stores loaded directly inside `triage_agent.run()`.
Why: Option B looked simpler at first — no signature change to `run()`. But it would have made `triage_agent.py` implicitly depend on `memory_runner.py` state, and would have required mock-patching a module-level dict in tests instead of passing a clean test object.
Decision: Option A. Explicit is better than implicit. `MemoryContext` is a first-class object that can be inspected, mocked with a simple constructor call, and passed null (`None`) for backward compatibility. Every existing `run()` test required zero changes.

### [PROCESS] Threshold-based trigger tests need "setup state + 1 new" framing, not fully mocked state
What happened: Writing tests for "post_run triggers extraction when threshold met" required careful thought about *which* episode count feeds the threshold check.
Impact: First draft patched `add_episode` as a no-op and set `existing = 4 episodes` hoping `4 >= 5` would fail and `5 >= 5` would succeed. But since `add_episode` was patched, the count never reached 5.
Fix applied: Removed `add_episode` mock, let the test rely on the real append, and documented the framing as "4 existing episodes + 1 new episode from run_log = 5 total = threshold met."
Rule going forward: When writing a threshold-trigger test, think in terms of "loaded state + new state appended = total that triggers." Don't mock the append step — only mock the I/O boundaries.

---

## Session: 2026-04-29 — Phase 5: Eval & Feedback Loop

### [GOTCHA] Module-level buffer requires explicit `clear()` before each test, not just after
Symptom: A test that wrote to `_confirmation_ts_buffer` would pollute the next test in the same file if the buffer wasn't cleared at the start of the test.
Root cause: `_confirmation_ts_buffer` is a module-level `list[str]`. Unlike function-local state, it persists across test function calls within the same process. A test that fails mid-way before clearing it leaves stale state for the next test.
Fix: Every test that touches the buffer now calls `slack_tools._confirmation_ts_buffer.clear()` at the start (not just at the end). The `drain_confirmation_ts()` tests also explicitly `.clear()` before asserting empty.
Rule going forward: Any module-level mutable state used as a side-channel must be cleared at the *start* of every test that uses it — treat it like a test fixture setup, not teardown.

### [GOTCHA] `patch("run_triage.run_eval_step")` only works because of a direct module-level import
Symptom: Patching `run_triage.run_eval_step` works correctly; patching `pipeline.eval_runner.run_eval_step` would *also* fire the original for callers that imported the name directly.
Root cause: Python's `unittest.mock.patch` replaces the name in the *target module's namespace*, not the function object itself. `run_triage.py` uses `from pipeline.eval_runner import run_eval_step`, which binds the name `run_eval_step` in the `run_triage` namespace. Patching `run_triage.run_eval_step` replaces that binding. If `run_triage.py` had used `import pipeline.eval_runner` and then called `eval_runner.run_eval_step(...)`, the patch target would need to be `pipeline.eval_runner.run_eval_step` instead.
Fix: N/A — the import style in `run_triage.py` is already correct for patching.
Rule going forward: When writing a test that patches a function called from module A, always patch `A.function_name`, not `module.function_name`. The binding that matters is the one in the calling module.

### [PROCESS] Separating eval logic into `eval_runner.py` made the run_triage test trivial
What happened: The test for `run_triage.main()` calling eval hooks in the right order (`test_run_triage_main_calls_eval_runner_before_and_after`) required only two patches and ~10 lines to write.
Impact: If eval logic had stayed in `triage_agent.run()`, testing lifecycle order would have required inspecting call order inside a 175-line function with many side effects.
Fix applied: User prompted the architectural question "does it really belong in run_triage.py or a separate file?", leading to the creation of `pipeline/eval_runner.py` as the dedicated eval lifecycle owner.
Rule going forward: When a function starts managing more than one "phase" of work (e.g., pre-step + post-step around a core run), extract the surrounding orchestration into a dedicated module. The test complexity delta is the signal.

### [DECISION] Module-level `_confirmation_ts_buffer` side-channel vs. returning `ts` directly from `post_slack_message`
What happened: We needed to capture the Slack `message_ts` from inside `post_slack_message` (an LLM-callable tool) so that `triage_agent.run()` could attach it to the `BlockResult` after the LLM loop completed.
Why it was hard: Returning `ts` directly would require changing `post_slack_message`'s return type from `str` to `tuple[str, Optional[str]]` or `Optional[str]`, propagating the change through `_execute_tool`, `_run_llm_loop`, and updating all 12 tests for those functions.
Decision: Module-level `_confirmation_ts_buffer` side-channel. The tool contract stays `str → str` (stable). The buffer is cleared at the start of each block and drained after the LLM loop. The pattern is similar to Flask's `g` object — well understood, clearly documented, and isolated. The trade-off (invisible in the type signature) is documented with a comment in `slack_tools.py`.

### [GOTCHA] `SYSTEM_PROMPT` in `triage_agent.py` had stale code-fence artifacts from prompt iteration
Symptom: On every LLM call, GPT-4o received a system prompt prefixed with "Use this refined version:\n\n\`\`\`text\n" and suffixed with "\`\`\`\n\nMain fixes: clearer classifications...". These artifacts were burned as prompt tokens on every run.
Root cause: A past prompt refinement session edited the string in-place using markdown code fences, and the wrapper text was never stripped.
Fix: Stripped during kaizen — prompt now opens directly with "You are a software triage agent...".
Rule going forward: System prompts are strings, not markdown documents. Never leave code-fence wrappers or meta-commentary inside a string literal that gets sent to the LLM.

---



### [GOTCHA] Blocking `_client.chat.completions.create()` call on the async event loop
**Symptom:** Not a visible crash — the agent worked correctly. But `_run_llm_loop` was blocking the entire event loop on every OpenAI call, preventing any concurrent I/O from progressing.
**Root cause:** `asyncio` does not make synchronous functions async automatically. Calling the synchronous OpenAI SDK directly inside an `async def` blocks the event loop for the full round-trip latency. The `embed_texts` function had already been wrapped correctly with `asyncio.to_thread`; `_run_llm_loop` had not.
**Fix:** Changed `response = _client.chat.completions.create(...)` to `response = await asyncio.to_thread(_client.chat.completions.create, ...)`.
**Rule going forward:** Any synchronous I/O call inside an `async def` must be wrapped in `asyncio.to_thread`. This includes SDK clients that don't yet provide an async interface. Grep for `.create(`, `.get(`, `.post(` — if they're not awaited or threaded, they're blocking.

---

### [GOTCHA] `build_embedding_cache` silently mutated the caller's dict
**Symptom:** No crash, no wrong output. But the caller's `existing_cache` dict would gain new keys after calling `build_embedding_cache`, which could cause subtle bugs if the caller re-used the dict after the call.
**Root cause:** `existing_tickets = existing_cache.get("tickets", existing_cache)` returned a reference into the nested dict. Subsequent writes to `existing_tickets` mutated the original.
**Fix:** `existing_tickets = dict(existing_cache.get("tickets", existing_cache))` — shallow copy is sufficient since only top-level keys are added.
**Rule going forward:** Any function that receives a dict and is expected to return a new/updated version should copy at the point of extraction. The rule: if you didn't create it, don't mutate it.

---

### [PROCESS] Cache-only-grows pattern is a common accumulation bug
**What happened:** `build_embedding_cache` was adding new tickets to the cache on every run but never removing closed ones. After many runs, the cache would contain embeddings for tickets that no longer exist in Jira.
**Impact:** Closed tickets would still match as duplicates. Cache grows without bound.
**Fix applied:** Added `{k: v for k, v in existing_tickets.items() if k in open_keys}` before the new-ticket loop — pruning stale entries before adding new ones.
**Rule going forward:** Any time you write a cache-update function, ask: "what removes entries?" If the answer is "nothing", that's a bug waiting to happen. Prune first, add second.

---

### [PROCESS] Pagination should always return partial results on error, not empty
**What happened:** The original `fetch_open_tickets` had a single `try/except` that returned `[]` if anything failed — including mid-pagination failures. A network error on page 3 would discard pages 1 and 2.
**Fix applied:** Moved the `all_issues` accumulator outside the `try` block; the `except` still logs and returns, but now returns whatever was collected before the error.
**Rule going forward:** In any paginated fetch, accumulate results before the `try/except`. Never discard partial pages on error — partial results are better than no results.

---

## Session: 2026-04-29 — Phase 4: Duplicate Detection

### [GOTCHA] `jira_search` MCP returns flat issue format, not Jira REST API nested format
**Symptom:** `fetch_open_tickets` failed on every E2E run with `KeyError: 'fields'`. The duplicate gate was silently disabled (Rule 5 catch), so the agent ran but never checked for duplicates.
**Root cause:** Code assumed `issue["fields"]["summary"]` (Jira REST API v3 format), but the Atlassian MCP `jira_search` tool returns flat issues where `summary` and `status` are top-level keys on each issue dict.
**Fix:** Changed to `issue["summary"]` and `issue["status"]["name"]`. Updated unit test mock to match flat format.
**Rule going forward:** When writing parsers for MCP tool results, always run a live spike first to inspect the actual response shape. Never assume MCP tools wrap Jira REST format — they often flatten or reshape it.

---

### [GOTCHA] `patch_run_deps` extended with new patches but existing tests only consumed `[0:3]`
**Symptom:** After adding 6 Phase 4 patches to `patch_run_deps`, existing `run()` tests that entered only `patches[0], patches[1], patches[2]` still called real OpenAI (via `embed_texts` in the new `run()` code) because the Phase 4 mocks in `patches[3:]` were never activated. Tests took 15+ seconds and then exited with SystemExit.
**Root cause:** Extending `patch_run_deps` return value doesn't automatically apply the new patches — every call site must explicitly enter them.
**Fix:** Bulk-replaced all `with patches[0], patches[1], patches[2]:` to `with patches[0], ..., patches[8]:` across 9 tests.
**Rule going forward:** When adding patches to a shared helper, immediately grep for all call sites and update them. Or consider switching to a `contextlib.ExitStack`-based pattern that enters all patches implicitly.

---

### [PROCESS] All Block 3 `run()` changes implemented in one GREEN step
**What happened:** The plan separated `run()` integration into Chunks 3.1–3.4. Once Phase 4 imports were added, ALL 33 existing `run()` tests failed simultaneously because they didn't mock the new functions. Fixing them chunk-by-chunk would have left the suite in a broken intermediate state.
**Impact:** The TDD plan was slightly deviated from — all `run()` changes went GREEN together.
**Fix applied:** Implemented all four chunks in one GREEN step. Consistent with Phase 2 LEARNINGS.md entry ("When all changes land in the same function, implement the full set in one GREEN step").
**Rule going forward:** When a plan chunk touches a function that already has many tests, expect to update all of them at once. Plan for this in the chunk design — group all changes to the same function into one chunk.

---

### [GOTCHA] Embedding `openai.APIError` was incorrectly scoped under Rule 6 (fatal exit)
**Symptom:** If the OpenAI Embeddings API failed during the duplicate gate, the agent would exit the process (Rule 6 — fatal) instead of skipping the check and continuing (Rule 5 — skip+continue), as specified in the design doc.
**Root cause:** The `embed_texts` calls were inside the block loop's `try` block which has `except openai.APIError → sys.exit(1)`. All OpenAI errors in scope triggered Rule 6.
**Fix:** Wrapped both `embed_texts` calls (duplicate gate + cache update) in their own `try/except Exception` blocks that set `match = None` / log and continue, so they never reach the `openai.APIError` handler.
**Rule going forward:** When a block loop has a `catch openai.APIError → exit` handler, be explicit about which calls are allowed to raise it. Wrap non-fatal OpenAI calls (embeddings, optional enrichment) in their own inner `try/except` before they can surface as Rule 6.

---

### [GOTCHA] `openai.APIConnectionError` Cannot Be Instantiated Directly
**Symptom:** `openai.APIConnectionError("some message")` raises `TypeError` — constructor requires a `request` argument that isn't easy to construct in tests.
**Root cause:** `openai.APIConnectionError` inherits from `httpx.ConnectError`, whose `__init__` requires a live `httpx.Request` object.
**Fix:** Use `openai.APIConnectionError.__new__(openai.APIConnectionError)` to create a bare instance without triggering `__init__`. Works cleanly in `side_effect=` assignments.
**Rule going forward:** When mocking OpenAI error types in tests, always use `ClassName.__new__(ClassName)` — never call the constructor directly.

---

### [PROCESS] All Three Handlers Implemented in One GREEN Step
**What happened:** The plan called for Chunk 2.1 (OpenAI handler), 3.1 (per-block accumulator), and 3.2 (consolidated post) as separate RED→GREEN cycles. In practice, all three lived in `run()` and adding them one at a time would have required leaving `run()` in inconsistent intermediate states.
**Impact:** The TDD plan was slightly violated — multiple handlers went GREEN together.
**Fix applied:** Accepted the deviation; added a plan note explaining the reason. Single GREEN step kept the code consistent.
**Rule going forward:** When all changes land in the same function, implement the full set in one GREEN step and document why. Don't artificially split a single function's implementation across multiple commits.

---

### [DECISION] `try/except` at Point of Failure vs Decorator
**What happened:** The design considered a single error-handling decorator wrapping all tool calls. Rejected in favour of `try/except` directly at each failure site.
**Why:** Priority Rules 1, 5, and 6 require different responses — Jira failure posts Slack + continues; OpenAI failure posts Slack + exits; Slack MCP failure accumulates + continues. A single decorator cannot express all three behaviours without conditional logic that would be harder to read than the explicit `try/except` blocks.
**Decision:** Explicit `try/except` at each site. Each handler is self-contained, readable, and directly traceable to its Priority Rule.

---

## Session: 2026-04-27 — Test Infrastructure + Kaizen

### [GOTCHA] patch() doesn't work for dict-bound function references
**Symptom:** `patch("triage_agent.post_slack_message")` had no effect — the mock was never called even though the test looked correct.
**Root cause:** `TOOL_EXECUTORS = {"post_slack_message": post_slack_message}` binds the function reference at import time. Patching the module-level name replaces the name but not the dict entry.
**Fix:** Use `patch.dict(triage_agent_module.TOOL_EXECUTORS, {"post_slack_message": mock_fn})` to replace the dict value directly.
**Rule going forward:** When a function is stored in a dict or list at module load time, always use `patch.dict` — never `patch("module.name")`.

---

### [GOTCHA] pytest-asyncio must be explicitly installed — pytest does not bundle it
**Symptom:** All async tests were silently skipped with `PytestUnhandledCoroutineWarning`. `pytest --co` collected them but they never ran.
**Root cause:** `pytest-asyncio` is a separate package from `pytest`. Without it, `@pytest.mark.asyncio` is an unknown mark and async tests are skipped entirely.
**Fix:** `pip install pytest-asyncio`. Also ensure `pytest.ini` has `asyncio_mode = auto` to avoid decorating every test.
**Rule going forward:** After setting up a new Python test environment, always verify async tests actually RUN (not just collect) with a quick `pytest tests/unit/ -v` before trusting results.

---

### [PROCESS] Cursor skills: thin wrapper → workflow file is the right pattern
**What happened:** Cursor SDLC skills were initially written as large self-contained files. The user flagged this was not aligned with how other skills worked (e.g. `/brainstorm`).
**Impact:** Confusion about where the canonical content lived; skills hard to maintain.
**Fix applied:** Each skill is now a thin 12-line wrapper that reads and follows `.agent/workflows/<name>.md`. All real content lives in the workflow file.
**Rule going forward:** Skills describe *when* to invoke and *delegate immediately*. Workflow files contain *what* to do. Never put procedural content in a SKILL.md.

---

## Session: 2026-04-25 — Phase 1 Core Pipeline

### [GOTCHA] Atlassian Basic Auth — Email Must Exactly Match Token Owner
**Symptom:** Jira API returned `401 Unauthorized` despite a valid-looking API token.
**Root cause:** `JIRA_EMAIL` in `.env` was `pavaniaml75@gmail.com` — a typo — but the Atlassian account and token were under `pavaniaiml75@gmail.com`. The emails didn't match.
**Fix:** Corrected the email in `.env`. Auth worked immediately.
**Rule going forward:** When debugging a 401 against Atlassian, check the email first — before rotating the token. A mismatched email looks identical to an invalid token.

---

### [GOTCHA] Slack MCP Server Deprecation Warning
**Symptom:** `npm warn deprecated @modelcontextprotocol/server-slack@2025.4.25: Package no longer supported.`
**Root cause:** The Slack MCP package is deprecated. It still works but may stop being maintained.
**Watch for:** If Slack MCP starts failing, evaluate replacing with direct Slack API calls using `slack_sdk`.
**Not urgent:** Functional for now. Logged in `docs/BUGS.md` as DEBT-004 when it becomes relevant.

---

### [PROCESS] Tests Were Written as Manual Scripts, Not Pytest Tests
**What happened:** All 5 test files in `tests/` were `if __name__ == "__main__": asyncio.run(main())` scripts — not pytest tests. Running `pytest` collected 0 tests.
**Impact:** No automated safety net. Any code change could break things silently.
**Fix applied:** Defined three test layers (unit / integration / e2e) in the SDLC workflows. Created `tests/unit/` and `tests/integration/` directories. Going forward, every /build chunk must produce a pytest-compatible unit test.
**Rule going forward:** Before writing any test, ask: can `pytest` discover and run this automatically? If not, it's a script, not a test.

---

### [PROCESS] SDLC Was Applied Retroactively
**What happened:** The full pipeline was built before any SDLC structure existed — no brainstorm doc, no design doc, no plan, no diagrams.
**Impact:** No record of why key decisions were made (e.g. why GPT-4o tool-calling vs classifier.py, why 5-minute time window, why Jira REST vs Jira MCP).
**Fix applied:** Applied SDLC structure retroactively — created CLAUDE.md, PROJECT_HISTORY.md, PROJECT_ROADMAP.md, workflow files, diagrams.
**Rule going forward:** Start every new feature with /brainstorm, even if it feels like overkill. The docs are faster to write upfront than to reconstruct later.

---

### [DECISION] Two Classification Approaches Exist But Only One Is Used
**What happened:** `classifier.py` exists as a standalone classification module (returns structured JSON). `triage_agent.py` implements its own inline classification via GPT-4o tool-calling. These are two different approaches to the same problem.
**Why it happened:** `classifier.py` was built first as a proof of concept. `triage_agent.py` was built later using the tool-calling pattern which is more powerful.
**Current state:** `classifier.py` is unused by the main pipeline. Logged as DEBT-001 in `docs/BUGS.md`.
**Decision:** Keep both for now. Consolidate in Phase 2 as part of the intelligence workstream.
