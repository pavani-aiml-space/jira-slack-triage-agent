import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from config/ directory
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


class Settings:
    # ── LLM ───────────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Default triage brain: Claude. Set LLM_PROVIDER=openai + LLM_MODEL=gpt-4o to swap.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-5-20250929")
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))
    # Embeddings always use OpenAI (Anthropic has no embeddings API).

    # ── Slack ──────────────────────────────────────────────────────────────────
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_TEAM_ID: str = os.getenv("SLACK_TEAM_ID", "")
    SLACK_USER_ID: str = os.getenv("SLACK_USER_ID", "")
    SLACK_CHANNEL_ID: str = os.getenv("SLACK_CHANNEL_ID", "")

    # ── Jira ───────────────────────────────────────────────────────────────────
    JIRA_URL: str = os.getenv("JIRA_URL", "")
    JIRA_EMAIL: str = os.getenv("JIRA_EMAIL", "")
    JIRA_API_TOKEN: str = os.getenv("JIRA_API_TOKEN", "")
    JIRA_PROJECT_KEY: str = os.getenv("JIRA_PROJECT_KEY", "DEV")

    # ── Agent behaviour ────────────────────────────────────────────────────────
    CONFIDENCE_AUTO_ACT: float = float(os.getenv("CONFIDENCE_AUTO_ACT", "0.90"))
    CONFIDENCE_ASK_HUMAN: float = float(os.getenv("CONFIDENCE_ASK_HUMAN", "0.65"))
    MAX_AGENT_ITERATIONS: int = int(os.getenv("MAX_AGENT_ITERATIONS", "10"))

    # ── Triage Context ─────────────────────────────────────────────────────────
    CONTEXT_WINDOW_MINUTES: int = int(os.getenv("CONTEXT_WINDOW_MINUTES", "5"))
    MAX_MESSAGES_TO_FETCH: int = int(os.getenv("MAX_MESSAGES_TO_FETCH", "20"))
    # When a watermark exists, this limit is the cap on new messages since the last run.
    # On first run (no watermark) it caps the bootstrap fetch.
    # Increase if the channel is high-volume between runs.

    # ── Observability ──────────────────────────────────────────────────────────
    LOG_DIR: str = os.getenv("LOG_DIR", "logs")

    # ── Duplicate Detection ────────────────────────────────────────────────────
    DUPLICATE_SIMILARITY_THRESHOLD: float = float(
        os.getenv("DUPLICATE_SIMILARITY_THRESHOLD", "0.85")
    )
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_CACHE_PATH: str = os.getenv("EMBEDDING_CACHE_PATH", "memory/ticket_embeddings.json")
    JIRA_OPEN_TICKETS_LIMIT: int = int(os.getenv("JIRA_OPEN_TICKETS_LIMIT", "100"))
    # Max pages fetched per run; 100 per page × 10 pages = up to 1000 open tickets
    JIRA_MAX_PAGES: int = int(os.getenv("JIRA_MAX_PAGES", "10"))

    # ── Phase 5 — Eval & Feedback ──────────────────────────────────────────────
    QUALITY_ALERT_THRESHOLD:   float = float(os.getenv("QUALITY_ALERT_THRESHOLD",   "0.70"))
    MIN_REACTIONS_FOR_QUALITY: int   = int(os.getenv("MIN_REACTIONS_FOR_QUALITY",    "5"))
    REACTION_WINDOW_HOURS:     int   = int(os.getenv("REACTION_WINDOW_HOURS",        "48"))
    REACTION_HISTORY_LIMIT:    int   = int(os.getenv("REACTION_HISTORY_LIMIT",       "50"))
    QUALITY_STORE_PATH:        str   = os.getenv("QUALITY_STORE_PATH", "memory/quality_store.json")

    # ── Phase 5b — LLM-as-Judge (post-run scoring; opt-in) ────────────────────
    ENABLE_LLM_JUDGE: bool = os.getenv("ENABLE_LLM_JUDGE", "false").lower() in ("1", "true", "yes")
    # Default judge: OpenAI (different family from Claude triage) when unset.
    JUDGE_LLM_PROVIDER: str = os.getenv("JUDGE_LLM_PROVIDER", "openai")
    JUDGE_LLM_MODEL: str = os.getenv("JUDGE_LLM_MODEL", "gpt-4o-mini")
    JUDGE_STORE_PATH: str = os.getenv("JUDGE_STORE_PATH", "memory/judge_store.json")

    # ── Phase 6 — Scheduling & Watermark ──────────────────────────────────────
    WATERMARK_PATH: str = os.getenv("WATERMARK_PATH", "memory/watermark.json")
    # Minimum interval between scheduled runs (minutes). 0 = run once and exit.
    SCHEDULE_INTERVAL_MINUTES: int = int(os.getenv("SCHEDULE_INTERVAL_MINUTES", "0"))

    # ── Phase 7 — Memory ───────────────────────────────────────────────────────
    EPISODE_STORE_PATH:            str = os.getenv("EPISODE_STORE_PATH",  "memory/episode_store.json")
    SEMANTIC_STORE_PATH:           str = os.getenv("SEMANTIC_STORE_PATH", "memory/semantic_store.json")
    MAX_EPISODES:                  int = int(os.getenv("MAX_EPISODES",                   "200"))
    MAX_INJECTED_EPISODES:         int = int(os.getenv("MAX_INJECTED_EPISODES",          "3"))
    SEMANTIC_EXTRACTION_THRESHOLD: int = int(os.getenv("SEMANTIC_EXTRACTION_THRESHOLD",  "5"))
    SEMANTIC_LLM_MIN_PATTERNS:     int = int(os.getenv("SEMANTIC_LLM_MIN_PATTERNS",      "3"))
    MAX_SEMANTIC_PATTERN_CHARS:    int = int(os.getenv("MAX_SEMANTIC_PATTERN_CHARS",     "1000"))

    @classmethod
    def validate(cls, required: list[str] | None = None) -> None:
        """Raise ValueError listing any missing required variables."""
        if required is not None:
            checks = list(required)
        else:
            checks = [
                "SLACK_BOT_TOKEN",
                "SLACK_TEAM_ID",
                "JIRA_URL",
                "JIRA_EMAIL",
                "JIRA_API_TOKEN",
                "OPENAI_API_KEY",  # embeddings (duplicate gate) always need OpenAI
            ]
            provider = (cls.LLM_PROVIDER or "").strip().lower()
            if provider == "anthropic":
                checks.append("ANTHROPIC_API_KEY")
            elif provider == "openai":
                # OPENAI_API_KEY already listed for embeddings
                pass
            judge = (cls.JUDGE_LLM_PROVIDER or "").strip().lower()
            if cls.ENABLE_LLM_JUDGE and judge == "anthropic":
                if "ANTHROPIC_API_KEY" not in checks:
                    checks.append("ANTHROPIC_API_KEY")
        missing = [k for k in checks if not getattr(cls, k, "")]
        if missing:
            raise ValueError(
                f"Missing environment variables: {', '.join(missing)}\n"
                f"Copy config/.env.example → config/.env and fill in the values."
            )


settings = Settings()
