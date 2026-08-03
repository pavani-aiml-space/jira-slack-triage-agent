"""
Duplicate Detector — pre-ticket gate using embedding-based similarity.

Pre-ticket gate flow:
  1. fetch_open_tickets  — pull open Jira tickets via JQL
  2. load_embedding_cache — read memory/ticket_embeddings.json from disk
  3. build_embedding_cache — embed any tickets not already cached; write updated cache
  4. find_duplicate — compare block embedding against cache with cosine similarity
  5. add_ticket_to_cache — store newly-created tickets for intra-run dedup
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from datetime import datetime
from typing import Optional

from openai import OpenAI

from agents.triage.tools.jira_tools import jira_mcp_session
from config.settings import settings

_embed_client = OpenAI(api_key=settings.OPENAI_API_KEY)


# ── Pure maths ────────────────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Return cosine similarity in [0.0, 1.0]. Returns 0.0 for zero vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    mag = math.sqrt(sum(x ** 2 for x in a)) * math.sqrt(sum(x ** 2 for x in b))
    return dot / mag if mag else 0.0


def find_duplicate(
    block_embedding: list[float],
    cache: dict,
    threshold: float,
) -> Optional[dict]:
    """
    Return {key, summary, similarity} for the best match above threshold.
    Returns None if no match or cache is empty.
    """
    best: Optional[dict] = None
    for key, entry in cache.items():
        sim = cosine_similarity(block_embedding, entry["embedding"])
        if sim >= threshold and (best is None or sim > best["similarity"]):
            best = {"key": key, "summary": entry["summary"], "similarity": sim}
    return best


# ── Cache I/O ─────────────────────────────────────────────────────────────────

def load_embedding_cache(cache_path: str) -> dict:
    """
    Read memory/ticket_embeddings.json.
    Returns {} if file is missing or malformed — never raises.
    """
    try:
        with open(cache_path) as f:
            return json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_cache(cache_path: str, tickets: dict) -> None:
    """Write the tickets dict to disk wrapped in the cache envelope."""
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    payload = {
        "refreshed_at": datetime.utcnow().isoformat(timespec="seconds"),
        "project_key": settings.JIRA_PROJECT_KEY,
        "tickets": tickets,
    }
    with open(cache_path, "w") as f:
        json.dump(payload, f, indent=2)


# ── Embedding ─────────────────────────────────────────────────────────────────

async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of strings via OpenAI text-embedding-3-small.
    Returns one float vector per input string.
    """
    resp = await asyncio.to_thread(
        _embed_client.embeddings.create,
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in resp.data]


async def build_embedding_cache(
    tickets: list[dict],
    existing_cache: dict,
    cache_path: str,
) -> dict:
    """
    Rebuild the embedding cache for the current set of open tickets.

    - Copies (not mutates) the input cache (DEBT-013).
    - Prunes entries for tickets no longer open (DEBT-012).
    - Embeds only tickets NOT already in the cache (diff by key).
    - Writes updated cache to disk.
    - Returns full flat tickets dict {ticket_key: {summary, status, embedding}}.
    """
    # Copy to avoid mutating the caller's dict
    existing_tickets: dict = dict(existing_cache.get("tickets", existing_cache))

    # Remove tickets that are no longer open (closed, done, deleted)
    open_keys = {t["key"] for t in tickets}
    existing_tickets = {k: v for k, v in existing_tickets.items() if k in open_keys}

    new_tickets = [t for t in tickets if t["key"] not in existing_tickets]

    if new_tickets:
        summaries = [t["summary"] for t in new_tickets]
        embeddings = await embed_texts(summaries)
        for ticket, embedding in zip(new_tickets, embeddings):
            existing_tickets[ticket["key"]] = {
                "summary": ticket["summary"],
                "status": ticket.get("status", ""),
                "embedding": embedding,
            }

    _write_cache(cache_path, existing_tickets)
    return existing_tickets


# ── Jira fetch ────────────────────────────────────────────────────────────────

async def fetch_open_tickets(project_key: str) -> list[dict]:
    """
    Fetch open Jira tickets via jira_search JQL, paginating until all are retrieved.
    Returns [] on any error (caller skips duplicate check — Rule 5).
    Partial results accumulated before an error are returned rather than discarded.
    """
    jql = (
        f"project = {project_key} AND status not in (Done, Closed) "
        f"ORDER BY created DESC"
    )
    all_issues: list[dict] = []
    limit = settings.JIRA_OPEN_TICKETS_LIMIT
    try:
        async with jira_mcp_session() as session:
            for page in range(settings.JIRA_MAX_PAGES):
                result = await session.call_tool(
                    "jira_search",
                    {"jql": jql, "limit": limit, "start_at": page * limit},
                )
                data = json.loads(result.content[0].text)
                issues = data.get("issues", [])
                all_issues.extend(
                    {
                        "key": issue["key"],
                        "summary": issue["summary"],
                        "status": issue["status"]["name"],
                    }
                    for issue in issues
                )
                if len(issues) < limit:
                    break
    except Exception as e:
        print(f"[duplicate_detector] fetch_open_tickets failed (Rule 5): {e}")
    return all_issues


# ── Cache update ──────────────────────────────────────────────────────────────

def add_ticket_to_cache(
    cache: dict,
    ticket_key: str,
    ticket_summary: str,
    ticket_embedding: list[float],
    cache_path: str,
) -> dict:
    """
    Add a newly created ticket to the in-memory cache and persist to disk.
    Prevents intra-run duplicates (same issue reported twice in one run).
    Returns updated cache.
    """
    cache[ticket_key] = {
        "summary": ticket_summary,
        "status": "Open",
        "embedding": ticket_embedding,
    }
    try:
        _write_cache(cache_path, cache)
    except Exception as e:
        print(f"[duplicate_detector] add_ticket_to_cache disk write failed: {e}")
    return cache
