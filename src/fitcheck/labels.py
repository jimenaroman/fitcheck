"""The eval answer key format (evals/labels/*.yaml), validated so a typo fails loudly.

Labels are written by hand by the owner, from the posting text and the frozen resume only,
never from model output. Code reads them; it never writes or edits them.
"""

import hashlib
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fitcheck.schemas import Decision, Eligibility, Kind, Section, Status

LABELED_STATUSES = {Status.HAVE, Status.WEAK, Status.GAP}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabeledRequirement(_Strict):
    quote: str = Field(min_length=1)  # copied exactly from the posting
    kind: Kind
    section: Section | None = None    # skill and soft items
    status: Status | None = None      # skill items only: have, weak, or gap (requirement level, R3)

    @model_validator(mode="after")
    def _check(self) -> "LabeledRequirement":
        if self.kind == Kind.ELIGIBILITY and (self.section or self.status):
            raise ValueError("eligibility items have no section or status")
        if self.kind != Kind.ELIGIBILITY and self.section is None:
            raise ValueError(f"{self.kind.value} items need a section")
        if self.kind == Kind.SKILL and self.status not in LABELED_STATUSES:
            raise ValueError("skill items need a status: have, weak, or gap")
        if self.kind == Kind.SOFT and self.status:
            raise ValueError("soft items are never scored, so they have no status")
        return self


class Label(_Strict):
    posting: str                                    # file name in evals/postings/ (gitignored)
    source_url: str = Field(pattern=r"^https?://")
    saved_on: date
    posting_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")  # fingerprint: proves the text didn't change
    resume: str = "eval_resume_v1"
    is_job_posting: bool
    has_section_split: bool
    requirements: list[LabeledRequirement]
    decision: Decision
    eligibility: Eligibility


def load_label(path: Path) -> Label:
    return Label.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
