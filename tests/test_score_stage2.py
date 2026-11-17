"""Stage 2 of score.py: one requirement from its skills (R1, R3), then the posting decision (P6, P8).

These tests are the spec (decision_rules.md sections 3, 5, 8). Change the spec first, then the test.
"""

import pytest

from fitcheck.schemas import Decision, NumberedRequirement, Requirement, RequirementResult, SkillResult, Status
from fitcheck.score import APPLY_COVERAGE, decide, requirement_result

HAVE, WEAK, GAP, NOT_SCORED = Status.HAVE, Status.WEAK, Status.GAP, Status.NOT_SCORED
APPLY, DONT = Decision.APPLY, Decision.DONT_APPLY


def skill_req(req_id, skills=("python",), operator=None, section="required"):
    return NumberedRequirement(id=req_id, requirement=Requirement(
        text="x", section=section, kind="skill", skills=list(skills), operator=operator, type="named_tool"))


def soft_req(req_id, section="required"):
    return NumberedRequirement(id=req_id, requirement=Requirement(text="Teamwork", section=section, kind="soft"))


def elig_req(req_id):
    return NumberedRequirement(id=req_id, requirement=Requirement(
        text="US work authorization", section="required", kind="eligibility", eligibility_type="work_authorization"))


def skill(req_id, name, status):
    return SkillResult(requirement_id=req_id, skill=name, status=status)


# ---------- requirement_result: R1 soft items ----------

def test_soft_item_is_not_scored():
    result = requirement_result(soft_req("r1"), [])
    assert (result.requirement_id, result.status, result.skills) == ("r1", NOT_SCORED, [])


# ---------- requirement_result: R3 several skills ----------

def test_single_skill_requirement_takes_its_skill_status():
    req = skill_req("r1")
    for status in (HAVE, WEAK, GAP):
        assert requirement_result(req, [skill("r1", "python", status)]).status == status


@pytest.mark.parametrize("statuses, expected", [
    ((HAVE, GAP), HAVE),   # "Python or Java": one have is enough
    ((GAP, HAVE), HAVE),
    ((WEAK, GAP), WEAK),   # not covered; the best skill (weak) feeds the incl-weak %
    ((GAP, GAP), GAP),
])
def test_or_requirement_is_as_good_as_its_best_skill(statuses, expected):
    req = skill_req("r1", ("python", "java"), "or")
    skills = [skill("r1", "python", statuses[0]), skill("r1", "java", statuses[1])]
    assert requirement_result(req, skills).status == expected


@pytest.mark.parametrize("statuses, expected", [
    ((HAVE, HAVE), HAVE),
    ((HAVE, GAP), GAP),    # "Python and Kubernetes": kubernetes missing -> not covered
    ((HAVE, WEAK), WEAK),  # the worst skill decides
    ((WEAK, GAP), GAP),
])
def test_and_requirement_is_as_good_as_its_worst_skill(statuses, expected):
    req = skill_req("r1", ("python", "kubernetes"), "and")
    skills = [skill("r1", "python", statuses[0]), skill("r1", "kubernetes", statuses[1])]
    assert requirement_result(req, skills).status == expected


def test_skill_results_are_kept_on_the_requirement():
    skills = [skill("r1", "python", HAVE), skill("r1", "java", GAP)]
    result = requirement_result(skill_req("r1", ("python", "java"), "or"), skills)
    assert result.skills == skills  # the leaderboard tallies every skill on its own (L8)


# ---------- decide: P6, the section 8 decision rows ----------

def posting(*statuses):
    """n required skill items with these statuses, in order."""
    reqs = [skill_req(f"r{i}") for i in range(1, len(statuses) + 1)]
    results = [RequirementResult(requirement_id=r.id, status=s) for r, s in zip(reqs, statuses)]
    return results, reqs


@pytest.mark.parametrize("statuses, expected", [
    ((GAP,), DONT),                          # 1 required, 0 have: nothing met
    ((HAVE, GAP), APPLY),                    # 2 required, 1 have: 1 missed
    ((HAVE, HAVE, GAP), APPLY),              # 3 required, 2 have
    ((HAVE, GAP, GAP), APPLY),               # 3 required, 1 have: 2 missed
    ((HAVE, HAVE, HAVE, GAP, GAP), APPLY),   # 5 required, 3 have: 2 missed
    ((HAVE, HAVE, GAP, GAP, GAP), DONT),     # 5 required, 2 have: 3 missed, 40%
    ((HAVE,) * 12 + (GAP,) * 3, APPLY),      # 12/15: 3 missed, but exactly 80% (long postings stay fair)
    ((HAVE,) * 11 + (GAP,) * 4, DONT),       # 11/15: 4 missed, 73%
], ids=["1-0", "2-1", "3-2", "3-1", "5-3", "5-2", "15-12", "15-11"])
def test_apply_rule(statuses, expected):
    assert decide(*posting(*statuses)).decision == expected


def test_weak_never_counts_as_covered():
    # 3 required, all weak: 0 covered -> dont_apply, even though incl. weak is 100% (P6).
    coverage = decide(*posting(WEAK, WEAK, WEAK))
    assert coverage.decision == DONT and coverage.have == 0


def test_threshold_is_one_constant():
    assert APPLY_COVERAGE == 0.80


# ---------- decide: counts and both percentages ----------

def test_counts_and_percentages():
    # "60% proven, 80% incl. weak (3 of 5 required)"
    coverage = decide(*posting(HAVE, HAVE, HAVE, WEAK, GAP))
    assert (coverage.total, coverage.have, coverage.weak) == (5, 3, 1)
    assert coverage.proven_pct == pytest.approx(60.0)
    assert coverage.incl_weak_pct == pytest.approx(80.0)


def test_no_scorable_required_items_applies_without_percentages():
    # P5's decision; the no_skill_requirements flag is added by score() in stage 3.
    coverage = decide([], [])
    assert (coverage.decision, coverage.total, coverage.proven_pct, coverage.incl_weak_pct) == (APPLY, 0, None, None)


# ---------- decide: only scorable required items count (P8, R1, section 4) ----------

def test_preferred_items_never_change_the_decision():
    results, reqs = posting(HAVE, GAP)
    preferred = [skill_req(f"p{i}", section="preferred") for i in range(5)]
    results += [RequirementResult(requirement_id=p.id, status=GAP) for p in preferred]
    coverage = decide(results, reqs + preferred)
    assert coverage.decision == APPLY and coverage.total == 2


def test_soft_and_eligibility_items_are_not_counted():
    results, reqs = posting(HAVE, GAP, GAP)
    extra = [soft_req("s1"), elig_req("e1")]
    results += [RequirementResult(requirement_id="s1", status=NOT_SCORED)]
    coverage = decide(results, reqs + extra)
    assert coverage.total == 3 and coverage.decision == APPLY
