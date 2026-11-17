"""Real calls, each well under $0.01. Opt-in only, so normal test runs stay free:

    FITCHECK_LIVE=1 uv run pytest tests/test_live_smoke.py -s
    FITCHECK_LIVE=1 uv run pytest tests/test_live_smoke.py -s -k gemini   (Gemini only, 2 calls)
"""

import os

import pytest
from pydantic import BaseModel

from fitcheck.extract import extract
from fitcheck.providers.anthropic_provider import AnthropicProvider
from fitcheck.providers.budget import BudgetedProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("FITCHECK_LIVE") != "1", reason="live API test; set FITCHECK_LIVE=1 to run"
)


class Echo(BaseModel):
    word: str


def test_haiku_returns_structured_output():
    provider = AnthropicProvider()
    result = provider.generate(
        system="You reply with JSON only.",
        user="Set `word` to the single word: ok",
        schema=Echo,
    )
    print(f"\n{result.output!r}  {result.log}")
    assert result.output.word.strip().lower() == "ok"
    assert result.log.input_tokens > 0 and result.log.output_tokens > 0


def gemini():
    pytest.importorskip("google.genai", reason="Gemini is optional: uv sync --extra gemini")
    from fitcheck.providers.gemini_provider import GeminiProvider
    return BudgetedProvider(GeminiProvider())  # through my spending limits, like every real call


def test_gemini_returns_structured_output():
    provider = gemini()
    result = provider.generate(system="You reply with JSON only.", user="Set `word` to the single word: ok", schema=Echo)
    print(f"\n{result.output!r}  {result.log}")
    assert result.output.word.strip().lower() == "ok"
    assert result.log.input_tokens > 0 and result.log.output_tokens > 0


def test_gemini_accepts_the_extraction_schema():
    # The real risk for M7: Gemini rejects a schema it finds too complex (400 INVALID_ARGUMENT).
    outcome = extract("Required: Experience with Python. Strong communication skills.",
                      gemini())
    print(f"\n{outcome.extraction!r}")
    assert outcome.extraction is not None, outcome.decision_reason
