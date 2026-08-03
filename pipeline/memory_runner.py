"""
Memory Runner — orchestrates the two-step memory lifecycle.

Lifecycle:
  pre_run()  → load stores, build MemoryContext (semantic injection + episode store)
  post_run() → write new episodes, extract and summarise semantic patterns

Called from run_triage.main() wrapping the triage + eval steps:
  1. memory_pre  = await pre_run()
  2. run_eval_step(None)
  3. run_log = await triage_run(memory_context=memory_context)
  4. run_eval_step(run_log)
  5. await post_run(run_log)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from config.settings import settings
from pipeline.duplicate_detector import embed_texts
from pipeline.episode_store import (
    Episode,
    EpisodeStore,
    add_episode,
    load_episode_store,
    save_episode_store,
)
from pipeline.semantic_store import (
    SemanticStore,
    build_semantic_injection,
    extract_count_patterns,
    load_semantic_store,
    save_semantic_store,
    summarise_with_llm,
)

if TYPE_CHECKING:
    from pipeline.run_logger import RunLog


# ── MemoryContext ─────────────────────────────────────────────────────────────

@dataclass
class MemoryContext:
    """Memory state snapshot for one triage run — built by pre_run(), consumed by triage_agent.run()."""
    semantic_injection: str          = ""
    episode_store:      EpisodeStore = field(default_factory=EpisodeStore)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

async def pre_run() -> MemoryContext:
    """
    Load episode and semantic stores from disk; build the MemoryContext
    that will be passed to triage_agent.run().
    Returns an empty MemoryContext on any failure (Rule 5).
    """
    try:
        ep_store   = load_episode_store(settings.EPISODE_STORE_PATH)
        sem_store  = load_semantic_store(settings.SEMANTIC_STORE_PATH)
        injection  = build_semantic_injection(sem_store, settings.MAX_SEMANTIC_PATTERN_CHARS)
        return MemoryContext(semantic_injection=injection, episode_store=ep_store)
    except Exception as e:
        print(f"[memory_runner] pre_run failed (Rule 5): {e}")
        return MemoryContext()


async def post_run(run_log: "RunLog") -> None:
    """
    Persist new episodes for every ticket_created block in run_log.
    Trigger semantic extraction when enough new episodes have accumulated.
    All failures are silent (Rule 5).
    """
    try:
        ep_store  = load_episode_store(settings.EPISODE_STORE_PATH)
        sem_store = load_semantic_store(settings.SEMANTIC_STORE_PATH)
        run_ts    = datetime.now(timezone.utc).isoformat(timespec="seconds")

        # ── Write one episode per ticket_created block ────────────────────────
        for block in run_log.blocks:
            if block.action != "ticket_created":
                continue
            if not block.ticket_summary:
                continue
            try:
                [emb] = await embed_texts([block.ticket_summary])
            except Exception as e:
                print(f"[memory_runner] embed_texts failed for block {block.block_index} (Rule 5): {e}")
                continue

            episode = Episode(
                run_id=run_log.run_id,
                block_index=block.block_index,
                block_snippet=block.block_snippet,
                ticket_key=block.ticket_key or "",
                ticket_type=block.ticket_type or "",
                ticket_priority=block.ticket_priority or "",
                ticket_summary=block.ticket_summary,
                embedding=emb,
                run_ts=run_ts,
            )
            add_episode(ep_store, episode, settings.MAX_EPISODES)

        save_episode_store(ep_store, settings.EPISODE_STORE_PATH)

        # ── Semantic extraction threshold check ───────────────────────────────
        total_episodes = len(ep_store.episodes)
        delta = total_episodes - sem_store.last_extracted_episode_count
        if delta >= settings.SEMANTIC_EXTRACTION_THRESHOLD:
            patterns = extract_count_patterns(
                ep_store.episodes,
                min_count=settings.SEMANTIC_EXTRACTION_THRESHOLD,
            )
            if len(patterns) >= settings.SEMANTIC_LLM_MIN_PATTERNS:
                patterns = await summarise_with_llm(patterns)

            sem_store.patterns = patterns
            sem_store.last_extracted_episode_count = total_episodes
            save_semantic_store(sem_store, settings.SEMANTIC_STORE_PATH)

    except Exception as e:
        print(f"[memory_runner] post_run failed (Rule 5): {e}")
