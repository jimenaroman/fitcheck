"""Stage 4 of score.py: eligibility (decision_rules.md section 4 and the section 8 eligibility rows)."""

import pytest

from fitcheck.profile import Profile
from fitcheck.schemas import Eligibility, EligibilityCheck, NumberedRequirement, Requirement
from fitcheck.score import check_eligibility, combine_eligibility

ELIGIBLE, UNCLEAR, NEXT_YEAR, FILTERED = (
    Eligibility.ELIGIBLE, Eligibility.UNCLEAR, Eligibility.NEXT_YEAR, Eligibility.FILTERED_OUT)


def profile(filter_on=False, remote_ok=True, authorized=True, sponsorship=False, early="2028-12"):
    return Profile.model_validate({
        "expected_graduation": "2029-05",
        "early_graduation": early,
        "work_authorization": {"us_authorized": authorized, "needs_sponsorship": sponsorship},
        "location_filter": {"enabled": filter_on, "metros": ["chicago", "nyc", "sf", "dallas_plano"],
                            "remote_ok": remote_ok},
    })


def item(eligibility_type, text="x", **fields):
    return NumberedRequirement(id="e1", requirement=Requirement(
        text=text, section="required", kind="eligibility", eligibility_type=eligibility_type, **fields))


def status(numbered, prof=None):
    return check_eligibility(numbered, prof or profile()).status


def grad(earliest, latest, text="Graduating ..."):
    return item("graduation", text, grad_earliest=earliest, grad_latest=latest)


# ---------- E1 graduation window ----------

def test_expected_date_inside_window_is_eligible():  # "Graduating Dec 2028 – Aug 2029"
    assert status(grad("2028-12", "2029-08")) == ELIGIBLE


def test_whole_year_window_is_eligible():  # "Graduating in 2029"
    assert status(grad("2029-01", "2029-12")) == ELIGIBLE


def test_only_early_date_inside_is_unclear():  # "Graduating Sep 2028 – Mar 2029" (section 8)
    check = check_eligibility(grad("2028-09", "2029-03"), profile())
    assert check.status == UNCLEAR and "2028-12" in check.reason


def test_window_ending_before_expected_date_is_next_year():  # "Graduating Dec 2026 – Jun 2027"
    assert status(grad("2026-12", "2027-06")) == NEXT_YEAR


def test_window_starting_after_expected_date_is_filtered_out():
    assert status(grad("2030-01", "2030-06")) == FILTERED


def test_open_ended_bounds():
    assert status(grad(None, "2029-06")) == ELIGIBLE  # "graduating by June 2029"
    assert status(grad("2029-01", None)) == ELIGIBLE


def test_unreadable_graduation_dates_are_unclear_with_the_quote():  # "Graduating Summer 2029"
    check = check_eligibility(grad(None, None, "Graduating Summer 2029"), profile())
    assert check.status == UNCLEAR and "Graduating Summer 2029" in check.reason


def test_no_early_date_in_profile_skips_that_rule():
    assert status(grad("2028-09", "2028-12"), profile(early=None)) == NEXT_YEAR


# ---------- E2 class year (v1: not interpreted yet) ----------

def test_class_year_is_unclear_for_now():  # "Class of 2029"
    assert status(item("class_year", "Class of 2029")) == UNCLEAR


# ---------- E3 degree ----------

@pytest.mark.parametrize("text, expected", [
    ("Pursuing a BS in CS or related field", ELIGIBLE),
    ("Bachelor's degree or equivalent experience", ELIGIBLE),
    ("Open to MS and PhD students only", FILTERED),
    ("BS, MS, or PhD in Computer Science", ELIGIBLE),
])
def test_degree(text, expected):
    assert status(item("degree", text)) == expected


# ---------- E4 GPA and E8 other: details only ----------

def test_gpa_minimum_is_ignored():  # "Minimum GPA 3.7"
    assert status(item("gpa", "Minimum GPA 3.7")) is None


def test_other_is_shown_only():  # "Must be able to lift 25 lbs"
    assert status(item("other", "Must be able to lift 25 lbs")) is None


# ---------- E5 work authorization ----------

def test_authorized_without_sponsorship_passes_no_sponsorship_wording():
    assert status(item("work_authorization", "We do not offer visa sponsorship")) == ELIGIBLE


def test_needing_sponsorship_is_unclear_not_filtered():
    assert status(item("work_authorization", "US work authorization required"), profile(sponsorship=True)) == UNCLEAR


# ---------- E6 location ----------

def loc(*places):
    return item("location", "Located in ...", locations=list(places))


def test_location_filter_off_ignores_locations_completely():  # "Located in Boston," filter off
    assert status(loc("Boston")) is None


def test_unlisted_city_is_filtered_out_with_the_city_as_reason():  # filter on
    check = check_eligibility(loc("Boston"), profile(filter_on=True))
    assert check.status == FILTERED and "Boston" in check.reason


def test_any_listed_metro_or_remote_is_eligible():  # "New York, Seattle, or Remote"
    assert status(loc("New York", "Seattle", "Remote"), profile(filter_on=True)) == ELIGIBLE


def test_metro_alias_with_state():  # "Plano, TX" -> dallas_plano
    assert status(loc("Plano, TX"), profile(filter_on=True)) == ELIGIBLE


def test_remote_only_counts_when_remote_ok():
    assert status(loc("Remote"), profile(filter_on=True, remote_ok=False)) == FILTERED


def test_unrecognized_location_is_unclear():
    assert status(loc("Springfield"), profile(filter_on=True)) == UNCLEAR


def test_location_item_without_places_is_eligible():
    assert status(loc(), profile(filter_on=True)) == ELIGIBLE


# ---------- E7 combine ----------

def check(s):
    return EligibilityCheck(requirement_id="e1", eligibility_type="other", status=s)


@pytest.mark.parametrize("statuses, expected", [
    ([], ELIGIBLE),                                   # no eligibility items: nothing to check
    ([ELIGIBLE, None], ELIGIBLE),                     # details-only checks don't count
    ([ELIGIBLE, UNCLEAR], UNCLEAR),
    ([UNCLEAR, NEXT_YEAR], NEXT_YEAR),
    ([NEXT_YEAR, FILTERED, UNCLEAR], FILTERED),       # any filtered_out wins
])
def test_combine_most_severe_wins(statuses, expected):
    assert combine_eligibility([check(s) for s in statuses]) == expected
