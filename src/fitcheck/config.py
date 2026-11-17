"""Load API keys from .env and fetch them safely."""

import os

from dotenv import load_dotenv


class MissingKeyError(RuntimeError):
    """Setup is missing: a required environment variable (empty counts), or an optional package."""


def load_env() -> None:
    """Read .env into os.environ. Variables already set are not overwritten."""
    load_dotenv()


def require(name: str) -> str:
    """Return the value of env var `name`, or raise MissingKeyError."""
    value = os.environ.get(name)
    # An empty value (e.g. `ANTHROPIC_API_KEY=` in .env) counts as missing.
    if not value:
        raise MissingKeyError(
            f"{name} is not set. Copy .env.example to .env and fill it in."
        )
    return value
