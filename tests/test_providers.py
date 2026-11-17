import pytest

from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.schemas import Extraction

VALID = {"is_job_posting": True, "has_section_split": False, "requirements": []}


def test_fake_returns_validated_object():
    provider = FakeProvider([VALID])
    result = provider.generate(system="sys", user="posting", schema=Extraction)
    assert isinstance(result.output, Extraction)
    assert result.output.is_job_posting is True
    assert result.log.provider == "fake"


def test_fake_records_what_was_sent():
    provider = FakeProvider([VALID])
    provider.generate(system="sys", user="posting text", schema=Extraction)
    assert provider.calls[0].user == "posting text"
    assert provider.calls[0].schema is Extraction


def test_bad_output_raises_invalid_output():
    # A model that sneaks in a status must be rejected (extra="forbid" in schemas.py).
    provider = FakeProvider([{**VALID, "status": "have"}])
    with pytest.raises(InvalidOutputError):
        provider.generate(system="sys", user="posting", schema=Extraction)


def test_responses_are_used_in_order():
    bad = {"is_job_posting": "maybe"}
    provider = FakeProvider([bad, VALID])
    with pytest.raises(InvalidOutputError):
        provider.generate(system="s", user="u", schema=Extraction)
    assert provider.generate(system="s", user="u", schema=Extraction).output.is_job_posting


def test_canned_exception_is_raised():
    provider = FakeProvider([InvalidOutputError("refused")])
    with pytest.raises(InvalidOutputError, match="refused"):
        provider.generate(system="s", user="u", schema=Extraction)


def test_running_out_of_responses_fails_loudly():
    provider = FakeProvider([])
    with pytest.raises(AssertionError):
        provider.generate(system="s", user="u", schema=Extraction)
