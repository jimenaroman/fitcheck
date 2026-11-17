"""The card (decision_rules.md section 6): headline, both percentages, labels and fixes, details."""

from fitcheck.card import render_card
from fitcheck.schemas import (
    EligibilityCheck, FitResult, NumberedRequirement, Requirement, RequirementResult, SkillResult,
)


def numbered(req_id, text, section="required", kind="skill", **fields):
    defaults = {"skills": ["python"], "type": "named_tool"} if kind == "skill" else {}
    return NumberedRequirement(id=req_id, requirement=Requirement(
        text=text, section=section, kind=kind, **(defaults | fields)))


REQS = [
    numbered("r1", "Experience with Python"),
    numbered("r2", "Experience with SQL", skills=["sql"]),
    numbered("r3", "Kubernetes", skills=["kubernetes"]),
    numbered("r4", "Linux a plus", section="preferred", skills=["linux"]),
    numbered("r5", "Teamwork", kind="soft"),
    numbered("r6", "US work authorization", kind="eligibility", eligibility_type="work_authorization"),
]


def req_result(req_id, status, skill=None, reason=None, evidence=None):
    skills = [SkillResult(requirement_id=req_id, skill=skill, status=status, reason_code=reason,
                          evidence_id=evidence)] if skill else []
    return RequirementResult(requirement_id=req_id, status=status, skills=skills)


RESULTS = [
    req_result("r1", "have", "python", evidence="proj-finance-b1"),
    req_result("r2", "weak", "sql", "listed_not_shown"),
    req_result("r3", "gap", "kubernetes", "not_on_resume"),
    req_result("r4", "gap", "linux", "not_on_resume"),
    req_result("r5", "not_scored"),
]
CHECK = EligibilityCheck(requirement_id="r6", eligibility_type="work_authorization", status="eligible")


def card(**fields):
    base = dict(decision="apply", required_total=3, required_have=1, required_weak=1, proven_pct=100 / 3,
                incl_weak_pct=200 / 3, eligibility="eligible", eligibility_checks=[CHECK], requirements=RESULTS)
    return render_card(FitResult(**(base | fields)), REQS)


def test_apply_card_shows_headline_and_both_percentages():
    text = card()
    assert text.startswith("APPLY\n")
    assert "33% proven, 67% incl. weak (1 of 3 required)" in text


def test_weak_and_gap_always_show_label_and_fix():
    text = card()
    assert "python: have via proj-finance-b1" in text
    assert "sql: weak · Listed, not shown → Add a bullet where you used it" in text
    assert "kubernetes: gap · Not on your resume" in text


def test_preferred_listed_separately_and_soft_in_details():
    text = card()
    assert text.index("Required:") < text.index("Preferred") < text.index("Details:")
    assert "Teamwork (not scored)" in text and "[work_authorization: eligible]" in text


def test_caught_false_match_is_shown_as_not_on_resume():
    results = RESULTS[:2] + [req_result("r3", "gap", "kubernetes", "false_match_caught")] + RESULTS[3:]
    assert "kubernetes: gap · Not on your resume" in card(requirements=results)


def test_no_split_shows_beside_the_score():
    assert "required) · Required/preferred split unclear" in card(flags=["no_split"])


def test_dont_apply_says_how_many_are_not_proven():
    assert card(decision="dont_apply").startswith("NOT YET: 2 required not proven")


def test_apply_with_unclear_eligibility_says_check_first():
    unclear = EligibilityCheck(requirement_id="r6", eligibility_type="work_authorization", status="unclear",
                               reason="check your work authorization")
    text = card(eligibility="unclear", eligibility_checks=[unclear])
    assert text.startswith("APPLY, check eligibility first: check your work authorization")


def test_filtered_out_and_next_year_headlines():
    out = EligibilityCheck(requirement_id="r6", eligibility_type="location", status="filtered_out", reason="Boston")
    assert card(eligibility="filtered_out", eligibility_checks=[out]).startswith("FILTERED OUT: Boston")
    nxt = EligibilityCheck(requirement_id="r6", eligibility_type="graduation", status="next_year",
                           reason="window ends 2027-06, before your 2029-05")
    assert card(eligibility="next_year", eligibility_checks=[nxt]).startswith("NEXT YEAR: window ends 2027-06")


def test_review_manually_card():
    text = render_card(FitResult(decision="review_manually", decision_reason="not_a_posting",
                                 eligibility="unclear"), [])
    assert text == "REVIEW MANUALLY: this doesn't look like a job posting"
