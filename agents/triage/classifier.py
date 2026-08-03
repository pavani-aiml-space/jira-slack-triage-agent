"""
Classifier

Sends a conversation block to OpenAI and gets back:
  - issue type (Bug / Story / Task / Unclear)
  - priority
  - summary + description
  - confidence score
  - duplicate detection (against provided open tickets)
  - reasoning

Flow:
  1. SYSTEM_PROMPT   — rules and role given to the LLM
  2. _build_user_prompt() — builds the user message (Slack text + open tickets)
  3. classify()      — calls OpenAI and returns structured result
"""
import json
from openai import OpenAI

from config.settings import settings


# ── 1. System Prompt — rules and role given to the LLM ───────────────────────
SYSTEM_PROMPT = """You are a software triage agent.

Your job is to read Slack messages from a team and:
1. Classify the issue as Bug, Story, Task, or Unclear
2. Assign a priority (Critical, High, Medium, Low)
3. Write a short Jira ticket summary (max 80 chars, imperative tone)
4. Write a structured description with sections: ## What, ## Steps to Reproduce (bugs only), ## Expected, ## Context
5. Suggest labels (lowercase, hyphenated e.g. ["login", "mobile", "regression"])
6. Give a confidence score 0.0-1.0
7. Check if this matches any of the open Jira tickets provided

Priority guide:
  Critical → all users affected, production down, data loss, security issue
  High     → significant feature broken, affects many users, workaround exists
  Medium   → partial feature broken, affects some users, workaround available
  Low      → minor/cosmetic, affects few users, or small enhancement

Always respond with valid JSON only. No extra text.

JSON format:
{
  "issue_type":    "Bug | Story | Task | Unclear",
  "priority":      "Critical | High | Medium | Low",
  "summary":       "Short imperative title max 80 chars",
  "description":   "Structured markdown description",
  "labels":        ["label1", "label2"],
  "confidence":    0.95,
  "is_duplicate":  true or false,
  "duplicate_of":  "SCRUM-1 or null",
  "reasoning":     "Why you classified it this way"
}"""


# ── 2. Build User Prompt — Slack messages + open tickets ──────────────────────
def _build_user_prompt(combined_text: str, open_tickets: list[dict]) -> str:
    """
    Builds the message sent to the LLM as the "user" turn.

    Combines:
      - The Slack messages to classify
      - The list of open Jira tickets (for duplicate detection)
    """
    if open_tickets:
        tickets_section = "\n\nOpen Jira tickets to check for duplicates:\n"
        for t in open_tickets:
            tickets_section += f"  - {t['key']}: {t['summary']}\n"
    else:
        tickets_section = "\n\nNo open Jira tickets provided."

    return f"""Slack message(s) to classify:

{combined_text}
{tickets_section}

Classify the above and return JSON only."""


# ── 3. Classify — call OpenAI and return structured result ────────────────────
_client = OpenAI(api_key=settings.OPENAI_API_KEY)


def classify(combined_text: str, open_tickets: list[dict] | None = None) -> dict:
    """
    Classify a conversation block using OpenAI.

    Args:
        combined_text : joined Slack messages from one conversation block
        open_tickets  : list of open Jira tickets [{key, summary}] for duplicate check

    Returns:
        dict with issue_type, priority, summary, description, labels,
             confidence, is_duplicate, duplicate_of, reasoning
    """
    # build the user prompt from Slack text + open tickets
    prompt = _build_user_prompt(combined_text, open_tickets or [])

    # send system prompt + user prompt to OpenAI
    response = _client.chat.completions.create(
        model=settings.LLM_MODEL,
        max_tokens=1024,
        response_format={"type": "json_object"},   # force JSON — no code fences
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},   # the rules
            {"role": "user",   "content": prompt},           # the question
        ],
    )

    # extract and parse the JSON response
    raw = response.choices[0].message.content.strip()
    return json.loads(raw)
