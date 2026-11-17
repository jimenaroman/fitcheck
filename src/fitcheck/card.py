"""Render a FitResult as the card from decision_rules.md section 6. Display only: no rules here.

Never a bare status: every weak or gap shows its label and fix.
"""

from fitcheck.schemas import (
    Decision, DecisionReason, Eligibility, FitResult, Flag, Kind, NumberedRequirement, ReasonCode, Section,
    SkillResult, Status,
)

# Section 6: reason -> (label, fix).
LABELS = {
    ReasonCode.LISTED_NOT_SHOWN: ("Listed, not shown", "Add a bullet where you used it"),
    ReasonCode.SIMILAR_TOOL: ("Similar tool", "Mention it, or try their tool"),
    ReasonCode.SMALLER_SCALE: ("Smaller scale", "Describe or build something bigger"),
    ReasonCode.COURSEWORK_ONLY: ("Coursework only", "Add a project that uses it"),
    ReasonCode.YEARS_NOT_SHOWN: ("Years not shown", "State the duration in a bullet"),
    ReasonCode.NOT_ON_RESUME: ("Not on your resume", "Learn it, or tap \"I've done this\""),
}
# Caught false matches are saved, not shown (section 6): the card shows them as not on the resume.
LABELS[ReasonCode.FALSE_MATCH_CAUGHT] = LABELS[ReasonCode.NOT_ON_RESUME]

REVIEW_REASONS = {
    DecisionReason.INVALID_OUTPUT: "the model's answer failed validation twice",
    DecisionReason.NOT_A_POSTING: "this doesn't look like a job posting",
    DecisionReason.UNRELIABLE_MATCHING: "more than half of the evidence citations didn't hold up",
}
SYMBOLS = {Status.HAVE: "✓", Status.WEAK: "~", Status.GAP: "✗", Status.NOT_SCORED: "·"}


def render_card(result: FitResult, requirements: list[NumberedRequirement]) -> str:
    lines = [_headline(result)]
    if result.decision != Decision.REVIEW_MANUALLY or result.required_total:
        lines += _coverage(result)
    by_id = {n.id: n.requirement for n in requirements}
    results = {r.requirement_id: r for r in result.requirements}

    for section, title in ((Section.REQUIRED, "Required"), (Section.PREFERRED, "Preferred (never changes the decision)")):
        ids = [n.id for n in requirements
               if n.requirement.section == section and n.requirement.kind == Kind.SKILL and n.id in results]
        if ids:
            lines.append(f"\n{title}:")
            for req_id in ids:
                lines += _requirement(req_id, by_id[req_id].text, results[req_id].status, results[req_id].skills)

    details = [f"  · {by_id[r.requirement_id].text} (not scored)"
               for r in result.requirements if r.status == Status.NOT_SCORED]
    for check in result.eligibility_checks:
        shown = check.status.value if check.status else "shown only"
        reason = f": {check.reason}" if check.reason else ""
        details.append(f"  · {by_id[check.requirement_id].text} [{check.eligibility_type.value}: {shown}{reason}]")
    if Flag.NO_SKILL_REQUIREMENTS in result.flags:
        details.append("  · No scorable required skills, so there was nothing to miss.")
    if details:
        lines += ["\nDetails:"] + details
    return "\n".join(lines)


def _headline(result: FitResult) -> str:
    if result.decision == Decision.REVIEW_MANUALLY:
        return f"REVIEW MANUALLY: {REVIEW_REASONS[result.decision_reason]}"
    if result.eligibility == Eligibility.FILTERED_OUT:
        return f"FILTERED OUT: {_eligibility_reasons(result, Eligibility.FILTERED_OUT)}"
    if result.eligibility == Eligibility.NEXT_YEAR:
        return f"NEXT YEAR: {_eligibility_reasons(result, Eligibility.NEXT_YEAR)}"
    if result.decision == Decision.APPLY:
        if result.eligibility == Eligibility.UNCLEAR:
            return f"APPLY, check eligibility first: {_eligibility_reasons(result, Eligibility.UNCLEAR)}"
        return "APPLY"
    missed = result.required_total - result.required_have
    return f"NOT YET: {missed} required not proven"


def _coverage(result: FitResult) -> list[str]:
    if result.proven_pct is None:
        return []
    line = (f"{result.proven_pct:.0f}% proven, {result.incl_weak_pct:.0f}% incl. weak "
            f"({result.required_have} of {result.required_total} required)")
    if Flag.NO_SPLIT in result.flags:
        line += " · Required/preferred split unclear"
    return [line]


def _eligibility_reasons(result: FitResult, level: Eligibility) -> str:
    return "; ".join(c.reason or c.eligibility_type.value for c in result.eligibility_checks if c.status == level)


def _requirement(req_id: str, text: str, status: Status, skills: list[SkillResult]) -> list[str]:
    lines = [f"  {SYMBOLS[status]} {req_id} \"{text}\""]
    for s in skills:
        line = f"      {s.skill}: {s.status.value}"
        if s.status == Status.HAVE and s.evidence_id:
            line += f" via {s.evidence_id}"
        elif s.reason_code in LABELS:
            label, fix = LABELS[s.reason_code]
            line += f" · {label} → {fix}"
        lines.append(line)
    return lines
