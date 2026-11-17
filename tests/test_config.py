import pytest

from fitcheck.config import MissingKeyError, require


def test_require_returns_value(monkeypatch):
    monkeypatch.setenv("FITCHECK_TEST_KEY", "abc123")
    assert require("FITCHECK_TEST_KEY") == "abc123"


def test_require_raises_when_absent(monkeypatch):
    monkeypatch.delenv("FITCHECK_TEST_KEY", raising=False)
    with pytest.raises(MissingKeyError, match="FITCHECK_TEST_KEY"):
        require("FITCHECK_TEST_KEY")
