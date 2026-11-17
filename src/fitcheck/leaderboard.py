"""Skills leaderboard (decision_rules.md section 7): which skills postings ask for, and how I stand on each.

Plain Python over saved checks; no model call. Rules L1-L10 are noted where they apply.
"""

from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from fitcheck.schemas import (Decision, Eligibility, FitResult, Kind, NumberedRequirement, Operator,
                              ReasonCode, Section, Status)


@dataclass(frozen=True)
class SavedCheck:
    """What the leaderboard needs from one saved check (db.CheckRow provides it)."""
    id: int
    posting_sha256: str
    resume_sha256: str
    requirements: list[NumberedRequirement]
    result: FitResult


class BoardEntry(BaseModel):
    skill: str
    required: int = 0         # postings that ask for it as required
    preferred: int = 0        # ... as preferred (L6: tallied separately)
    as_alternative: int = 0   # gaps only (L8): an "or" another skill already covered
    reasons: dict[str, int] = {}  # unproven only: why it's weak -> postings ("listed_not_shown": 3)
    highlighted: bool = False  # gaps only (L6): preferred by more than half of postings
    marked_at: datetime | None = None  # marked_done only: when I marked it


class Leaderboard(BaseModel):
    postings_counted: int
    skipped_review_manually: int   # L3
    skipped_filtered_out: int      # L5
    skipped_duplicates: int        # L2
    decisions: dict[str, int]      # one per posting (L2), all of them, including skipped ones: "apply": 5
    stale_check_ids: list[int]     # L7: checked against an older resume; a recheck updates them
    strengths: list[BoardEntry]          # have
    unproven: list[BoardEntry]           # weak, for any reason; each reason has its own fix on the card
    marked_done: list[BoardEntry]        # gaps I marked "I've done this" (L9)
    gaps: list[BoardEntry]               # every other gap, most requested first


@dataclass
class _Tally:
    entries: dict[str, BoardEntry] = field(default_factory=dict)

    def add(self, skill: str, section: Section, alternative: bool = False, reason: ReasonCode | None = None) -> None:
        entry = self.entries.setdefault(skill, BoardEntry(skill=skill))
        if section == Section.REQUIRED:
            entry.required += 1
        else:
            entry.preferred += 1
        entry.as_alternative += alternative
        if reason is not None:
            entry.reasons[reason.value] = entry.reasons.get(reason.value, 0) + 1

    def ranked(self) -> list[BoardEntry]:
        return sorted(self.entries.values(), key=lambda e: (-e.required, -e.preferred, e.skill))


def build_leaderboard(checks: list[SavedCheck], done: dict[str, datetime], current_resume_sha256: str) -> Leaderboard:
    """`checks` newest first (as db.list_checks returns them); `done` maps skills marked "I've done this"
    to when."""
    seen_postings: set[str] = set()
    skipped = {"review": 0, "filtered": 0, "duplicate": 0}
    boards = {"strengths": _Tally(), "unproven": _Tally(), "done": _Tally(), "gaps": _Tally()}
    counted = 0
    decisions: dict[str, int] = {}

    for check in checks:
        if check.posting_sha256 in seen_postings:  # L2: the newest check of a posting is the one that counts
            skipped["duplicate"] += 1
            continue
        seen_postings.add(check.posting_sha256)
        decision = check.result.decision.value
        decisions[decision] = decisions.get(decision, 0) + 1
        if check.result.decision == Decision.REVIEW_MANUALLY:  # L3
            skipped["review"] += 1
            continue
        if check.result.eligibility == Eligibility.FILTERED_OUT:  # L5 (next_year postings do count)
            skipped["filtered"] += 1
            continue
        counted += 1
        for skill, (section, status, reason, alternative) in _skills_in_posting(check).items():
            if status == Status.HAVE:
                boards["strengths"].add(skill, section)
            elif status == Status.WEAK:
                boards["unproven"].add(skill, section, reason=reason)
            elif status == Status.GAP:
                boards["done" if skill in done else "gaps"].add(skill, section, alternative)  # L9

    gaps = boards["gaps"].ranked()
    for entry in gaps:
        entry.highlighted = entry.preferred * 2 > counted  # L6: more than half of postings
    marked_done = boards["done"].ranked()
    for entry in marked_done:
        entry.marked_at = done[entry.skill]
    return Leaderboard(
        postings_counted=counted,
        skipped_review_manually=skipped["review"],
        skipped_filtered_out=skipped["filtered"],
        skipped_duplicates=skipped["duplicate"],
        decisions=decisions,
        stale_check_ids=sorted(c.id for c in checks if c.resume_sha256 != current_resume_sha256),
        strengths=boards["strengths"].ranked(),
        unproven=boards["unproven"].ranked(),
        marked_done=marked_done,
        gaps=gaps,
    )


_RANK = {Status.GAP: 0, Status.WEAK: 1, Status.HAVE: 2}


def _skills_in_posting(check: SavedCheck) -> dict[str, tuple[Section, Status, ReasonCode | None, bool]]:
    """Each skill once per posting (L1): (section, status, reason, as_alternative).

    A skill asked for in both sections counts as required. Asked for twice in the same section, its
    best status counts. Only `skills` are tallied, never `examples` (L10); soft and eligibility
    items have no skills (L4).
    """
    reqs = {n.id: n.requirement for n in check.requirements}
    found: dict[str, tuple[Section, Status, ReasonCode | None, bool]] = {}
    for req_result in check.result.requirements:
        req = reqs.get(req_result.requirement_id)
        if req is None or req.kind != Kind.SKILL:
            continue
        covered_or = req.operator == Operator.OR and req_result.status == Status.HAVE
        for s in req_result.skills:  # L8: every skill in a combined requirement on its own
            if s.status not in _RANK:
                continue
            candidate = (req.section, s.status, s.reason_code, covered_or and s.status == Status.GAP)
            current = found.get(s.skill)
            if current is None or _better(candidate, current):
                found[s.skill] = candidate
    return found


def _better(new: tuple, old: tuple) -> bool:
    if new[0] != old[0]:
        return new[0] == Section.REQUIRED
    return _RANK[new[1]] > _RANK[old[1]]
