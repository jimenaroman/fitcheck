"""A wrapper that saves successful answers so an identical request never calls the API twice.

The cache key is a hash of everything that affects the answer: model, schema, system prompt,
and user text. Editing the prompt or schema changes the key, so stale answers are never reused.
Answers are saved as files by default, or in the database for the API (so a deploy can't wipe them).
Wrap it outside the budget guard, so cache hits don't count toward my limits:

    CachedProvider(BudgetedProvider(AnthropicProvider()))
"""

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

from pydantic import ValidationError

from fitcheck.providers.base import CallLog, Provider, ProviderResult, T
from fitcheck.providers.storage import AnswerCache, FileCache

DEFAULT_DIR = Path(".fitcheck/cache")


class CachedProvider(Provider):
    def __init__(self, inner: Provider, cache_dir: Path = DEFAULT_DIR, store: AnswerCache | None = None):
        # `store` replaces the file cache (the API keeps answers in the database).
        self.inner = inner
        self.name = inner.name
        self.model = getattr(inner, "model", inner.name)
        self.store = store or FileCache(cache_dir)

    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        key = self._key(system, user, schema)
        hit = self._load(self.store.get(key), schema)
        if hit is not None:
            return hit

        result = self.inner.generate(system=system, user=user, schema=schema)  # failures aren't cached
        self.store.put(key, {"output": result.output.model_dump(mode="json"), "log": asdict(result.log)})
        return result

    def _key(self, system: str, user: str, schema: type[T]) -> str:
        parts = {
            "model": self.model,
            "schema": schema.model_json_schema(),
            "system": system,
            "user": user,
        }
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()

    def _load(self, saved: dict | None, schema: type[T]) -> ProviderResult[T] | None:
        if saved is None:
            return None
        try:
            output = schema.model_validate(saved["output"])
        except (KeyError, TypeError, ValidationError):
            return None  # unreadable entry: treat as a miss and overwrite it
        # A hit costs nothing now, but keeps the original call's tokens and latency (for evals).
        log = replace(CallLog(**saved["log"]), cached=True)
        return ProviderResult(output=output, log=log)
