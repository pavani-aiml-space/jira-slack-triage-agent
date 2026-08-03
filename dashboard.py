"""
JiraSlack — Run Dashboard

A local Streamlit dashboard for monitoring and triggering the triage agent.

Usage:
    streamlit run dashboard.py

Features:
    - Run history table (newest-first) from logs/ directory
    - Click a run to see per-block details and errors
    - "Run Agent" button triggers run_triage.py as a background subprocess
    - Auto-refreshes every 2 seconds while the agent is running
"""
import os
import subprocess
import time

import streamlit as st

from config.settings import settings
from pipeline.quality_metrics import load_quality_store, rolling_thumbs_up_rate
from pipeline.run_logger import SENTINEL_FILE, load_run_logs

# ── GPT-4o pricing (per 1M tokens, USD) ──────────────────────────────────────
# Source: https://openai.com/pricing  — update here if pricing changes
PRICE_PER_1M_INPUT  = 2.50   # prompt tokens
PRICE_PER_1M_OUTPUT = 15.00  # completion tokens


def compute_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Return USD cost for one LLM call at current GPT-4o rates."""
    return (prompt_tokens / 1_000_000) * PRICE_PER_1M_INPUT \
         + (completion_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT


def run_total_cost(log: dict) -> float:
    """Sum cost across all blocks in a run log dict."""
    total = 0.0
    for b in log.get("blocks", []):
        llm = b.get("llm")
        if llm:
            total += compute_cost(
                llm.get("prompt_tokens", 0),
                llm.get("completion_tokens", 0),
            )
    return total


def fmt_cost(usd: float) -> str:
    """Format a USD cost — show cents for small amounts, dollars for large."""
    if usd < 0.01:
        return f"${usd * 100:.3f}¢"
    return f"${usd:.4f}"


def _render_quality_section() -> None:
    """
    Render the Phase 5 quality trend section.
    Shows thumbs-up rate per run as a line chart.
    Displays 'warming up' message when total reactions < MIN_REACTIONS_FOR_QUALITY.
    """
    st.subheader("👍 Quality Trend")
    store = load_quality_store(settings.QUALITY_STORE_PATH)

    if not store.runs:
        st.info(
            f"🔄 Warming up — no reactions collected yet.  "
            f"React to bot confirmation messages with 👍 or 👎 to build the quality signal."
        )
        return

    total_reactions = sum(r.reactions_found for r in store.runs)
    min_required    = settings.MIN_REACTIONS_FOR_QUALITY

    if total_reactions < min_required:
        st.info(
            f"🔄 Warming up ({total_reactions}/{min_required} reactions).  "
            f"Need {min_required - total_reactions} more 👍/👎 reactions before quality alerts activate."
        )

    rolling = rolling_thumbs_up_rate(store)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Reactions", total_reactions)
    col2.metric("Rolling Thumbs-Up Rate",
                f"{rolling * 100:.0f}%" if rolling is not None else "—")
    col3.metric("Alert Threshold", f"{settings.QUALITY_ALERT_THRESHOLD * 100:.0f}%")

    chart_data = [
        {
            "Run":          r.run_id[:16],
            "Thumbs-Up %":  round(r.thumbs_up_rate * 100, 1) if r.thumbs_up_rate is not None else None,
            "👍":            r.thumbs_up,
            "👎":            r.thumbs_down,
        }
        for r in store.runs
        if r.thumbs_up_rate is not None
    ]

    if chart_data:
        st.line_chart(
            data={d["Run"]: d["Thumbs-Up %"] for d in chart_data},
            use_container_width=True,
        )
        st.dataframe(chart_data, use_container_width=True)
    else:
        st.caption("No runs with confirmed reactions yet.")


# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="JiraSlack Dashboard", layout="wide")
st.title("🤖 JiraSlack — Run Dashboard")

# ── Running state ─────────────────────────────────────────────────────────────
is_running = os.path.exists(SENTINEL_FILE)

if is_running:
    st.warning("⏳ Agent is running...")
    time.sleep(2)
    st.rerun()

# ── Trigger ───────────────────────────────────────────────────────────────────
col_btn, col_info = st.columns([1, 4])
with col_btn:
    if st.button("▶  Run Agent", disabled=is_running, type="primary"):
        try:
            subprocess.Popen(["python", "run_triage.py"])
            st.rerun()
        except FileNotFoundError as e:
            st.error(f"Could not start agent: {e}")

with col_info:
    st.caption(f"Channel: `{settings.SLACK_CHANNEL_ID}` · "
               f"Max messages: {settings.MAX_MESSAGES_TO_FETCH} · "
               f"Log dir: `{settings.LOG_DIR}` · "
               f"Pricing: ${PRICE_PER_1M_INPUT}/1M input · ${PRICE_PER_1M_OUTPUT}/1M output (GPT-4o)")

st.divider()

# ── Run history ───────────────────────────────────────────────────────────────
logs = load_run_logs(settings.LOG_DIR)

if not logs:
    st.info("No runs yet. Click **Run Agent** to start.")
else:
    # Summary table
    st.subheader(f"Run History ({len(logs)} run{'s' if len(logs) != 1 else ''})")

    STATUS_ICONS = {"success": "✅", "partial": "⚠️", "fatal": "❌"}

    rows = [
        {
            "Run ID":         l["run_id"],
            "Status":         f"{STATUS_ICONS.get(l['status'], '—')} {l['status']}",
            "Tickets":        l.get("tickets_created_count", 0),
            "Duplicates":     l.get("duplicates_flagged_count", 0),
            "Clarifications": l.get("clarifications_asked_count", 0),
            "Errors":         l.get("error_count", 0),
            "Blocks":         l.get("blocks_processed", 0),
            "Messages":       l.get("messages_fetched", 0),
            "Cost (USD)":     fmt_cost(run_total_cost(l)),
        }
        for l in logs
    ]
    st.dataframe(rows, width="stretch")

    # Total cost across all runs
    grand_total = sum(run_total_cost(l) for l in logs)
    st.caption(f"Total cost across all {len(logs)} run(s): **{fmt_cost(grand_total)}**")

    st.divider()

    # Detail view
    st.subheader("Run Details")
    selected_id = st.selectbox("Select a run to inspect", [l["run_id"] for l in logs])
    selected = next(l for l in logs if l["run_id"] == selected_id)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Blocks**")
        for b in selected.get("blocks", []):
            icon = {"ticket_created": "✅", "clarification_asked": "💬",
                    "error": "⚠️", "no_action": "—"}.get(b.get("action", ""), "—")
            if b.get("action") == "ticket_created":
                key = b.get("ticket_key", "?")
                summary = b.get("ticket_summary", "")
                btype = b.get("ticket_type", "")
                priority = b.get("ticket_priority", "")
                st.write(f"{icon} Block {b['block_index']+1}: **{key}** — {summary} ({btype} · {priority})")
            else:
                st.write(f"{icon} Block {b['block_index']+1}: {b.get('action', '?')}")

        if not selected.get("blocks"):
            st.caption("No block data")

    with col2:
        st.markdown("**Errors**")
        errors = selected.get("errors", [])
        if errors:
            for e in errors:
                st.error(
                    f"Block {e['block_index']+1}: `{e.get('error_type', '?')}` — "
                    f"{e.get('error_message', '')} *(Phase 2 {e.get('phase2_rule', '')})*"
                )
        else:
            st.success("No errors this run")

    # LLM stats + cost expander
    with st.expander("LLM Stats (tokens + cost)"):
        run_cost = 0.0
        for b in selected.get("blocks", []):
            llm = b.get("llm")
            if llm:
                pt = llm.get("prompt_tokens", 0)
                ct = llm.get("completion_tokens", 0)
                block_cost = compute_cost(pt, ct)
                run_cost += block_cost
                st.write(
                    f"Block {b['block_index']+1}: "
                    f"{llm.get('iterations', '?')} iterations · "
                    f"tools: {', '.join(llm.get('tools_called', [])) or 'none'} · "
                    f"prompt: {pt:,} tokens · "
                    f"completion: {ct:,} tokens · "
                    f"**cost: {fmt_cost(block_cost)}**"
                )
        if run_cost > 0:
            st.markdown(f"---\n**Run total: {fmt_cost(run_cost)}**  "
                        f"*(input @ ${PRICE_PER_1M_INPUT}/1M · output @ ${PRICE_PER_1M_OUTPUT}/1M)*")

st.divider()
_render_quality_section()
