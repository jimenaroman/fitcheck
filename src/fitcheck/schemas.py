"""Pydantic schemas for the fit checker. Rules live in docs/decision_rules.md.

Two groups of models:
- MODEL-FACING (Extraction and its item shapes, MatchSet, SkillMatch): sent to the LLM as
  structured-output schemas. They contain no status, decision, or ID the model could invent.
  Extraction items come in four shapes (skill, soft, graduation, other eligibility), so the
  decoder itself enforces which fields each kind has; code converts them to Requirement.
- CODE-ONLY (NumberedRequirement, SkillResult, RequirementResult, EligibilityCheck,
  FitResult): filled in by our Python code, never by the model.

Schemas define which values exist. Policy (the status table, the apply rule) lives in score.py.
"""

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator


# ---------- Enums: every fixed set of values ----------

class Section(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"


class Kind(StrEnum):
    SKILL = "skill"
    SOFT = "soft"
    ELIGIBILITY = "eligibility"


class Operator(StrEnum):
    AND = "and"
    OR = "or"


class ReqType(StrEnum):
    CATEGORY = "category"
    NAMED_TOOL = "named_tool"
    KNOWLEDGE = "knowledge"
    HANDS_ON = "hands_on"


class Level(StrEnum):
    EXPERT = "expert"
    DEEP = "deep"
    ADVANCED = "advanced"


class EligibilityType(StrEnum):
    GRADUATION = "graduation"
    CLASS_YEAR = "class_year"
    DEGREE = "degree"
    GPA = "gpa"
    WORK_AUTHORIZATION = "work_authorization"
    LOCATION = "location"
    OTHER = "other"


class Relation(StrEnum):
    EXACT = "exact"
    SAME_CATEGORY = "same_category"
    SMALLER_SCALE = "smaller_scale"
    TAUGHT = "taught"
    COURSEWORK = "coursework"
    DIFFERENT_TECHNIQUE = "different_technique"
    NONE = "none"


class Status(StrEnum):
    HAVE = "have"
    WEAK = "weak"
    GAP = "gap"
    NOT_SCORED = "not_scored"


class ReasonCode(StrEnum):
    LISTED_NOT_SHOWN = "listed_not_shown"
    SIMILAR_TOOL = "similar_tool"
    SMALLER_SCALE = "smaller_scale"
    COURSEWORK_ONLY = "coursework_only"
    YEARS_NOT_SHOWN = "years_not_shown"
    NOT_ON_RESUME = "not_on_resume"
    FALSE_MATCH_CAUGHT = "false_match_caught"


class Eligibility(StrEnum):
    ELIGIBLE = "eligible"
    UNCLEAR = "unclear"
    NEXT_YEAR = "next_year"
    FILTERED_OUT = "filtered_out"


class Decision(StrEnum):
    APPLY = "apply"
    DONT_APPLY = "dont_apply"
    REVIEW_MANUALLY = "review_manually"


class DecisionReason(StrEnum):
    INVALID_OUTPUT = "invalid_output"            # P1
    NOT_A_POSTING = "not_a_posting"              # P2
    UNRELIABLE_MATCHING = "unreliable_matching"  # P3


class Flag(StrEnum):
    NO_SPLIT = "no_split"                            # P4
    NO_SKILL_REQUIREMENTS = "no_skill_requirements"  # P5


# ---------- Shared field types ----------

_YEAR_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _check_year_month(value: str) -> str:
    if not _YEAR_MONTH.fullmatch(value):
        raise ValueError(f"expected 'YYYY-MM' with a real month, got {value!r}")
    return value


YearMonth = Annotated[str, AfterValidator(_check_year_month)]


class _Strict(BaseModel):
    # Unknown keys are errors, so a model that adds "status": "have" gets rejected.
    model_config = ConfigDict(extra="forbid")


# ---------- MODEL-FACING: extraction ----------

class Requirement(_Strict):
    """One line from the posting, labeled by the model. No ID: code assigns those."""

    text: str = Field(min_length=1)
    section: Section
    kind: Kind
    skills: list[str] = []
    operator: Operator | None = None
    type: ReqType | None = None
    category: str | None = None
    examples: list[str] = []  # skill items: tools given as illustrations ("such as"); never tallied (L10)
    min_years: int | None = Field(default=None, ge=0)
    level: Level | None = None
    eligibility_type: EligibilityType | None = None
    locations: list[str] = []
    grad_earliest: YearMonth | None = None
    grad_latest: YearMonth | None = None

    @field_validator("skills", "examples")
    @classmethod
    def _normalize_skills(cls, skills: list[str]) -> list[str]:
        # Canonical names are lowercase. Alias merging (js -> javascript) happens later in code.
        return [s.strip().lower() for s in skills if s.strip()]

    @model_validator(mode="after")
    def _check_consistency(self) -> "Requirement":
        is_elig = self.kind == Kind.ELIGIBILITY
        if is_elig and self.eligibility_type is None:
            raise ValueError("eligibility items need an eligibility_type")
        if not is_elig and (self.eligibility_type or self.locations):
            raise ValueError("eligibility_type and locations are for eligibility items only")
        has_grad = self.grad_earliest is not None or self.grad_latest is not None
        if has_grad and self.eligibility_type != EligibilityType.GRADUATION:
            raise ValueError("grad_earliest/grad_latest are for graduation items only")
        if self.grad_earliest and self.grad_latest and self.grad_earliest > self.grad_latest:
            raise ValueError("grad_earliest is after grad_latest")
        if self.kind == Kind.SKILL and self.type is None:
            raise ValueError("skill items need a type")
        # An empty `skills` is allowed here: extract.py fills it from `category` (and counts it),
        # and NumberedRequirement enforces "at least one skill" for everything downstream.
        if len(self.skills) > 1 and self.operator is None:
            raise ValueError("requirements naming several skills need an operator")
        if self.examples and self.kind != Kind.SKILL:
            raise ValueError("examples are for skill items only")
        return self


# The shapes the model fills in. Requirement above is what code works with after conversion.

def _every_key_required(schema: dict) -> None:
    # The API marks a field with a default as optional, and Haiku skips optional fields (it left
    # `skills` out 12 times on one posting). Requiring every key makes it answer each one
    # (null or [] where nothing applies). Python defaults stay, so tests can omit fields.
    for prop in schema.get("properties", {}).values():
        prop.pop("default", None)
    schema["required"] = list(schema.get("properties", {}))


class _Shape(_Strict):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_every_key_required)

    def to_requirement(self) -> Requirement:
        return Requirement(**self.model_dump())

    @model_validator(mode="after")
    def _check_as_requirement(self):
        self.to_requirement()  # Requirement's rules (operator for several skills, ...) apply here too
        return self


def _only(value: str):
    # A one-value Literal becomes "const", which structured outputs don't enforce; "enum" is enforced.
    return Annotated[Literal[value], WithJsonSchema({"type": "string", "enum": [value]})]


class SkillItem(_Shape):
    kind: _only("skill")
    text: str = Field(min_length=1)
    section: Section
    skills: list[str] = Field(min_length=1)  # minItems 1: the decoder can't return an empty list
    operator: Operator | None = None
    type: ReqType
    category: str | None = None
    examples: list[str] = []
    min_years: int | None = Field(default=None, ge=0)
    level: Level | None = None


class SoftItem(_Shape):
    kind: _only("soft")
    text: str = Field(min_length=1)
    section: Section


class GraduationItem(_Shape):
    """The only shape with graduation dates, so dates can't land on a degree or location item."""

    kind: _only("eligibility")
    text: str = Field(min_length=1)
    section: Section
    eligibility_type: _only("graduation")
    grad_earliest: YearMonth | None = None
    grad_latest: YearMonth | None = None


class EligibilityItem(_Shape):
    kind: _only("eligibility")
    text: str = Field(min_length=1)
    section: Section
    eligibility_type: Literal["class_year", "degree", "gpa", "work_authorization", "location", "other"]
    locations: list[str] = []


ExtractedItem = SkillItem | SoftItem | GraduationItem | EligibilityItem


class Extraction(_Strict):
    """Everything the model extracts from one posting."""

    is_job_posting: bool
    has_section_split: bool
    requirements: list[ExtractedItem]


# ---------- MODEL-FACING: matching ----------

class SkillMatch(_Strict):
    """The model's evidence for one skill. A relation, never a status."""

    requirement_id: str
    skill: str
    evidence_id: str | None = None
    quote: str | None = None
    relation: Relation
    reason: str  # short free text from the model; the reason *code* is set by our code

    @field_validator("skill")
    @classmethod
    def _normalize_skill(cls, skill: str) -> str:
        return skill.strip().lower()

    @model_validator(mode="after")
    def _evidence_matches_relation(self) -> "SkillMatch":
        has_evidence = bool(self.evidence_id) and bool(self.quote)
        if self.relation == Relation.NONE:
            if self.evidence_id is not None or self.quote is not None:
                raise ValueError("relation 'none' must not cite evidence")
        elif not has_evidence:
            raise ValueError(f"relation {self.relation.value!r} needs an evidence_id and a quote")
        return self


class MatchSet(_Strict):
    """All matches for one posting (one model call per posting)."""

    matches: list[SkillMatch]


# ---------- CODE-ONLY ----------

class NumberedRequirement(_Strict):
    """A requirement after code has assigned its ID (r1, r2, ...)."""

    id: str
    requirement: Requirement

    @model_validator(mode="after")
    def _check_skills(self) -> "NumberedRequirement":
        if self.requirement.kind == Kind.SKILL and not self.requirement.skills:
            raise ValueError("skill items need at least one skill")
        return self


class SkillResult(_Strict):
    """One skill after verify.py and score.py. Status and reason code come from code."""

    requirement_id: str
    skill: str
    status: Status
    reason_code: ReasonCode | None = None
    relation: Relation | None = None  # what the model claimed, kept for evals
    evidence_id: str | None = None
    quote: str | None = None


class RequirementResult(_Strict):
    requirement_id: str
    status: Status
    skills: list[SkillResult] = []


class EligibilityCheck(_Strict):
    requirement_id: str
    eligibility_type: EligibilityType
    status: Eligibility | None  # None = shown in details only, no effect (E4 GPA, E8 other)
    reason: str | None = None


class FitResult(_Strict):
    """Final result for one posting. Filled in by score.py."""

    decision: Decision
    decision_reason: DecisionReason | None = None
    flags: list[Flag] = []
    required_total: int = Field(default=0, ge=0)  # scorable required items (n)
    required_have: int = Field(default=0, ge=0)
    required_weak: int = Field(default=0, ge=0)
    proven_pct: float | None = Field(default=None, ge=0, le=100)     # have / n; drives the decision
    incl_weak_pct: float | None = Field(default=None, ge=0, le=100)  # (have + weak) / n; info only
    eligibility: Eligibility  # no default: forgetting it should fail loudly, not show "unclear"
    eligibility_checks: list[EligibilityCheck] = []
    requirements: list[RequirementResult] = []

    @model_validator(mode="after")
    def _check_consistency(self) -> "FitResult":
        is_review = self.decision == Decision.REVIEW_MANUALLY
        if is_review != (self.decision_reason is not None):
            raise ValueError("decision_reason is required for review_manually, and only for it")
        if self.required_have + self.required_weak > self.required_total:
            raise ValueError("have + weak cannot exceed required_total")
        return self
