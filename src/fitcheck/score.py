"""OWNER WRITES: Jimena implements this file. Claude may review, point out bugs, and give hints only.

Step 5 of the pipeline: apply the decision rules. No AI here; plain Python only.
Rules: docs/decision_rules.md section 2 (status table), 3 (ladder), 5 (decision), 4 (eligibility).

Stages (each has its own test file; make one pass before starting the next):
  1. STATUS_TABLE and skill_status()           tests/test_score_stage1.py
  2. requirement_result() and decide()         tests/test_score_stage2.py
  3. score()                                   tests/test_score_stage3.py
  4. check_eligibility(), combine_eligibility()  tests/test_score_stage4.py
"""

import re
from collections import defaultdict
from dataclasses import dataclass

from fitcheck.extract import ExtractionOutcome
from fitcheck.match import MatchOutcome
from fitcheck.profile import Profile
from fitcheck.schemas import (
    Decision, DecisionReason, Eligibility, EligibilityCheck, EligibilityType, FitResult, Flag, Kind,
    NumberedRequirement, Operator, ReasonCode, Relation, ReqType, Requirement, RequirementResult,
    Section, SkillResult, Status,
)
from fitcheck.textmatch import names_term, normalize
from fitcheck.verify import Verification, VerifiedMatch

# ---------- Stage 1 ----------

# Section 2 status table: every (relation, requirement type) pair -> status. 7 x 4 = 28 entries.
STATUS_TABLE: dict[tuple[Relation, ReqType], Status] = {
    (Relation.EXACT, ReqType.CATEGORY): Status.HAVE,
    (Relation.EXACT, ReqType.NAMED_TOOL): Status.HAVE,
    (Relation.EXACT, ReqType.KNOWLEDGE): Status.HAVE,
    (Relation.EXACT, ReqType.HANDS_ON): Status.HAVE,

    (Relation.SAME_CATEGORY, ReqType.CATEGORY): Status.HAVE,
    (Relation.SAME_CATEGORY, ReqType.NAMED_TOOL): Status.WEAK,
    (Relation.SAME_CATEGORY, ReqType.KNOWLEDGE): Status.HAVE,  # *
    (Relation.SAME_CATEGORY, ReqType.HANDS_ON): Status.WEAK,  # *

    (Relation.SMALLER_SCALE, ReqType.CATEGORY): Status.WEAK,  # *
    (Relation.SMALLER_SCALE, ReqType.NAMED_TOOL): Status.WEAK,  # *
    (Relation.SMALLER_SCALE, ReqType.KNOWLEDGE): Status.WEAK,  # *
    (Relation.SMALLER_SCALE, ReqType.HANDS_ON): Status.WEAK,

    (Relation.TAUGHT, ReqType.CATEGORY): Status.HAVE,  # *
    (Relation.TAUGHT, ReqType.NAMED_TOOL): Status.HAVE,
    (Relation.TAUGHT, ReqType.KNOWLEDGE): Status.HAVE,  # *
    (Relation.TAUGHT, ReqType.HANDS_ON): Status.HAVE,  # *

    (Relation.COURSEWORK, ReqType.CATEGORY): Status.WEAK,  # *
    (Relation.COURSEWORK, ReqType.NAMED_TOOL): Status.WEAK,  # *
    (Relation.COURSEWORK, ReqType.KNOWLEDGE): Status.HAVE,
    (Relation.COURSEWORK, ReqType.HANDS_ON): Status.WEAK,

    (Relation.DIFFERENT_TECHNIQUE, ReqType.CATEGORY): Status.GAP,
    (Relation.DIFFERENT_TECHNIQUE, ReqType.NAMED_TOOL): Status.GAP,
    (Relation.DIFFERENT_TECHNIQUE, ReqType.KNOWLEDGE): Status.GAP,
    (Relation.DIFFERENT_TECHNIQUE, ReqType.HANDS_ON): Status.GAP,

    (Relation.NONE, ReqType.CATEGORY): Status.GAP,
    (Relation.NONE, ReqType.NAMED_TOOL): Status.GAP,
    (Relation.NONE, ReqType.KNOWLEDGE): Status.GAP,
    (Relation.NONE, ReqType.HANDS_ON): Status.GAP,
}  # "*" = proposed in decision_rules.md, not yet confirmed by the owner


def skill_status(verified: VerifiedMatch, requirement: Requirement) -> SkillResult:
    """Status and reason code for one skill of one requirement (rules R4-R10).

    verified.match       what the model claimed (relation, evidence_id, quote)
    verified.evidence_kind  "bullet", "course", "honor", "skill" (skills list), or None
    verified.caught      why verify.py rejected the citation, or None
    """
    # (Written by Claude at the owner's request, as a one-time exception to OWNER WRITES.)
    m = verified.match

    def result(status: Status, reason: ReasonCode | None = None) -> SkillResult:
        return SkillResult(
            requirement_id=m.requirement_id, skill=m.skill, status=status, reason_code=reason,
            relation=m.relation, evidence_id=m.evidence_id, quote=m.quote,
        )

    # Overrides: these win no matter what relation the model claimed.
    if verified.caught:  # R5: the citation didn't hold up
        return result(Status.GAP, ReasonCode.FALSE_MATCH_CAUGHT)
    if m.evidence_id is None:  # R8: nothing cited
        return result(Status.GAP, ReasonCode.NOT_ON_RESUME)
    if verified.evidence_kind == "skill":  # R7: skills list only
        return result(Status.WEAK, ReasonCode.LISTED_NOT_SHOWN)

    relation = m.relation
    if verified.evidence_kind == "course" and relation != Relation.DIFFERENT_TECHNIQUE:
        relation = Relation.COURSEWORK  # R6: a course counts as coursework, whatever the model said

    status = STATUS_TABLE[(relation, requirement.type)]  # R4
    if status == Status.GAP:  # R8b: different_technique
        return result(Status.GAP, ReasonCode.NOT_ON_RESUME)
    if status == Status.WEAK:
        return result(Status.WEAK, WEAK_REASONS[relation])

    # R9 runs last because it can only lower a have. R10: level words are ignored.
    if requirement.min_years and not states_years(m.quote, requirement.min_years):
        return result(Status.WEAK, ReasonCode.YEARS_NOT_SHOWN)
    return result(Status.HAVE)


# Why a skill is weak, by the relation that made it weak (section 6 labels).
WEAK_REASONS = {
    Relation.SAME_CATEGORY: ReasonCode.SIMILAR_TOOL,
    Relation.SMALLER_SCALE: ReasonCode.SMALLER_SCALE,
    Relation.COURSEWORK: ReasonCode.COURSEWORK_ONLY,
}

_YEARS = re.compile(r"(\d+)\s*\+?\s*years?\b", re.IGNORECASE)


def states_years(quote: str, min_years: int) -> bool:
    """R9: does the quote itself state at least `min_years`? Never calculated from dates."""
    return any(int(n) >= min_years for n in _YEARS.findall(quote))


# ---------- Stage 2 ----------

APPLY_COVERAGE = 0.80  # P6: the 80% part of the apply rule. Pinned by tests; change only via decision_rules.md.


@dataclass(frozen=True)
class Coverage:
    """What decide() returns. score() (stage 3) copies these into FitResult."""

    decision: Decision           # apply or dont_apply (review_manually is stage 3)
    total: int                   # n: scorable required items
    have: int                    # covered: required items whose status is have
    weak: int                    # required items whose status is weak
    proven_pct: float | None     # 100 * have / n; None when n == 0
    incl_weak_pct: float | None  # 100 * (have + weak) / n; None when n == 0


def requirement_result(numbered: NumberedRequirement, skills: list[SkillResult]) -> RequirementResult:
    """One requirement's status from its skills' statuses (R1, R3)."""
    # (Stage 2 written by Claude at the owner's request.)
    req = numbered.requirement
    if req.kind == Kind.SOFT:  # R1: never scored
        return RequirementResult(requirement_id=numbered.id, status=Status.NOT_SCORED)
    ranks = [_RANK[s.status] for s in skills] or [_RANK[Status.GAP]]  # no evidence at all -> gap
    # R3: "or" is as good as its best skill, "and" as good as its worst.
    best_or_worst = min if req.operator == Operator.AND else max
    status = _BY_RANK[best_or_worst(ranks)]
    return RequirementResult(requirement_id=numbered.id, status=status, skills=skills)


_RANK = {Status.GAP: 0, Status.WEAK: 1, Status.HAVE: 2}  # have > weak > gap
_BY_RANK = {rank: status for status, rank in _RANK.items()}


def decide(results: list[RequirementResult], requirements: list[NumberedRequirement]) -> Coverage:
    """The apply rule over scorable required items only (P6, P8)."""
    status_of = {r.requirement_id: r.status for r in results}
    # Preferred items never count (P8); soft and eligibility items aren't scorable (R1, section 4).
    scorable = [
        n for n in requirements
        if n.requirement.section == Section.REQUIRED and n.requirement.kind == Kind.SKILL
    ]
    n = len(scorable)
    if n == 0:  # P5: nothing to miss; score() adds the no_skill_requirements flag
        return Coverage(Decision.APPLY, 0, 0, 0, None, None)

    statuses = [status_of[r.id] for r in scorable]
    have = statuses.count(Status.HAVE)  # P6: only have counts as covered
    weak = statuses.count(Status.WEAK)
    missed = n - have
    # Written the way the spec states it: covered >= 1 AND (missed <= 2 OR covered / n >= 80%).
    applies = have >= 1 and (missed <= 2 or have / n >= APPLY_COVERAGE)
    return Coverage(
        decision=Decision.APPLY if applies else Decision.DONT_APPLY,
        total=n,
        have=have,
        weak=weak,
        proven_pct=100 * have / n,
        incl_weak_pct=100 * (have + weak) / n,
    )


# ---------- Stage 3 ----------
# (Stages 3 and 4 written by Claude at the owner's request.)

UNRELIABLE_SHARE = 0.5  # P3: more than half of citations caught as false -> review manually


def score(
    extraction: ExtractionOutcome, matched: MatchOutcome, verification: Verification, profile: Profile
) -> FitResult:
    """The final result for one posting: P1-P8, statuses, and eligibility."""
    if extraction.extraction is None:  # P1: extraction failed twice
        return _review(DecisionReason.INVALID_OUTPUT)
    if not extraction.extraction.is_job_posting:  # P2
        return _review(DecisionReason.NOT_A_POSTING)

    requirements = extraction.requirements
    checks = [check_eligibility(n, profile) for n in requirements if n.requirement.kind == Kind.ELIGIBILITY]
    eligibility = combine_eligibility(checks)
    if matched.decision_reason:  # P1: matching failed twice
        return _review(DecisionReason.INVALID_OUTPUT, eligibility, checks)

    by_id = {n.id: n for n in requirements}
    skills_by_requirement: dict[str, list[SkillResult]] = defaultdict(list)
    for v in verification.matches:
        numbered = by_id[v.match.requirement_id]
        skills_by_requirement[numbered.id].append(skill_status(v, numbered.requirement))
    results = [
        requirement_result(n, skills_by_requirement[n.id])
        for n in requirements
        if n.requirement.kind != Kind.ELIGIBILITY  # eligibility is checked separately (section 4)
    ]
    coverage = decide(results, requirements)

    flags = []
    if not extraction.extraction.has_section_split:
        flags.append(Flag.NO_SPLIT)  # P4
    if coverage.total == 0:
        flags.append(Flag.NO_SKILL_REQUIREMENTS)  # P5

    decision, reason = coverage.decision, None
    if verification.citations and verification.caught / verification.citations > UNRELIABLE_SHARE:
        decision, reason = Decision.REVIEW_MANUALLY, DecisionReason.UNRELIABLE_MATCHING  # P3

    return FitResult(
        decision=decision,
        decision_reason=reason,
        flags=flags,
        required_total=coverage.total,
        required_have=coverage.have,
        required_weak=coverage.weak,
        proven_pct=coverage.proven_pct,
        incl_weak_pct=coverage.incl_weak_pct,
        eligibility=eligibility,
        eligibility_checks=checks,
        requirements=results,
    )


def _review(
    reason: DecisionReason, eligibility: Eligibility = Eligibility.UNCLEAR, checks: list[EligibilityCheck] = ()
) -> FitResult:
    # Without a usable extraction nothing was checked, so eligibility is unclear, not eligible.
    return FitResult(
        decision=Decision.REVIEW_MANUALLY, decision_reason=reason,
        eligibility=eligibility, eligibility_checks=list(checks),
    )


# ---------- Stage 4: eligibility (section 4) ----------

# E6: metro aliases. Listed metros can be enabled in profile.yaml; other known cities are recognized
# (so "Boston" is filtered out by name) and anything else is unclear.
METRO_ALIASES = {
    "chicago": "chicago",
    "new york": "nyc", "new york city": "nyc", "nyc": "nyc", "manhattan": "nyc", "brooklyn": "nyc",
    "san francisco": "sf", "sf": "sf",
    "dallas": "dallas_plano", "plano": "dallas_plano", "richardson": "dallas_plano", "frisco": "dallas_plano",
    "boston": "boston", "seattle": "seattle", "austin": "austin", "los angeles": "los_angeles",
}

_GRAD_DEGREES = ("ms", "m.s.", "master's", "masters", "phd", "ph.d.", "doctoral")
_UNDERGRAD_DEGREES = ("bs", "b.s.", "ba", "b.a.", "bachelor's", "bachelors", "undergraduate")


def check_eligibility(numbered: NumberedRequirement, profile: Profile) -> EligibilityCheck:
    """One eligibility item against the profile (E1-E6, E8). status None = shown in details only."""
    req = numbered.requirement
    kind = req.eligibility_type
    if kind == EligibilityType.GRADUATION:
        status, reason = _graduation(req, profile)
    elif kind == EligibilityType.CLASS_YEAR:
        status, reason = Eligibility.UNCLEAR, f"class-year wording isn't interpreted yet (E2): {req.text!r}"
    elif kind == EligibilityType.DEGREE:
        status, reason = _degree(req)
    elif kind == EligibilityType.WORK_AUTHORIZATION:
        status, reason = _work_authorization(req, profile)
    elif kind == EligibilityType.LOCATION:
        status, reason = _location(req, profile)
    else:  # E4 GPA (ignored entirely) and E8 other: shown in details only
        status, reason = None, None
    return EligibilityCheck(requirement_id=numbered.id, eligibility_type=kind, status=status, reason=reason)


def _graduation(req: Requirement, profile: Profile) -> tuple[Eligibility, str | None]:
    """E1. Dates are "YYYY-MM" strings, so comparing them as text compares them as dates."""
    low, high = req.grad_earliest, req.grad_latest
    if low is None and high is None:
        return Eligibility.UNCLEAR, f"graduation restriction without readable dates: {req.text!r}"

    def inside(date: str) -> bool:
        return (low is None or low <= date) and (high is None or date <= high)

    expected = profile.expected_graduation
    if inside(expected):
        return Eligibility.ELIGIBLE, None
    if profile.early_graduation and inside(profile.early_graduation):
        return Eligibility.UNCLEAR, f"only your early graduation date ({profile.early_graduation}) fits"
    if high is not None and high < expected:
        return Eligibility.NEXT_YEAR, f"window ends {high}, before your {expected}"
    return Eligibility.FILTERED_OUT, f"window starts {low}, after your {expected}"


def _degree(req: Requirement) -> tuple[Eligibility, str | None]:
    """E3: only a graduate-only requirement filters; "BS ... or related field" and the like pass."""
    grad_only = any(names_term(req.text, d) for d in _GRAD_DEGREES) and not any(
        names_term(req.text, d) for d in _UNDERGRAD_DEGREES
    )
    if grad_only:
        return Eligibility.FILTERED_OUT, f"graduate students only: {req.text!r}"
    return Eligibility.ELIGIBLE, None


def _work_authorization(req: Requirement, profile: Profile) -> tuple[Eligibility, str | None]:
    """E5: authorized without needing sponsorship passes any wording, including "no sponsorship"."""
    auth = profile.work_authorization
    if auth.us_authorized and not auth.needs_sponsorship:
        return Eligibility.ELIGIBLE, None
    return Eligibility.UNCLEAR, f"check your work authorization against: {req.text!r}"


def _location(req: Requirement, profile: Profile) -> tuple[Eligibility | None, str | None]:
    """E6. Off by default, which means locations are ignored completely."""
    rules = profile.location_filter
    if not rules.enabled:
        return None, None
    if not req.locations:
        return Eligibility.ELIGIBLE, None
    unknown, elsewhere = [], []
    for place in req.locations:
        if names_term(place, "remote"):
            if rules.remote_ok:
                return Eligibility.ELIGIBLE, None
            elsewhere.append(place)
            continue
        metro = METRO_ALIASES.get(normalize(place.split(",")[0]))  # "Plano, TX" -> "plano"
        if metro in rules.metros:
            return Eligibility.ELIGIBLE, None
        (unknown if metro is None else elsewhere).append(place)
    if unknown:
        return Eligibility.UNCLEAR, f"location not recognized: {', '.join(unknown)}"
    return Eligibility.FILTERED_OUT, ", ".join(elsewhere)


_SEVERITY = [Eligibility.FILTERED_OUT, Eligibility.NEXT_YEAR, Eligibility.UNCLEAR]


def combine_eligibility(checks: list[EligibilityCheck]) -> Eligibility:
    """E7: the most severe result wins. No checks at all (or details-only ones) -> eligible."""
    statuses = {c.status for c in checks}
    for level in _SEVERITY:
        if level in statuses:
            return level
    return Eligibility.ELIGIBLE
