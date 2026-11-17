"""Claude via the official Anthropic SDK. Defaults to Haiku to keep costs low."""

import time

import anthropic
from anthropic import transform_schema
from pydantic import ValidationError

from fitcheck.config import load_env, require
from fitcheck.providers.base import CallLog, InvalidOutputError, Provider, ProviderResult, T

DEFAULT_MODEL = "claude-haiku-4-5"


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000, client=None):
        # `client` lets tests pass a stand-in so they never hit the network.
        if client is None:
            load_env()
            client = anthropic.Anthropic(api_key=require("ANTHROPIC_API_KEY"))
        self.client = client
        self.model = model
        self.max_tokens = max_tokens

    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        start = time.perf_counter()
        # create() + our own validation instead of parse(): same structured-output request, but a
        # reply that fails validation still gives us its token counts, so the budget can price it.
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": transform_schema(schema.model_json_schema())}},
        )
        latency = time.perf_counter() - start
        log = CallLog(
            provider=self.name,
            model=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_s=round(latency, 3),
        )

        if response.stop_reason == "refusal":
            raise InvalidOutputError(f"{self.model} refused the request", log)
        if response.stop_reason == "max_tokens":
            raise InvalidOutputError(f"{self.model} hit max_tokens={self.max_tokens}; output is incomplete", log)
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise InvalidOutputError(f"{self.model} returned no {schema.__name__}", log)
        try:
            output = schema.model_validate_json(text)
        except ValidationError as e:
            raise InvalidOutputError(f"{self.model} output failed {schema.__name__} validation: {e}", log) from e
        return ProviderResult(output=output, log=log)
