"""A provider that replays canned answers. Free, offline, and deterministic, for tests."""

from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from fitcheck.providers.base import CallLog, InvalidOutputError, Provider, ProviderResult, T


@dataclass(frozen=True)
class SentCall:
    """One call the fake received, so tests can check what the pipeline sent."""

    system: str
    user: str
    schema: type[BaseModel]


class FakeProvider(Provider):
    name = "fake"

    def __init__(self, responses: list[dict | BaseModel | Exception]):
        # Each call uses the next response. A dict is validated against the schema like real
        # model JSON would be; an Exception is raised, to simulate failures.
        self._responses = list(responses)
        self.calls: list[SentCall] = []

    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        self.calls.append(SentCall(system=system, user=user, schema=schema))
        if not self._responses:
            raise AssertionError("FakeProvider ran out of canned responses")
        response = self._responses.pop(0)

        if isinstance(response, Exception):
            raise response
        try:
            data = response.model_dump() if isinstance(response, BaseModel) else response
            output = schema.model_validate(data)
        except ValidationError as e:
            raise InvalidOutputError(f"fake output failed {schema.__name__} validation: {e}") from e

        log = CallLog(provider=self.name, model="fake", input_tokens=0, output_tokens=0, latency_s=0.0)
        return ProviderResult(output=output, log=log)
