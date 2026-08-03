# Feature: Phase 7 — Agent Memory (Episodic + Semantic + Working)

**Date:** 2026-04-29
**Phase:** 7 (advanced to before Phase 6 — Reliability — by user decision)

---

## Actors

| Actor | Role | What they need | Priority |
|---|---|---|---|
| **A — Operator** | Runs the agent; monitors dashboard | Agent improves over time without manual prompt editing; consistent, explainable decisions | Primary |
| **B — Reporting engineer** | Posts bugs/requests in Slack | Messages handled correctly; familiar classes of issue handled without re-asking the same questions | Primary |
| **C — Developer** | Builds and maintains the agent | Memory layers are testable, inspectable, and cannot silently corrupt behaviour | Secondary |

## Priority Rule

When operator explainability and developer testability conflict, **testability wins** — a memory layer that cannot be unit-tested or inspected is a liability. Every memory read and write must be independently testable with mocked I/O.

---

## Customer Problem

**Operator (A):** Every run starts from zero. The agent has no recollection of past decisions. If the same class of issue appears in a new form, it re-derives the same answer from scratch — sometimes inconsistently. There is no way to verify the agent is improving without manually auditing Jira.

**Reporting engineer (B):** They have reported similar issues before. They expect the agent to recognise the pattern — right type, right priority, right label — without needing to repeat context or answer clarifying questions for well-understood issue classes.

**Cost of not building this:** Agent accuracy is permanently capped by the static system prompt. Improvement requires manual prompt editing by a developer — expensive, rare, and untestable without a labeled dataset.

---

## What We're Building

A three-layer persistent memory system that logs every triage decision (episodic), extracts recurring patterns (semantic), and injects relevant context at run time (working) — making the agent measurably more consistent and accurate over time without manual prompt changes.

---

## Memory Architecture (Decided This Session)

| Layer | Storage | What It Stores | Injection Point |
|---|---|---|---|
| **Episodic** | `memory/episode_store.json` | Every `ticket_created` decision: `{run_id, block_index, slack_text_hash, ticket_key, ticket_type, ticket_priority, ticket_summary, run_ts}` | User message per block (top-K similar episodes) |
| **Semantic** | `memory/semantic_store.json` | Extracted patterns: count-based first (`(type, priority, keyword) × ≥5`) + LLM-summarised richer facts | SYSTEM_PROMPT (once per run, all active patterns) |
| **Working** | Runtime only | Retrieved episode examples + injected semantic patterns assembled per-block | Semantic → SYSTEM_PROMPT; Episodes → user message |

---

## Out of Scope

- Phase 6 watermark / last-processed Slack timestamp (separate file, separate phase)
- Confidence threshold auto-tuning from memory (Phase 5b)
- Memory-based routing to different prompt templates per ticket type
- Memory pruning / archival (beyond a simple `MAX_EPISODES` cap in settings)
- UI for inspecting memory contents (future phase — `python run_triage.py --show-memory`)
- Vector-database-backed retrieval (overkill until episode count justifies it; cosine similarity on JSON is sufficient)

---

## Success Metrics

| Metric | Target | How Measured | Actor |
|---|---|---|---|
| Episodic log completeness | 100% of `ticket_created` BlockResults produce an episode entry | Unit test: every ticket_created run writes episode | Developer |
| Semantic pattern emergence | ≥ 1 pattern emitted after 5 identical `(type, keyword)` decisions | Unit test: 5 synthetic episodes → `extract_patterns()` returns ≥1 pattern | Developer |
| LLM-pattern summarisation | Patterns are summarised and stored after extraction trigger | Unit test: mock OpenAI call; verify `semantic_store.json` updated | Developer |
| Prompt injection — semantic | Semantic patterns appear in SYSTEM_PROMPT context at run time | Unit test: mock store; verify injected text in assembled prompt | Developer |
| Prompt injection — episodic | Relevant episodes appear in user message per block | Unit test: mock store + similarity; verify user message contains episode examples | Developer |
| No regressions | 175/175 tests pass; no new duplicate tickets | Full test suite + manual E2E | Developer |
| Agent consistency (manual) | Same class of message triaged to same type/priority on 3 consecutive runs once 5+ episodes exist | Manual E2E spot check | Operator |

---

## Risks & Open Questions

1. **Episode store growth** — JSON with no cap will grow unboundedly. `MAX_EPISODES` setting needed (rolling window or oldest-first eviction). Format must support it.

2. **Semantic extraction timing** — synchronous (during the run) vs. async (background). Synchronous is simpler but adds latency on large episode logs. Decision: synchronous for now; if extraction exceeds 2s, wrap in `asyncio.to_thread`.

3. **LLM call for semantic summarisation may fail** — needs its own Rule 5 handler. Count-based patterns must still work if LLM summarisation is unavailable.

4. **Duplicate detection overlap** — embedding cache (`ticket_embeddings.json`) detects same-ticket duplicates; episode memory detects same-class-of-issue. Embedding gate runs first (faster, no LLM call). Episode injection adds context but does not replace the embedding gate.

5. **Working memory injection size** — injecting too many episodes or too long a semantic summary will bloat the context window and increase cost. Need a `MAX_INJECTED_EPISODES` (default: 3) and a `MAX_SEMANTIC_PATTERN_CHARS` cap.

6. **Phase 6 forward compatibility** — `episode_store.json` must be designed so Phase 6 can add `last_processed_ts` to a separate `memory/run_state.json` without migration. No shared file between phases.

7. **Retrieval method** — episodes are retrieved by cosine similarity of the current block's text vs. stored `slack_text_hash` (reuse `text-embedding-3-small`). This requires embedding the current block before retrieval — same embed call as the duplicate gate. Opportunity to batch.

---

## New Priority Rules (feature-specific)

**Rule 10 — Semantic extraction LLM call fails**
Fall back silently to count-based patterns only. Never block or alert on pattern extraction failure. The agent continues with whatever semantic context is already in the store (Rule 5 applies).

**Rule 11 — Episode retrieval returns no matches**
Continue with no episodic injection. The agent runs with semantic patterns only (or with a clean context if the store is empty). Never block or alert on empty retrieval.

---

## Decisions Made This Session

| Decision | What Was Chosen | Why |
|---|---|---|
| Storage format | JSON (not SQLite) | Simpler, fully decoupled from Phase 6; consistent with `quality_store.json` pattern already in use |
| Semantic extraction | Count-based first pass + LLM summarisation | Count-based is free and fast; LLM adds richer, human-readable patterns once signal exists |
| Working memory injection | Hybrid — semantic patterns → SYSTEM_PROMPT (run-level); episodes → user message (block-level) | System prompt is the right place for stable, run-wide facts; user message is the right place for dynamic, per-block precedents |
| Phase 6 dependency | Fully decoupled — no shared file | Skip Phase 6 without any memory migration cost; Phase 6 adds `memory/run_state.json` independently |
| Phase ordering | Memory before Reliability (Phase 6) | User explicit decision; episodic memory delivers operator value immediately; watermark can wait |
