# Code Diagram: Phase 2 — Failure Transparency

> Generated from: [Technical Design](../plans/2026-04-29-phase2-failure-transparency-design.md)
> Last updated: 2026-04-29
> Status: draft

---

## ASCII Overview

Only two existing files are modified. No new files.

```
python run_triage.py
        │
        ▼
┌────────────────────────────────────────────────────┐
│  triage_agent.py  run()                      [MOD] │
│                                                    │
│  slack_errors: list[str] = []                      │
│                                                    │
│  for each block:                                   │
│    try:                                            │
│      await _run_llm_loop(block)  ──────────────┐  │
│    except openai.APIError:  ◀── Rule 6 handler  │  │
│      post_slack_message(OpenAI error)           │  │
│      sys.exit(1)                                │  │
│    except Exception:  ◀──────── Rule 5 handler  │  │
│      slack_errors.append(...)                   │  │
│      continue                                   │  │
│                                                 │  │
│  if slack_errors:                               │  │
│    post_slack_message(consolidated)             │  │
│    → if fails: print to stdout + exit(1)        │  │
└─────────────────────────────────────────────────┼──┘
                                                  │
                         ┌────────────────────────┘
                         ▼
         ┌────────────────────────────┐
         │  triage_agent.py           │
         │  _run_llm_loop(block_text) │
         │                            │
         │  _client.chat.completions  │
         │    .create(...)  ──────────┼──▶  OpenAI API
         │                            │     (raises APIError
         │  _execute_tool(name, args) │      → propagates up)
         └────────────┬───────────────┘
                      │
          ┌───────────┴──────────────┐
          │                          │
          ▼                          ▼
┌──────────────────────┐   ┌───────────────────────┐
│  jira_tools.py [MOD] │   │  slack_tools.py        │
│  create_jira_ticket()│   │  post_slack_message()  │
│                      │   │  ask_for_clarification │
│  try:                │   │                        │
│    jira_mcp_session()│   │  slack_mcp_session()   │
│    → uvx mcp-atlassian    │  → npx server-slack    │
│  except Exception:   │   │  (raises → propagates  │
│    post_slack_msg()  │   │   to run() per-block   │
│    return [JIRA_ERROR│   │   except Exception)    │
└──────────────────────┘   └───────────────────────┘
```

---

## Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    participant RT    as run_triage.py
    participant TA    as ✏️ triage_agent.py<br/>run()
    participant LLM   as triage_agent.py<br/>_run_llm_loop()
    participant OAI   as OpenAI API<br/>gpt-4o
    participant ET    as triage_agent.py<br/>_execute_tool()
    participant JT    as ✏️ jira_tools.py<br/>create_jira_ticket()
    participant JM    as Jira MCP<br/>uvx mcp-atlassian
    participant ST    as slack_tools.py<br/>post_slack_message()
    participant SM    as Slack MCP<br/>npx server-slack

    RT->>TA: asyncio.run(run())
    Note over TA: slack_errors = []

    loop For each conversation block
        Note over TA: try: _run_llm_loop(block)

        TA->>LLM: _run_llm_loop(block_text)

        %% ── OPENAI FAILURE (Rule 6) ──────────────────────────────
        alt OpenAI unavailable
            LLM->>OAI: chat.completions.create(...)
            OAI-->>LLM: raises openai.APIError
            LLM-->>TA: propagates openai.APIError
            Note over TA: except openai.APIError → Rule 6
            TA->>ST: post_slack_message("⚠️ OpenAI unavailable — triage manually")
            alt Slack also down
                SM-->>ST: raises Exception
                ST-->>TA: raises Exception
                Note over TA: print("[TRIAGE AGENT FATAL]...") to stdout
            else Slack up
                ST->>SM: call_tool("slack_post_message")
                SM-->>ST: ok
                ST-->>TA: "Message posted"
            end
            Note over TA: sys.exit(1)

        %% ── HAPPY PATH + JIRA/SLACK ERRORS ──────────────────────
        else LLM reaches tool_calls
            LLM->>OAI: chat.completions.create(...)
            OAI-->>LLM: finish_reason=tool_calls

            LLM->>ET: _execute_tool("create_jira_ticket", args)
            ET->>JT: create_jira_ticket(summary, type, priority, ...)

            %% ── JIRA FAILURE (Rule 1) ─────────────────────────
            alt Jira MCP unavailable
                JT->>JM: jira_mcp_session() / call_tool(...)
                JM-->>JT: raises Exception
                Note over JT: except Exception → Rule 1
                JT->>ST: post_slack_message("⚠️ Jira unavailable — create manually: {summary}")

                alt Slack also down
                    SM-->>ST: raises Exception
                    ST-->>JT: raises Exception
                    JT-->>ET: propagates Exception
                    ET-->>LLM: propagates Exception
                    LLM-->>TA: propagates Exception
                    Note over TA: except Exception → Rule 5<br/>slack_errors.append(block + error)
                else Slack up
                    ST->>SM: call_tool("slack_post_message")
                    SM-->>ST: ok
                    JT-->>ET: "[JIRA_ERROR] Jira unavailable — team notified. Do not re-notify."
                    ET-->>LLM: "[JIRA_ERROR]..." (tool result)
                    Note over LLM: LLM reads [JIRA_ERROR] prefix,<br/>does not post to Slack again
                    LLM->>OAI: append tool result, call again
                    OAI-->>LLM: finish_reason=stop
                    LLM-->>TA: done — continue to next block
                end

            %% ── JIRA SUCCESS, THEN SLACK TOOL ────────────────
            else Jira up
                JT->>JM: jira_mcp_session() / call_tool(...)
                JM-->>JT: {"issue": {"key": "SCRUM-X"}}
                JT-->>ET: "Created SCRUM-X: {summary} → {url}"
                ET-->>LLM: tool result
                LLM->>OAI: append result, call again
                OAI-->>LLM: finish_reason=tool_calls (post_slack_message)
                LLM->>ET: _execute_tool("post_slack_message", {message})
                ET->>ST: post_slack_message("Created SCRUM-X...")

                %% ── SLACK MCP FAILURE (Rule 5) ───────────────
                alt Slack MCP unavailable
                    SM-->>ST: raises Exception
                    ST-->>ET: raises Exception
                    ET-->>LLM: propagates Exception
                    LLM-->>TA: propagates Exception
                    Note over TA: except Exception → Rule 5<br/>slack_errors.append(block + error)<br/>continue to next block
                else Slack up
                    ST->>SM: call_tool("slack_post_message")
                    SM-->>ST: ok
                    ST-->>ET: "Message posted"
                    ET-->>LLM: tool result
                    LLM->>OAI: call again
                    OAI-->>LLM: finish_reason=stop
                    LLM-->>TA: done
                end
            end
        end
    end

    %% ── END OF RUN — CONSOLIDATED SLACK ERROR REPORT ─────────
    alt slack_errors is not empty
        Note over TA: Rule 5 — consolidated error post
        TA->>ST: post_slack_message("⚠️ Agent run completed with N failures...")
        alt Slack still down
            SM-->>ST: raises Exception
            ST-->>TA: raises Exception
            Note over TA: print("[TRIAGE AGENT ERROR]...") to stdout<br/>sys.exit(1)
        else Slack recovered
            ST->>SM: call_tool("slack_post_message")
            SM-->>ST: ok
            TA-->>RT: run complete (partial — errors reported)
        end
    else No errors
        TA-->>RT: run complete (clean)
    end
```

---

## Flowchart — Decision Logic in `run()`

```mermaid
flowchart TD
    START([run starts]) --> INIT[slack_errors = empty list]
    INIT --> BLOCKLOOP{more blocks?}

    BLOCKLOOP -->|yes| TRY[try: _run_llm_loop block]
    BLOCKLOOP -->|no| CHECKERRORS{slack_errors empty?}

    TRY --> LLMDONE[LLM loop completes normally]
    LLMDONE --> BLOCKLOOP

    TRY -->|raises openai.APIError| RULE6[Rule 6 handler]
    RULE6 --> POSTSLACK6[try: post_slack_message OpenAI error]
    POSTSLACK6 -->|Slack up| EXIT6A[sys.exit 1]
    POSTSLACK6 -->|Slack also down| STDOUT6[print to stdout]
    STDOUT6 --> EXIT6B[sys.exit 1]

    TRY -->|raises Exception| RULE5[Rule 5 handler]
    RULE5 --> ACCUMULATE[slack_errors.append block + error]
    ACCUMULATE --> BLOCKLOOP

    CHECKERRORS -->|yes — clean run| DONE([run complete clean])

    CHECKERRORS -->|no — post consolidated| POSTCONSOLIDATED[try: post_slack_message consolidated summary]
    POSTCONSOLIDATED -->|Slack up| DONE2([run complete partial])
    POSTCONSOLIDATED -->|Slack down| STDOUT5[print consolidated to stdout]
    STDOUT5 --> EXIT5[sys.exit 1]

    style RULE6 fill:#f66,color:#fff
    style RULE5 fill:#f96,color:#fff
    style EXIT6A fill:#c00,color:#fff
    style EXIT6B fill:#c00,color:#fff
    style EXIT5 fill:#c00,color:#fff
    style DONE fill:#6a6,color:#fff
    style DONE2 fill:#aa6,color:#fff
```

---

## Data Flow Summary

| Stage | Source | Shape | Destination |
|---|---|---|---|
| Jira MCP exception | `uvx mcp-atlassian` subprocess | `Exception` with error message | Caught in `create_jira_ticket()` |
| Jira error alert | `create_jira_ticket()` | `str` — "⚠️ Jira unavailable — please create manually: {summary}" | `post_slack_message()` → Slack channel |
| Jira error return | `create_jira_ticket()` | `str` — "[JIRA_ERROR] Jira unavailable — team notified..." | `_execute_tool()` → LLM as tool result |
| OpenAI exception | `openai.APIError` subclass | Exception with `.message` field | Caught in `run()` per-block handler |
| OpenAI error alert | `run()` | `str` — "⚠️ OpenAI unavailable — triage manually: {error}" | `post_slack_message()` → Slack channel |
| Slack MCP exception | `npx server-slack` subprocess | `Exception` with error message | Propagates to `run()` per-block handler |
| Slack error accumulation | `run()` | `str` — "Block '{snippet}...': {error}" | `slack_errors` list (in-memory, per run) |
| Consolidated error post | `run()` | `str` — multi-line summary with N failed blocks | `post_slack_message()` → Slack channel |
| Last-resort stdout | `run()` | `str` — "[TRIAGE AGENT ERROR]..." | Terminal stdout |

**Nothing new is stored or mutated on disk in Phase 2.** All error state is in-memory within a single `run()` call.

---

## Flow Plain-English

- **Jira failure handler** (`jira_tools.py → create_jira_ticket()`)
  - **Purpose:** When the Jira MCP subprocess fails, immediately notify the team in Slack and return a special error string so the LLM knows not to send a second notification
  - **Input:** Any `Exception` raised by `jira_mcp_session()` or `session.call_tool()`; original ticket args (summary, type, priority)
  - **Output:** Slack alert posted; `str` starting with `[JIRA_ERROR]` returned to the LLM as the tool result

- **OpenAI failure handler** (`triage_agent.py → run()`)
  - **Purpose:** When OpenAI is unreachable, stop the entire run immediately and tell the team to triage manually — the agent is useless without the LLM
  - **Input:** `openai.APIError` propagated from `_run_llm_loop()`
  - **Output:** Slack alert posted (or stdout if Slack is also down); process exits with code 1

- **Slack MCP per-block accumulator** (`triage_agent.py → run()`)
  - **Purpose:** When a Slack post fails mid-run, record the failure and continue processing remaining blocks so no messages are silently skipped
  - **Input:** `Exception` propagated from `post_slack_message()` or `ask_for_clarification()` through the LLM loop
  - **Output:** Error appended to `slack_errors` list; block processing continues

- **Consolidated error report** (`triage_agent.py → run()`)
  - **Purpose:** At the end of a run with Slack failures, post one summary message listing every block that could not be notified — the team knows exactly which messages need manual follow-up
  - **Input:** `slack_errors` list (non-empty); each entry has block text snippet + error detail
  - **Output:** Single Slack message with all failures listed; or stdout + exit 1 if Slack is still down

- **Stdout last-resort fallback** (`triage_agent.py → run()`)
  - **Purpose:** When Slack itself is unavailable and no notification can be posted, write the full error to terminal output so it is captured by any CI/CD or process manager running the agent
  - **Input:** Any `Exception` raised by the final `post_slack_message()` calls (OpenAI handler or consolidated post)
  - **Output:** `[TRIAGE AGENT FATAL]` or `[TRIAGE AGENT ERROR]` prefixed lines written to stdout; process exits with code 1
