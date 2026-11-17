"""Gemini Flash via Google's official SDK (google-genai, Interactions API). The M7 comparison model."""

import time

from google import genai
from pydantic import ValidationError

from fitcheck.config import load_env, require
from fitcheck.providers.base import CallLog, InvalidOutputError, Provider, ProviderResult, T

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000, thinking_level: str = "low",
                 client=None):
        # `client` lets tests pass a stand-in so they never hit the network.
        if client is None:
            load_env()
            client = genai.Client(api_key=require("GEMINI_API_KEY"))
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        # Haiku runs without extended thinking, so Gemini thinks as little as it can: a fairer
        # comparison, and thought tokens are billed as output. ("minimal" is rejected by this model.)
        self.thinking_level = thinking_level

    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        start = time.perf_counter()
        interaction = self.client.interactions.create(
            model=self.model,
            system_instruction=system,
            input=user,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema.model_json_schema()},
            generation_config={"max_output_tokens": self.max_tokens, "thinking_level": self.thinking_level},
            store=False,  # don't keep the posting and resume on Google's servers after the call
        )
        latency = time.perf_counter() - start
        usage = interaction.usage
        log = CallLog(
            provider=self.name,
            model=self.model,
            input_tokens=(usage.total_input_tokens or 0) if usage else 0,
            # Thought tokens are counted separately but billed as output, so both are priced.
            output_tokens=((usage.total_output_tokens or 0) + (usage.total_thought_tokens or 0)) if usage else 0,
            latency_s=round(latency, 3),
        )

        if interaction.status == "incomplete":
            raise InvalidOutputError(f"{self.model} hit max_output_tokens={self.max_tokens}; output is incomplete", log)
        if interaction.status != "completed":
            raise InvalidOutputError(f"{self.model} ended with status {interaction.status!r}", log)
        if not interaction.output_text:
            # A safety block or refusal completes with no text.
            raise InvalidOutputError(f"{self.model} returned no {schema.__name__}", log)
        try:
            output = schema.model_validate_json(interaction.output_text)
        except ValidationError as e:
            raise InvalidOutputError(f"{self.model} output failed {schema.__name__} validation: {e}", log) from e
        return ProviderResult(output=output, log=log)
