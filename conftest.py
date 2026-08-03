"""
Root conftest.py — loads config/.env before any test runs.
Required so unit and integration tests can import settings without error.
"""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / "config" / ".env")
