"""Stage 3 of score.py: score() puts it together (P1-P5, P8, statuses, eligibility) into a FitResult."""

import pytest

from fitcheck.extract import ExtractionOutcome
from fitcheck.match import MatchOutcome
from fitcheck.profile import Profile
from fitcheck.schemas import (
    Decision, DecisionReason, Eligibility, Extraction, Flag, NumberedRequirement, ReasonCode, Requirement,
    SkillMatch, Status,
)
from fitcheck.score import score
from fitcheck.verify import Verification, VerifiedMatch

PROFILE = Profile.model_validate({
    "expected_graduation": "2029-05",
    "work_authorization": {"us_authorized": True, "needs_sponsorship": False},
})


def skill_req(text, skill, section="required"):
    return Requirement(text=text, section=section, kind="skill", skills=[skill], type="hands_on")


# A small Codistry-like posting: 3 required skills, 1 preferred, 1 soft, 1 eligibility item.
REQS = [
    skill_req("Experience writing software", "software development"),
    skill_req("deploying to servers", "server deployment"),
    skill_req("using AI coding tools", "ai coding tools"),
    skill_req("ideally Linux", "linux", section="preferred"),
    Requirement(text="Strong communication", section="required", kind="soft"),
    Requirement(text="US work authorization required", section="required", kind="eligibility",
                eligibility_type="work_authorization"),
]
NUMBERED = [NumberedRequirement(id=f"r{i}", requirement=r) for i, r in enumerate(REQS, start=1)]


def extraction(split=True, is_posting=True, requirements=NUMBERED):
    # score() reads the numbered requirements, not the model's raw items, so those stay empty here.
    ext = Extraction(is_job_posting=is_posting, has_section_split=split, requirements=[])
    return ExtractionOutcome(ext, requirements, None, posting_chars=1000)


def have(req_id, skill):
    m = SkillMatch(requirement_id=req_id, skill=skill, evidence_id="proj-finance-b1",
                   quote="Built a Streamlit budget app", relation="exact", reason="t")
    return VerifiedMatch(m, "bullet")


def gap(req_id, skill):
    m = SkillMatch(requirement_id=req_id, skill=skill, relation="none", reason="t")
    return VerifiedMatch(m, None)


def caught(req_id, skill):
    m = SkillMatch(requirement_id=req_id, skill=skill, evidence_id="proj-finance-b2",
                   quote="Built a React app", relation="exact", reason="t")
    return VerifiedMatch(m, "bullet", caught="R5: quote not found in proj-finance-b2")


def run(verified, ext=None, match_failed=False, profile=PROFILE):
    matched = MatchOutcome([v.match for v in verified],
                           DecisionReason.INVALID_OUTPUT if match_failed else None)
    return score(ext or extraction(), matched, Verification(verified), profile)


GOOD = [have("r1", "software development"), gap("r2", "server deployment"),
        gap("r3", "ai coding tools"), gap("r4", "linux")]


# ---------- The normal path ----------

def test_one_have_two_gaps_applies_with_both_percentages():
    result = run(GOOD)
    assert result.decision == Decision.APPLY  # 3 required, 1 have, 2 missed (P6)
    assert (result.required_total, result.required_have, result.required_weak) == (3, 1, 0)
    assert result.proven_pct == pytest.approx(100 / 3)
    assert result.eligibility == Eligibility.ELIGIBLE and result.flags == []


def test_results_cover_skill_and_soft_items_but_not_eligibility():
    result = run(GOOD)
    statuses = {r.requirement_id: r.status for r in result.requirements}
    assert statuses == {"r1": Status.HAVE, "r2": Status.GAP, "r3": Status.GAP, "r4": Status.GAP,
                        "r5": Status.NOT_SCORED}
    assert [c.requirement_id for c in result.eligibility_checks] == ["r6"]


def test_preferred_gap_never_changes_the_decision():  # P8: r4 linux is a gap either way
    assert run(GOOD).decision == run(GOOD[:3] + [have("r4", "linux")]).decision == Decision.APPLY


def test_caught_citation_becomes_a_gap_with_its_reason():
    result = run([have("r1", "software development"), caught("r2", "server deployment"),
                  have("r3", "ai coding tools"), gap("r4", "linux")])
    r2 = next(r for r in result.requirements if r.requirement_id == "r2")
    assert r2.status == Status.GAP and r2.skills[0].reason_code == ReasonCode.FALSE_MATCH_CAUGHT
    assert result.decision == Decision.APPLY  # 1 of 3 citations caught: not P3


# ---------- P1-P3: review manually ----------

def test_failed_extraction_is_review_manually_invalid_output():  # P1
    failed = ExtractionOutcome(None, [], DecisionReason.INVALID_OUTPUT, posting_chars=1000)
    result = score(failed, MatchOutcome([], None), Verification([]), PROFILE)
    assert (result.decision, result.decision_reason) == (Decision.REVIEW_MANUALLY, DecisionReason.INVALID_OUTPUT)
    assert result.eligibility == Eligibility.UNCLEAR  # nothing was read, so nothing is known


def test_failed_matching_is_review_manually_but_keeps_eligibility():  # P1
    result = run([], match_failed=True)
    assert result.decision_reason == DecisionReason.INVALID_OUTPUT
    assert result.eligibility == Eligibility.ELIGIBLE and len(result.eligibility_checks) == 1


def test_not_a_posting_is_review_manually():  # P2
    result = run([], ext=extraction(is_posting=False, requirements=[]))
    assert (result.decision, result.decision_reason) == (Decision.REVIEW_MANUALLY, DecisionReason.NOT_A_POSTING)


def test_more_than_half_caught_is_unreliable_matching():  # P3: 2 of 3 citations caught
    result = run([have("r1", "software development"), caught("r2", "server deployment"),
                  caught("r3", "ai coding tools"), gap("r4", "linux")])
    assert (result.decision, result.decision_reason) == (
        Decision.REVIEW_MANUALLY, DecisionReason.UNRELIABLE_MATCHING)
    assert result.required_total == 3  # the counts are still filled in, for details and evals


def test_exactly_half_caught_is_not_unreliable():  # P3 says "more than half"
    result = run([have("r1", "software development"), caught("r2", "server deployment"),
                  gap("r3", "ai coding tools"), gap("r4", "linux")])
    assert result.decision != Decision.REVIEW_MANUALLY


def test_no_citations_at_all_is_not_unreliable():
    result = run([gap("r1", "software development"), gap("r2", "server deployment"),
                  gap("r3", "ai coding tools"), gap("r4", "linux")])
    assert result.decision == Decision.DONT_APPLY  # 0 have (P6), and no division by zero


# ---------- P4, P5 flags ----------

def test_no_split_is_flagged():  # P4
    assert Flag.NO_SPLIT in run(GOOD, ext=extraction(split=False)).flags


def test_no_skill_requirements_applies_with_flag():  # P5
    only_soft = [n for n in NUMBERED if n.requirement.kind != "skill"]
    result = run([], ext=extraction(requirements=only_soft))
    assert result.decision == Decision.APPLY and Flag.NO_SKILL_REQUIREMENTS in result.flags
    assert result.proven_pct is None


# ---------- Eligibility is separate from the decision ----------

def test_eligibility_never_changes_the_decision():
    needs_visa = Profile.model_validate({
        "expected_graduation": "2029-05",
        "work_authorization": {"us_authorized": False, "needs_sponsorship": True},
    })
    result = run(GOOD, profile=needs_visa)
    assert result.decision == Decision.APPLY and result.eligibility == Eligibility.UNCLEAR
