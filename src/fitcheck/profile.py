"""Load profile.yaml: the owner facts eligibility needs (decision_rules.md section 4).

The profile is never sent to a model. score.py compares it against extracted eligibility items.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from fitcheck.schemas import YearMonth

DEFAULT_PATH = Path("profile.yaml")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo like `remote_okk` fails loudly


class WorkAuthorization(_Strict):
    us_authorized: bool
    needs_sponsorship: bool


class LocationFilter(_Strict):
    enabled: bool = False  # E6: off by default
    metros: list[str] = Field(default_factory=list)
    remote_ok: bool = True


class Profile(_Strict):
    expected_graduation: YearMonth
    early_graduation: YearMonth | None = None  # E1: the earliest you could graduate, if earlier
    work_authorization: WorkAuthorization
    location_filter: LocationFilter = LocationFilter()


def load_profile(path: Path = DEFAULT_PATH) -> Profile:
    return parse_profile(Path(path).read_text(encoding="utf-8"))


def parse_profile(text: str) -> Profile:
    """The same checks as load_profile, for YAML that didn't come from a file (an API upload)."""
    return Profile.model_validate(yaml.safe_load(text))
