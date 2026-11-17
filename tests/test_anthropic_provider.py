"""Offline tests: a stand-in client replaces the real API, so these cost nothing."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from fitcheck.providers.anthropic_provider import AnthropicProvider
from fitcheck.providers.base import InvalidOutputError


class Echo(BaseModel):
    word: str


class StubClient:
    """Mimics client.messages.create(...): returns a canned response or raises a canned error."""

    def __init__(self, response=None, error=None):
        self.messages = self
        self._response, self._error = response, error
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self._error:
            raise self._error
        return self._response


def make_response(stop_reason="end_turn", text='{"word": "ok"}'):
    usage = SimpleNamespace(input_tokens=12, output_tokens=5)
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


def test_success_returns_output_and_log():
    client = StubClient(make_response())
    result = AnthropicProvider(client=client).generate(system="s", user="u", schema=Echo)
    assert result.output.word == "ok"
    assert (result.log.model, result.log.input_tokens, result.log.output_tokens) == ("claude-haiku-4-5", 12, 5)


def test_sends_schema_system_and_user_text():
    client = StubClient(make_response())
    AnthropicProvider(client=client).generate(system="rules", user="posting", schema=Echo)
    fmt = client.kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["properties"]["word"]["type"] == "string"
    assert client.kwargs["system"] == "rules"
    assert client.kwargs["messages"] == [{"role": "user", "content": "posting"}]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_and_truncation_raise_invalid_output(stop_reason):
    client = StubClient(make_response(stop_reason=stop_reason))
    with pytest.raises(InvalidOutputError):
        AnthropicProvider(client=client).generate(system="s", user="u", schema=Echo)


def test_missing_output_raises_invalid_output():
    client = StubClient(make_response(text=None))
    with pytest.raises(InvalidOutputError):
        AnthropicProvider(client=client).generate(system="s", user="u", schema=Echo)


def test_validation_error_becomes_invalid_output_with_token_counts():
    # The reply was billed even though it's unusable, so the error carries its tokens.
    client = StubClient(make_response(text='{"wrong": 1}'))
    with pytest.raises(InvalidOutputError, match="validation") as caught:
        AnthropicProvider(client=client).generate(system="s", user="u", schema=Echo)
    assert (caught.value.log.input_tokens, caught.value.log.output_tokens) == (12, 5)
