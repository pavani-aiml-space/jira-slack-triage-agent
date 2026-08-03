# Code Diagram: Phase 3 — Observability

> Generated from: [Technical Design](../plans/2026-04-29-phase3-observability-design.md)
> Last updated: 2026-04-29
> Status: draft

---

## ASCII Overview

```
  TRIGGER                   ENTRY POINT              AGENT CORE
  ─────────────────         ─────────────────────    ──────────────────────────────────────────
  ┌─────────────┐           ┌──────────────────┐     ┌──────────────────────────────────────┐
  │ dashboard.py│──Popen──▶│  run_triage.py   │────▶│        triage_agent.py               │
  │  [NEW]      │           │  [MOD]           │     │  [MOD]                               │
  │             │           │                  │     │  run()                               │
  │  load_run_  │           │  create sentinel │     │    ├─ fetch_messages()               │
  │  logs()     │◀──JSON────│  asyncio.run()   │     │    ├─ build_context_blocks()         │
  │             │           │  delete sentinel │     │    ├─ _run_llm_loop() → BlockResult  │
  └─────────────┘           └──────────────────┘     │    ├─ _print_block_outcome()         │
       │                                              │    ├─ write_run_log()                │
       │polls                                         │    ├─ _post_slack_summary()          │
       ▼                                              │    └─ _print_run_summary()           │
  ┌─────────────┐                                     └──────────────┬───────────────────────┘
  │ logs/       │                                                    │
  │ .running    │◀─────────────────────────── create/delete ────────┘
  │ run_*.json  │◀─────────────────────────── write_run_log() ──────┐
  └─────────────┘                                                    │
                                                                     │
  SERVICES                  EXTERNAL                                 │
  ─────────────────         ─────────────────────                    │
  ┌─────────────────┐       ┌─────────────────┐                      │
  │ run_logger.py   │◀──────│ triage_agent.py │──────────────────────┘
  │  [NEW]          │       │                 │
  │  write_run_log()│       │  _execute_tool()│──▶ jira_tools.py ──▶ Jira MCP
  │  load_run_logs()│       │                 │──▶ slack_tools.py ──▶ Slack MCP
  └─────────────────┘       └─────────────────┘
```

**Legend:** `[NEW]` = added this phase · `[MOD]` = modified this phase · no marker = unchanged

---

## Sequence Diagram

```mermaid
sequenceDiagram
    participant U as Operator
    participant D as dashboard.py 🆕
    participant RT as run_triage.py ✏️
    participant TA as triage_agent.py ✏️
    participant RL as run_logger.py 🆕
    participant SM as Slack MCP
    participant OA as OpenAI API
    participant JM as Jira MCP
    participant FS as filesystem (logs/)

    %% ── TRIGGER ──────────────────────────────────────────────────────
    U->>D: click "Run Agent"
    D->>RT: subprocess.Popen(["python", "run_triage.py"])
    Note over D: polls logs/.running every 2s<br/>shows ⏳ while sentinel exists

    %% ── STARTUP ──────────────────────────────────────────────────────
    RT->>FS: create logs/.running  (sentinel)
    RT->>TA: asyncio.run(run())
    TA->>RL: RunLog(run_id, started_at, status="success")

    %% ── FETCH & GROUP ────────────────────────────────────────────────
    TA->>SM: fetch_messages(channel_id)
    SM-->>TA: list[{user, text, ts}]  (N messages)
    TA->>TA: build_context_blocks(messages)
    Note over TA: returns M blocks<br/>each with combined_text, start_ts, end_ts

    %% ── BLOCK LOOP (happy path — one block shown) ───────────────────
    loop for each block[i]
        TA->>TA: _run_llm_loop(block_text, block_index=i, block_snippet)
        TA->>OA: chat.completions.create(model, tools, messages)
        OA-->>TA: choice{finish_reason:"tool_calls", tool_calls:[...]}
        Note over TA: accumulates: iterations,<br/>tools_called, prompt_tokens

        alt create_jira_ticket called
            TA->>JM: jira_mcp_session → call_tool("jira_create_issue", args)
            JM-->>TA: {key:"SCRUM-11", ...}
            TA->>OA: chat.completions.create(... + tool result)
            OA-->>TA: choice{finish_reason:"stop"}
            TA-->>TA: return BlockResult(action="ticket_created", ticket_key="SCRUM-11", llm=LlmStats)

        else ask_for_clarification called
            TA->>SM: post_message(clarification_text)
            SM-->>TA: ok
            TA->>OA: chat.completions.create(... + tool result)
            OA-->>TA: choice{finish_reason:"stop"}
            TA-->>TA: return BlockResult(action="clarification_asked", llm=LlmStats)
        end

        TA->>RL: run_log.blocks.append(BlockResult)
        TA->>TA: _print_block_outcome(result, i, total)
        Note over TA: stdout: [Block i/M] ✅ Ticket created: SCRUM-11 ...
    end

    %% ── ERROR PATHS ──────────────────────────────────────────────────
    alt openai.APIError raised (Rule 6)
        TA->>RL: run_log.status = "fatal"
        TA->>RL: run_log.completed_at = now()
        RL->>FS: write logs/run_<run_id>.json  (status:"fatal")
        TA->>SM: post_slack_message("⚠️ OpenAI unavailable…")
        TA->>TA: sys.exit(1)
        Note over RT: finally block runs even on sys.exit
        RT->>FS: delete logs/.running
    else Exception per block (Rule 5)
        TA->>RL: run_log.errors.append(ErrorEntry)
        TA->>RL: run_log.blocks.append(BlockResult(action="error"))
        TA->>TA: slack_errors.append(snippet + error)
        Note over TA: continue to next block
    end

    %% ── END-OF-RUN ───────────────────────────────────────────────────
    TA->>TA: _compute_status(run_log)
    Note over TA: "success" if error_count==0<br/>"partial" if error_count>0

    alt slack_errors accumulated (Rule 5)
        TA->>SM: post consolidated error summary
    end

    TA->>RL: run_log.completed_at = now()
    RL->>FS: write logs/run_<run_id>.json  (complete)

    alt status != "fatal"
        TA->>SM: _post_slack_summary → "✅ Run complete [ts] — N tickets, M errors"
    end

    TA->>TA: _print_run_summary(run_log)
    Note over TA: stdout: === Run Summary === block

    %% ── CLEANUP ──────────────────────────────────────────────────────
    RT->>FS: delete logs/.running  (finally block)

    %% ── DASHBOARD REFRESH ────────────────────────────────────────────
    D->>FS: poll — logs/.running gone?
    FS-->>D: file not found
    D->>FS: load_run_logs("logs/")
    FS-->>D: list[dict] sorted newest-first
    D->>U: re-render run history table with new row
```

---

## Flowchart — `run()` Decision Logic

```mermaid
flowchart TD
    A([run_triage.py: asyncio.run]) --> B[create RunLog\nrun_id = now]
    B --> C[fetch_messages via Slack MCP]
    C --> D[build_context_blocks]
    D --> E{blocks empty?}
    E -- yes --> Z[write log, print summary, done]
    E -- no --> F[for each block i]

    F --> G[_run_llm_loop\nblock_text, index, snippet]
    G --> H{exception?}

    H -- openai.APIError --> I[Rule 6:\nset status=fatal\nwrite log NOW\npost Slack alert]
    I --> J[sys.exit 1]
    J --> K[finally: delete sentinel]

    H -- Exception --> L[Rule 5:\nappend ErrorEntry\nappend BlockResult action=error\naccumulate slack_errors\ncontinue]
    L --> F

    H -- no error --> M[append BlockResult\nprint block outcome line]
    M --> N{more blocks?}
    N -- yes --> F
    N -- no --> O{slack_errors?}

    O -- yes --> P[post consolidated\nSlack error summary\nRule 5]
    O -- no --> Q
    P --> Q[_compute_status\nsuccess or partial]

    Q --> R[write_run_log to disk]
    R --> S{status == fatal?}
    S -- no --> T[_post_slack_summary\nUS3.3]
    S -- yes --> U
    T --> U[_print_run_summary stdout]
    U --> V[finally: delete sentinel]
    V --> W([done])
```

---

## Data Flow Summary

| Data | Source | Destination | Shape |
|------|--------|-------------|-------|
| Slack messages | Slack MCP | `triage_agent.run()` | `list[{user, text, ts}]` |
| Conversation blocks | `build_context_blocks()` | `_run_llm_loop()` | `list[{combined_text, start_ts, end_ts, messages}]` |
| OpenAI response | OpenAI API | `_run_llm_loop()` | `ChatCompletion{choices, usage}` |
| `BlockResult` | `_run_llm_loop()` | `run()` → `run_log.blocks` | `BlockResult{action, ticket_key, llm: LlmStats}` |
| `RunLog` | `run()` | `write_run_log()` → `logs/run_*.json` | `RunLog` dataclass → JSON |
| Slack summary | `_post_slack_summary()` | Slack MCP | `str` — `"✅ Run complete [ts] — N tickets…"` |
| Run history | `load_run_logs()` | `dashboard.py` | `list[dict]` sorted newest-first |
| Subprocess trigger | `dashboard.py` | `run_triage.py` | `subprocess.Popen(["python", "run_triage.py"])` |
| Sentinel file | `run_triage.py` | `dashboard.py` (poll) + `logs/` | `logs/.running` — exists while running |

**Stored / mutated:**
- `logs/run_<run_id>.json` — one file per run, written once, never mutated
- `logs/.running` — created at run start, deleted in `finally` block

**Not stored:**
- Raw Slack message text (Rule 8 — privacy)
- OpenAI API key or tokens (config only)

---

## Flow Plain-English

- **Trigger** (`dashboard.py → subprocess.Popen`)
  - **Purpose:** Operator clicks "Run Agent" and the triage pipeline starts as a background process
  - **Input:** Button click in Streamlit UI
  - **Output:** `run_triage.py` process starts; `logs/.running` sentinel appears; dashboard shows ⏳

- **Sentinel create** (`run_triage.py → open(SENTINEL_FILE)`)
  - **Purpose:** Mark that an agent run is in progress so the dashboard knows not to show stale state
  - **Input:** Run starting
  - **Output:** `logs/.running` file created on disk

- **Fetch & group** (`triage_agent.run() → fetch_messages + build_context_blocks`)
  - **Purpose:** Read recent Slack messages and group related ones into conversation blocks
  - **Input:** Slack channel ID from settings
  - **Output:** List of blocks, each containing the combined text of 1+ related messages

- **LLM loop** (`triage_agent._run_llm_loop(block_text, block_index, block_snippet) → BlockResult`)
  - **Purpose:** Send one conversation block to GPT-4o, let it call tools until done, and return a structured result
  - **Input:** Block text string, block index, 60-char snippet
  - **Output:** `BlockResult` — action taken (`ticket_created` / `clarification_asked` / `error`), ticket key/type/priority if created, LLM stats (iterations, tokens, tools called)

- **Block outcome print** (`triage_agent._print_block_outcome(result, i, total)`)
  - **Purpose:** Give the operator a live per-block summary line in their terminal
  - **Input:** `BlockResult`, block index, total block count
  - **Output:** Stdout line: `[Block 1/4] ✅ Ticket created : SCRUM-11 "..." (Bug · High)`

- **Rule 6 fatal handler** (`triage_agent.run() except openai.APIError`)
  - **Purpose:** When OpenAI is unreachable, write a fatal log entry before exiting so the dashboard shows a record
  - **Input:** `openai.APIError` exception
  - **Output:** `status:"fatal"` log written to disk; Slack alert posted; process exits 1

- **Rule 5 per-block error handler** (`triage_agent.run() except Exception`)
  - **Purpose:** When a Slack MCP or other transient error hits one block, record it and continue to the next block
  - **Input:** Any non-OpenAI exception during block processing
  - **Output:** `ErrorEntry` appended to `run_log.errors`; `action:"error"` `BlockResult` appended; error string accumulated for consolidated Slack post

- **Log write** (`run_logger.write_run_log(run_log, log_dir)`)
  - **Purpose:** Persist the full run record to disk so the dashboard and Phase 6 can read it
  - **Input:** Completed `RunLog` dataclass
  - **Output:** `logs/run_<run_id>.json` file — valid JSON with funnel counts, per-block trace, LLM stats, errors

- **Slack end-of-run summary** (`triage_agent._post_slack_summary(run_log)`)
  - **Purpose:** Post a brief summary to the Slack channel so the team sees what happened without opening the dashboard
  - **Input:** `RunLog` (status, counts)
  - **Output:** Slack message: `"✅ Run complete [ts] — N tickets, M clarifications, P errors"`; suppressed if `status == "fatal"`

- **Stdout run summary** (`triage_agent._print_run_summary(run_log)`)
  - **Purpose:** Replace the bare "Done" line with a structured human-readable summary in the terminal
  - **Input:** `RunLog`
  - **Output:** `=== Run Summary ===` block with counts, status, log path

- **Sentinel delete** (`run_triage.py → finally → os.remove(SENTINEL_FILE)`)
  - **Purpose:** Signal to the dashboard that the run is complete so it stops polling and refreshes
  - **Input:** `finally` block always runs — even on `sys.exit(1)`
  - **Output:** `logs/.running` deleted; dashboard sees file gone, calls `load_run_logs()`, re-renders table

- **Dashboard refresh** (`dashboard.py → load_run_logs()`)
  - **Purpose:** Show the operator the latest run result without requiring a manual browser refresh
  - **Input:** `logs/` directory contents
  - **Output:** Updated run history table with new row; ⏳ indicator cleared
