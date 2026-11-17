from pathlib import Path

import pytest
from pydantic import ValidationError

from fitcheck.profile import load_profile

EXAMPLE = Path("profile.example.yaml")


def test_example_profile_loads():
    profile = load_profile(EXAMPLE)
    assert profile.expected_graduation == "2029-05"
    assert profile.location_filter.enabled is False  # E6: off by default


def test_location_filter_defaults_to_off(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text('expected_graduation: "2029-05"\n'
                    "work_authorization: {us_authorized: true, needs_sponsorship: false}\n")
    assert load_profile(path).location_filter.enabled is False


@pytest.mark.parametrize("bad", [
    'expected_graduation: "May 2029"\nwork_authorization: {us_authorized: true, needs_sponsorship: false}\n',
    'expected_graduation: "2029-05"\n',  # work authorization missing
    'expected_graduation: "2029-05"\nwork_authorization: {us_authorized: true, needs_sponsorship: false}\n'
    "location_filter: {remote_okk: true}\n",  # typo
])
def test_bad_profile_fails_loudly(tmp_path, bad):
    path = tmp_path / "profile.yaml"
    path.write_text(bad)
    with pytest.raises(ValidationError):
        load_profile(path)
