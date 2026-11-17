"""The budget guard and the cache behave the same on files (CLI) and in the database (API)."""

from datetime import timedelta

import pytest

from fitcheck.db import connect
from fitcheck.providers.base import CallLog, InvalidOutputError
from fitcheck.providers.budget import MAX_CALLS_PER_HOUR, BudgetedProvider, BudgetExceededError
from fitcheck.providers.cache import CachedProvider
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.providers.storage import FileCache, FileUsageLog, SqlCache, SqlUsageLog
from test_budget import NOON, VALID, Clock, PricedProvider, call  # pytest puts tests/ on the import path


@pytest.fixture(params=["file", "database"])
def stores(request, tmp_path):
    """(usage log, answer cache) of one kind. Built by a function, so a test can 'restart' them."""
    if request.param == "file":
        return lambda: (FileUsageLog(tmp_path / "usage.jsonl"), FileCache(tmp_path / "cache"))
    url = f"sqlite:///{tmp_path}/fitcheck.db"
    return lambda: (SqlUsageLog(connect(url)), SqlCache(connect(url)))


def test_hourly_limit(stores):
    usage, _ = stores()
    provider = BudgetedProvider(FakeProvider([VALID] * MAX_CALLS_PER_HOUR), usage=usage, now=Clock(NOON))
    for _ in range(MAX_CALLS_PER_HOUR):
        call(provider)
    with pytest.raises(BudgetExceededError, match="calls in the last hour"):
        call(provider)


def test_daily_dollar_limit_and_the_next_day(stores):
    usage, _ = stores()
    clock = Clock(NOON)
    provider = BudgetedProvider(PricedProvider(0, 100_000), usage=usage, now=clock)  # $0.50 per call
    call(provider)
    clock.time += timedelta(hours=2)
    with pytest.raises(BudgetExceededError, match="spent today"):
        call(provider)
    clock.time += timedelta(days=1)
    call(provider)  # a new day


def test_a_billed_failure_is_priced(stores):
    usage, _ = stores()
    failure = InvalidOutputError("bad", log=CallLog("anthropic", "claude-haiku-4-5", 1000, 500, latency_s=0.0))
    provider = BudgetedProvider(FakeProvider([failure]), usage=usage, now=Clock(NOON))
    with pytest.raises(InvalidOutputError):
        call(provider)
    (record,) = usage.since(NOON - timedelta(hours=1))
    assert record["ok"] is False and record["cost_usd"] > 0


def test_limits_survive_a_restart(stores):
    # The reason for step 3: a new server process must still see today's spending.
    usage, _ = stores()
    call(BudgetedProvider(PricedProvider(0, 100_000), usage=usage, now=Clock(NOON)))
    usage_after_restart, _ = stores()
    with pytest.raises(BudgetExceededError, match="spent today"):
        call(BudgetedProvider(PricedProvider(0, 100_000), usage=usage_after_restart, now=Clock(NOON)))


def test_cache_answers_a_repeat_even_after_a_restart(stores):
    _, cache = stores()
    first = call(CachedProvider(FakeProvider([VALID]), store=cache))
    _, cache_after_restart = stores()
    inner = FakeProvider([])  # no canned answers: a real call would fail the test
    second = call(CachedProvider(inner, store=cache_after_restart))
    assert inner.calls == [] and second.output == first.output and second.log.cached is True


def test_failures_are_not_cached(stores):
    _, cache = stores()
    provider = CachedProvider(FakeProvider([InvalidOutputError("bad"), VALID]), store=cache)
    with pytest.raises(InvalidOutputError):
        call(provider)
    assert call(provider).log.cached is False


def test_the_api_provider_chain_keeps_usage_and_cache_in_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")  # a dummy: nothing is called
    from fitcheck.cli import default_provider
    provider = default_provider("claude", connect(f"sqlite:///{tmp_path}/fitcheck.db"))
    assert isinstance(provider.store, SqlCache) and isinstance(provider.inner.usage, SqlUsageLog)
