import json
from datetime import datetime, timedelta

import pytest

from fitcheck.providers import budget
from fitcheck.providers.base import CallLog, InvalidOutputError, Provider, ProviderResult
from fitcheck.providers.budget import BudgetedProvider, BudgetExceededError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.providers.pricing import cost_usd
from fitcheck.schemas import Extraction

VALID = {"is_job_posting": True, "has_section_split": False, "requirements": []}
NOON = datetime(2026, 10, 19, 12, 0)


class Clock:
    """A clock tests can move forward by hand."""

    def __init__(self, start):
        self.time = start

    def __call__(self):
        return self.time


class PricedProvider(Provider):
    """Returns a fixed token count on a real model name, so calls cost money in the log."""

    name = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self, input_tokens, output_tokens):
        self.tokens = (input_tokens, output_tokens)

    def generate(self, *, system, user, schema):
        log = CallLog("anthropic", self.model, *self.tokens, latency_s=0.0)
        return ProviderResult(output=schema.model_validate(VALID), log=log)


def call(provider):
    return provider.generate(system="s", user="u", schema=Extraction)


def test_cost_usd_uses_price_table():
    # Haiku: $1 per million input, $5 per million output.
    assert cost_usd("claude-haiku-4-5", 1_000_000, 200_000) == pytest.approx(2.00)


def test_unknown_model_price_raises():
    with pytest.raises(KeyError, match="pricing.py"):
        cost_usd("mystery-model", 10, 10)


def test_successful_call_is_logged(tmp_path):
    log = tmp_path / "usage.jsonl"
    provider = BudgetedProvider(PricedProvider(1000, 500), log_path=log, now=Clock(NOON))
    call(provider)
    record = json.loads(log.read_text())
    assert record["model"] == "claude-haiku-4-5"
    assert record["ok"] is True
    assert record["cost_usd"] == pytest.approx(cost_usd("claude-haiku-4-5", 1000, 500))


def test_failed_call_is_logged_and_reraised(tmp_path):
    log = tmp_path / "usage.jsonl"
    provider = BudgetedProvider(FakeProvider([InvalidOutputError("refused")]), log_path=log, now=Clock(NOON))
    with pytest.raises(InvalidOutputError):
        call(provider)
    assert json.loads(log.read_text())["ok"] is False


def test_failed_call_with_token_counts_is_priced(tmp_path):
    # The first eval run logged 6 failed-validation calls at $0 though they were billed.
    log = tmp_path / "usage.jsonl"
    billed = CallLog(provider="anthropic", model="claude-haiku-4-5", input_tokens=4000, output_tokens=800,
                     latency_s=1.0)
    provider = BudgetedProvider(FakeProvider([InvalidOutputError("failed validation", billed)]),
                                log_path=log, now=Clock(NOON))
    with pytest.raises(InvalidOutputError):
        call(provider)
    record = json.loads(log.read_text())
    assert record["ok"] is False
    assert record["cost_usd"] == pytest.approx(cost_usd("claude-haiku-4-5", 4000, 800))


def test_hourly_call_limit_blocks_and_then_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "MAX_CALLS_PER_HOUR", 2)
    clock = Clock(NOON)
    provider = BudgetedProvider(FakeProvider([VALID] * 3), log_path=tmp_path / "u.jsonl", now=clock)
    call(provider)
    call(provider)
    with pytest.raises(BudgetExceededError, match="Try again after 01:00 PM"):
        call(provider)
    clock.time = NOON + timedelta(hours=1, seconds=1)
    call(provider)  # the oldest calls have aged out of the window


def test_daily_dollar_limit_blocks_until_tomorrow(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "MAX_DOLLARS_PER_DAY", 0.01)
    clock = Clock(NOON)
    # 10,000 output tokens on Haiku = $0.05, over the $0.01 limit after one call.
    provider = BudgetedProvider(PricedProvider(0, 10_000), log_path=tmp_path / "u.jsonl", now=clock)
    call(provider)
    with pytest.raises(BudgetExceededError, match="tomorrow"):
        call(provider)
    clock.time = NOON + timedelta(days=1)
    call(provider)


def test_blocked_call_never_reaches_the_inner_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "MAX_CALLS_PER_HOUR", 1)
    inner = FakeProvider([VALID])
    provider = BudgetedProvider(inner, log_path=tmp_path / "u.jsonl", now=Clock(NOON))
    call(provider)
    with pytest.raises(BudgetExceededError):
        call(provider)
    assert len(inner.calls) == 1
