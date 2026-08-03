"""
Episode Store — episodic memory layer.

Persists every triage decision as an Episode so the agent can recall
similar past decisions when processing new conversation blocks.

Lifecycle:
  pre_run  → load_episode_store (read existing episodes)
  per-block → retrieve_similar (find relevant episodes by cosine similarity)
  post_run → add_episode + save_episode_store (persist new decisions)
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from pipeline.duplicate_detector import cosine_similarity


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Episode:
    run_id:          str
    block_index:     int
    block_snippet:   str
    ticket_key:      str
    ticket_type:     str
    ticket_priority: str
    ticket_summary:  str
    embedding:       list[float]
    run_ts:          str


@dataclass
class EpisodeStore:
    episodes: list[Episode] = field(default_factory=list)


# ── I/O ───────────────────────────────────────────────────────────────────────

def load_episode_store(path: str) -> EpisodeStore:
    """
    Load EpisodeStore from disk.
    Returns empty EpisodeStore if the file is missing or corrupt — never raises.
    """
    try:
        with open(path) as f:
            data = json.load(f)
        episodes = [Episode(**ep) for ep in data.get("episodes", [])]
        return EpisodeStore(episodes=episodes)
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return EpisodeStore()


def save_episode_store(store: EpisodeStore, path: str) -> None:
    """
    Persist EpisodeStore to disk.
    Creates parent directories if needed. Never raises (Rule 5).
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"episodes": [asdict(ep) for ep in store.episodes]}, f, indent=2)
    except Exception as e:
        print(f"[episode_store] save_episode_store failed (Rule 5): {e}")


# ── Episode operations ────────────────────────────────────────────────────────

def add_episode(store: EpisodeStore, episode: Episode, max_episodes: int) -> None:
    """
    Append episode to store, pruning oldest entries to stay within max_episodes.
    Oldest-first: the beginning of the list is the oldest.
    """
    store.episodes.append(episode)
    if len(store.episodes) > max_episodes:
        store.episodes = store.episodes[-max_episodes:]


def retrieve_similar(
    store: EpisodeStore,
    query_emb: list[float],
    top_k: int,
) -> list[Episode]:
    """
    Return up to top_k episodes sorted by cosine similarity to query_emb.
    Returns [] if the store is empty (Rule 11 — no injection, not an error).
    """
    if not store.episodes:
        return []
    scored = [
        (cosine_similarity(query_emb, ep.embedding), ep)
        for ep in store.episodes
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [ep for _, ep in scored[:top_k]]


def format_episode_context(episodes: list[Episode]) -> str:
    """
    Format a list of episodes as a human-readable block for LLM injection.
    Returns "" if no episodes (Rule 11).
    """
    if not episodes:
        return ""
    lines = ["## Similar past decisions"]
    for ep in episodes:
        date = ep.run_ts[:10]
        snippet = ep.block_snippet[:80]
        lines.append(
            f"- [{ep.ticket_key}] \"{snippet}\" → {ep.ticket_type}, {ep.ticket_priority} ({date})"
        )
    return "\n".join(lines)
