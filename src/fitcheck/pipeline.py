"""The full pipeline for one posting: extract, match, verify, score. Shared by the eval harness and the API.

The CLI keeps its own step-by-step version because `extract` and `match` stop partway.
"""

from dataclasses import dataclass
from pathlib import Path

from fitcheck.extract import ExtractionOutcome, extract
from fitcheck.match import MatchOutcome, match
from fitcheck.profile import Profile
from fitcheck.providers.base import CallLog, Provider
from fitcheck.providers.pricing import cost_usd
from fitcheck.resume import Resume
from fitcheck.retry import FAILURE_LOG, log_rejections
from fitcheck.schemas import FitResult
from fitcheck.score import score
from fitcheck.verify import Verification, verify


@dataclass(frozen=True)
class CheckOutcome:
    extraction: ExtractionOutcome
    matched: MatchOutcome
    verification: Verification
    result: FitResult

    @property
    def logs(self) -> list[CallLog]:
        return self.extraction.logs + self.matched.logs

    @property
    def calls(self) -> int:
        return self.extraction.calls + self.matched.calls


def check_posting(posting: str, provider: Provider, resume: Resume, profile: Profile,
                  failure_log: Path = FAILURE_LOG) -> CheckOutcome:
    """Raises ValueError for an empty posting and BudgetExceededError at my limit: neither is a model failure."""
    extraction = extract(posting, provider)
    log_rejections("extract", extraction.rejections, failure_log)
    matched = MatchOutcome([], None)  # P1/P2: no matching call when extraction failed or it's not a posting
    if extraction.extraction is not None and extraction.extraction.is_job_posting:
        matched = match(extraction.requirements, resume, provider)
        log_rejections("match", matched.rejections, failure_log)
    verification = verify(matched.matches, extraction.requirements, resume)
    return CheckOutcome(extraction, matched, verification, score(extraction, matched, verification, profile))


def saved_fields(checked: CheckOutcome, resume: Resume) -> dict:
    """Everything save_check and update_check store about one pipeline run."""
    def priced(logs):
        return round(sum(cost_usd(log.model, log.input_tokens, log.output_tokens) for log in logs), 6)

    return {
        "requirements": checked.extraction.requirements,
        "result": checked.result,
        "model": checked.logs[-1].model if checked.logs else "",
        "cost_usd": priced([log for log in checked.logs if not log.cached]),  # spent now
        "calls": checked.calls,
        "resume_sha256": resume.sha256,
        "match_cost_usd": priced(checked.matched.logs),  # original cost, cached or not
    }
