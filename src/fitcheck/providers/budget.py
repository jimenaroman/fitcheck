"""A wrapper that stops model calls once my own spending limits are reached.

BudgetedProvider follows the same Provider interface as the provider it wraps, so the rest of
the pipeline can't tell the difference. Every call attempt is appended to a usage log: a local JSONL
file by default, or the database for the API (so a deploy can't reset my limits).
"""

from datetime import datetime, timedelta
from pathlib import Path

from fitcheck.providers.base import Provider, ProviderResult, T
from fitcheck.providers.pricing import cost_usd
from fitcheck.providers.storage import FileUsageLog, UsageLog

MAX_CALLS_PER_HOUR = 20
MAX_DOLLARS_PER_DAY = 0.50
DEFAULT_LOG = Path(".fitcheck/usage.jsonl")


class BudgetExceededError(RuntimeError):
    """My own limit was reached. Not a model failure, so it never counts toward P1."""


class BudgetedProvider(Provider):
    def __init__(self, inner: Provider, log_path: Path = DEFAULT_LOG, now=datetime.now, usage: UsageLog | None = None):
        # `now` is swappable so tests can control the clock. `usage` replaces the file log (the API).
        self.inner = inner
        self.name = inner.name
        self.model = getattr(inner, "model", inner.name)
        self.usage = usage or FileUsageLog(log_path)
        self.now = now

    def generate(self, *, system: str, user: str, schema: type[T]) -> ProviderResult[T]:
        self._check_limits()
        model = self.model
        cost_usd(model, 0, 0)  # fail before calling if the model has no price
        try:
            result = self.inner.generate(system=system, user=user, schema=schema)
        except Exception as e:
            # A failed call is still billed when the model answered (e.g. failed validation): log
            # its tokens. Only failures with no reply at all (network errors) are logged as $0.
            failed = getattr(e, "log", None)
            self._append(model=failed.model if failed else model,
                         input_tokens=failed.input_tokens if failed else 0,
                         output_tokens=failed.output_tokens if failed else 0, ok=False)
            raise
        log = result.log
        self._append(model=log.model, input_tokens=log.input_tokens, output_tokens=log.output_tokens, ok=True)
        return result

    def _check_limits(self) -> None:
        now = self.now()
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        records = self.usage.since(min(start_of_today, now - timedelta(hours=1)))
        last_hour = [r for r in records if r["time"] > now - timedelta(hours=1)]
        if len(last_hour) >= MAX_CALLS_PER_HOUR:
            retry_at = min(r["time"] for r in last_hour) + timedelta(hours=1)
            raise BudgetExceededError(
                f"Limit reached: {len(last_hour)} calls in the last hour. "
                f"Try again after {retry_at:%I:%M %p}."
            )
        spent_today = sum(r["cost_usd"] for r in records if r["time"].date() == now.date())
        if spent_today >= MAX_DOLLARS_PER_DAY:
            raise BudgetExceededError(
                f"Limit reached: ${spent_today:.2f} spent today (limit ${MAX_DOLLARS_PER_DAY:.2f}). "
                "Try again tomorrow."
            )

    def _append(self, *, model: str, input_tokens: int, output_tokens: int, ok: bool) -> None:
        self.usage.append({
            "time": self.now().replace(microsecond=0),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd(model, input_tokens, output_tokens),
            "ok": ok,
        })
