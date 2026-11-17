import pytest

from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.cache import CachedProvider
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.schemas import Extraction

VALID = {"is_job_posting": True, "has_section_split": False, "requirements": []}
OTHER = {"is_job_posting": False, "has_section_split": False, "requirements": []}


def call(provider, system="sys", user="posting"):
    return provider.generate(system=system, user=user, schema=Extraction)


def test_identical_request_is_served_from_cache(tmp_path):
    inner = FakeProvider([VALID])  # only one canned answer: a second real call would fail
    provider = CachedProvider(inner, cache_dir=tmp_path)
    first = call(provider)
    second = call(provider)
    assert len(inner.calls) == 1
    assert second.output == first.output
    assert second.log.cached is True and first.log.cached is False
    assert second.log.input_tokens == first.log.input_tokens  # original cost kept for evals


def test_different_posting_misses(tmp_path):
    inner = FakeProvider([VALID, OTHER])
    provider = CachedProvider(inner, cache_dir=tmp_path)
    call(provider, user="posting A")
    assert call(provider, user="posting B").output.is_job_posting is False
    assert len(inner.calls) == 2


def test_edited_prompt_misses(tmp_path):
    inner = FakeProvider([VALID, OTHER])
    provider = CachedProvider(inner, cache_dir=tmp_path)
    call(provider, system="prompt v1")
    call(provider, system="prompt v2")
    assert len(inner.calls) == 2


def test_failures_are_not_cached(tmp_path):
    inner = FakeProvider([InvalidOutputError("bad"), VALID])
    provider = CachedProvider(inner, cache_dir=tmp_path)
    with pytest.raises(InvalidOutputError):
        call(provider)
    assert call(provider).output.is_job_posting is True  # retried for real, not a cached failure
    assert len(inner.calls) == 2


def test_corrupt_cache_entry_is_treated_as_miss(tmp_path):
    inner = FakeProvider([VALID, VALID])
    provider = CachedProvider(inner, cache_dir=tmp_path)
    call(provider)
    for entry in tmp_path.iterdir():
        entry.write_text("not json")
    call(provider)
    assert len(inner.calls) == 2
