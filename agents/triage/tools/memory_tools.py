"""
Memory Tools — LLM-callable tool for on-demand episode retrieval.

Two parts (same pattern as jira_tools.py / slack_tools.py):
  1. SCHEMA   — JSON schema the LLM uses to know the tool exists
  2. EXECUTOR — Python function that retrieves similar past episodes

The executor uses a module-level _active_episode_store that must be set by
triage_agent.run() before the block loop begins (via set_episode_store()).
This is the same side-channel pattern as _confirmation_ts_buffer in slack_tools.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from config.settings import settings
from pipeline.duplicate_detector import embed_texts
from pipeline.episode_store import format_episode_context, retrieve_similar

if TYPE_CHECKING:
    from pipeline.episode_store import EpisodeStore


# ── Module-level store context ────────────────────────────────────────────────
# Set by triage_agent.run() before the block loop when memory_context is present.
# Reset to None at the start of each run (set_episode_store replaces previous value).
_active_episode_store: EpisodeStore | None = None


def set_episode_store(store: EpisodeStore) -> None:
    """
    Set the active episode store for the current run.
    Called once by triage_agent.run() before the block loop when memory_context is set.
    """
    global _active_episode_store
    _active_episode_store = store


# ── 1. SCHEMA — what the LLM sees ────────────────────────────────────────────

SEARCH_MEMORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_memory",
        "description": (
            "Search past triage decisions for similar issues. "
            "Call this when uncertain about ticket type, priority, or whether "
            "a similar issue has already been triaged. "
            "Do NOT call for clear, unambiguous bug reports or feature requests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Brief description of the current issue to search for "
                        "similar past triage decisions"
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


# ── 2. EXECUTOR — what Python runs when the LLM calls the tool ───────────────

async def search_memory(query: str) -> str:
    """
    Retrieve similar past triage decisions from the episode store.

    Called by triage_agent when the LLM is uncertain about ticket type or priority.
    Embeds the query, finds top-K similar episodes by cosine similarity, and
    returns a formatted context string.

    Returns "No memory available for this run." if the store was not set
    (memory_context=None was passed to triage_agent.run()).
    Returns "No similar past decisions found." if the store is empty or no
    episodes match well enough (Rule 11 — empty retrieval is not an error).
    """
    if _active_episode_store is None:
        return "No memory available for this run."

    try:
        [query_emb] = await embed_texts([query])
        similar = retrieve_similar(
            _active_episode_store,
            query_emb,
            settings.MAX_INJECTED_EPISODES,
        )
        result = format_episode_context(similar)
        return result if result else "No similar past decisions found."
    except Exception as e:
        return f"Memory search unavailable: {e}"
