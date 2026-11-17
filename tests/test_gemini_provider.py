"""Offline tests: a stand-in client replaces the real API, so these cost nothing."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("google.genai", reason="Gemini is optional: uv sync --extra gemini")

from fitcheck.providers.base import InvalidOutputError  # noqa: E402 (after the skip check)
from fitcheck.providers.gemini_provider import GeminiProvider  # noqa: E402
from fitcheck.schemas import Extraction  # noqa: E402


class Echo(BaseModel):
    word: str


class StubClient:
    """Mimics client.interactions.create(...): returns a canned interaction."""

    def __init__(self, interaction):
        self.interactions = self
        self._interaction = interaction
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self._interaction


def make_interaction(status="completed", text='{"word": "ok"}', thoughts=0):
    usage = SimpleNamespace(total_input_tokens=12, total_output_tokens=5, total_thought_tokens=thoughts)
    return SimpleNamespace(status=status, output_text=text, usage=usage)


def generate(interaction, schema=Echo):
    client = StubClient(interaction)
    return client, GeminiProvider(client=client).generate(system="rules", user="posting", schema=schema)


def test_success_returns_output_and_log():
    _, result = generate(make_interaction())
    assert result.output.word == "ok"
    assert (result.log.provider, result.log.model) == ("gemini", "gemini-3.8-flash")
    assert (result.log.input_tokens, result.log.output_tokens) == (12, 5)


def test_thought_tokens_are_billed_as_output():
    _, result = generate(make_interaction(thoughts=7))
    assert result.log.output_tokens == 12


def test_sends_schema_system_and_user_text_and_is_not_stored():
    client, _ = generate(make_interaction())
    fmt = client.kwargs["response_format"]
    assert fmt["mime_type"] == "application/json" and fmt["schema"]["properties"]["word"]["type"] == "string"
    assert client.kwargs["system_instruction"] == "rules" and client.kwargs["input"] == "posting"
    assert client.kwargs["store"] is False
    assert client.kwargs["generation_config"]["thinking_level"] == "low"


def test_sends_the_extraction_schema_with_every_key_required():
    # The same per-kind shapes Claude gets: Gemini supports anyOf, enum, minItems, and $ref.
    client, _ = generate(make_interaction(text='{"is_job_posting": false, "has_section_split": false, '
                                                  '"requirements": []}'), schema=Extraction)
    sent = client.kwargs["response_format"]["schema"]
    assert sent == Extraction.model_json_schema()
    assert sent["$defs"]["SkillItem"]["properties"]["skills"]["minItems"] == 1


@pytest.mark.parametrize("status", ["incomplete", "failed", "budget_exceeded"])
def test_unfinished_interactions_raise_invalid_output_with_token_counts(status):
    with pytest.raises(InvalidOutputError) as caught:
        generate(make_interaction(status=status))
    assert caught.value.log.input_tokens == 12


@pytest.mark.parametrize("text", [None, ""])
def test_missing_output_raises_invalid_output(text):
    with pytest.raises(InvalidOutputError, match="returned no Echo"):
        generate(make_interaction(text=text))


def test_validation_error_becomes_invalid_output_with_token_counts():
    with pytest.raises(InvalidOutputError, match="validation") as caught:
        generate(make_interaction(text='{"wrong": 1}'))
    assert (caught.value.log.input_tokens, caught.value.log.output_tokens) == (12, 5)


def test_missing_usage_counts_as_zero_tokens():
    interaction = make_interaction()
    interaction.usage = None
    _, result = generate(interaction)
    assert (result.log.input_tokens, result.log.output_tokens) == (0, 0)
