"""Step 4 of the pipeline: check that every citation is real. Rules: docs/decision_rules.md R4, R5, R5b.

Verify answers only "is this evidence real and about the skill?" It never sets a status;
score.py does that. A caught citation is reported with the reason, and score.py turns it into
gap / false_match_caught (R5) and counts it toward P3.
"""

from dataclasses import dataclass

from fitcheck.aliases import canonical, names_for
from fitcheck.resume import EvidenceItem, Resume
from fitcheck.schemas import NumberedRequirement, ReqType, Relation, SkillMatch
from fitcheck.textmatch import appears_in, names_term, normalize


@dataclass(frozen=True)
class VerifiedMatch:
    match: SkillMatch
    evidence_kind: str | None  # bullet, course, honor, skill; None when nothing was cited
    caught: str | None = None  # why the citation was rejected (R5, R5b); None if it holds up


@dataclass(frozen=True)
class Verification:
    matches: list[VerifiedMatch]

    @property
    def citations(self) -> int:
        return sum(v.match.evidence_id is not None for v in self.matches)

    @property
    def caught(self) -> int:
        return sum(v.caught is not None for v in self.matches)


def verify(matches: list[SkillMatch], requirements: list[NumberedRequirement], resume: Resume) -> Verification:
    types = {n.id: n.requirement.type for n in requirements}
    return Verification([_verify_one(m, types.get(m.requirement_id), resume) for m in matches])


def _verify_one(m: SkillMatch, req_type: ReqType | None, resume: Resume) -> VerifiedMatch:
    if m.evidence_id is None:
        return VerifiedMatch(m, None)  # relation "none": nothing to check
    item = resume.by_id.get(m.evidence_id)
    if item is None:
        return VerifiedMatch(m, None, f"R5: no resume item with ID {m.evidence_id!r}")
    if not appears_in(m.quote, item.text):
        return VerifiedMatch(m, item.kind, f"R5: quote not found in {item.id}")
    if m.relation == Relation.EXACT and req_type == ReqType.NAMED_TOOL and not _is_about(item, m.skill, resume):
        return VerifiedMatch(m, item.kind, f"R5b: {item.id} doesn't name {m.skill!r} and isn't linked to it")
    return VerifiedMatch(m, item.kind)


def _is_about(item: EvidenceItem, skill: str, resume: Resume) -> bool:
    """R5b: the item names the skill (or an alias), or a skills-list entry for it links to the item."""
    if any(names_term(item.text, name) for name in names_for(skill)):
        return True
    return any(
        other.kind == "skill" and canonical(normalize(other.text)) == skill and item.id in other.shown_in
        for other in resume.items
    )
