"""M6 eval harness: run labeled postings through the pipeline and compare with the answer key.

Labels (evals/labels/*.yaml) are read, never written. Every posting's text must still match the
fingerprint in its label, and every labeled quote must appear in the posting, or the case is refused.
Matching uses the FROZEN resume so scores stay comparable over time. Metrics: evals/rubric.md.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fitcheck.labels import Label, load_label, sha256_of
from fitcheck.pipeline import check_posting
from fitcheck.profile import Profile
from fitcheck.providers.base import CallLog, Provider
from fitcheck.providers.pricing import cost_usd
from fitcheck.resume import Resume
from fitcheck.schemas import FitResult, Kind, NumberedRequirement, Status
from fitcheck.textmatch import appears_in

EVALS_DIR = Path("evals")
FROZEN_RESUME = EVALS_DIR / "fixtures" / "eval_resume_v1.yaml"
REPORTS_DIR = EVALS_DIR / "reports"  # one markdown file per run, so a report never needs a rerun
_RANK = {Status.GAP: 0, Status.WEAK: 1, Status.HAVE: 2}


class CaseError(ValueError):
    """A label or posting can't be evaluated as-is. The message says what to fix."""


@dataclass(frozen=True)
class Case:
    name: str  # "002"
    label: Label
    posting: str


@dataclass
class CaseReport:
    name: str
    labeled: int = 0          # labeled items (all kinds)
    found: int = 0            # labeled items the pipeline extracted
    missed: list[str] = field(default_factory=list)   # labeled quotes not found
    extra: list[str] = field(default_factory=list)    # extracted texts not in the label
    section_checked: int = 0
    section_right: int = 0
    status_checked: int = 0   # labeled skill items found and extracted as skills
    false_matches: list[str] = field(default_factory=list)  # pipeline status better than the label
    false_gaps: list[str] = field(default_factory=list)     # pipeline status worse than the label
    kind_wrong: list[str] = field(default_factory=list)
    category_fills: list[str] = field(default_factory=list)  # skill items whose skills code filled (#2b)
    citations: int = 0
    caught: int = 0
    decision: tuple[str, str] = ("", "")       # (pipeline, label)
    eligibility: tuple[str, str] = ("", "")
    cost_usd: float = 0.0     # original cost, even for cached answers
    latency_s: float = 0.0    # original latency
    calls: int = 0
    model: str = ""
    error: str | None = None  # set when the run stopped early (budget, setup)

    @property
    def decision_agrees(self) -> bool:
        return self.decision[0] == self.decision[1]

    @property
    def eligibility_agrees(self) -> bool:
        return self.eligibility[0] == self.eligibility[1]


def load_cases(evals_dir: Path = EVALS_DIR) -> list[Case]:
    cases = []
    for label_path in sorted((evals_dir / "labels").glob("*.yaml")):
        label = load_label(label_path)
        posting_path = evals_dir / "postings" / label.posting
        if not posting_path.is_file():
            raise CaseError(f"{label_path.name}: no posting at {posting_path}")
        if sha256_of(posting_path) != label.posting_sha256:
            raise CaseError(f"{label_path.name}: {posting_path} changed since it was labeled (fingerprint differs)")
        posting = posting_path.read_text(encoding="utf-8")
        bad = [r.quote for r in label.requirements if not appears_in(r.quote, posting)]
        if bad:
            raise CaseError(f"{label_path.name}: quotes not in the posting: {bad}")
        cases.append(Case(label_path.stem, label, posting))
    return cases


def run_case(case: Case, provider: Provider, resume: Resume, profile: Profile, failure_log: Path) -> CaseReport:
    checked = check_posting(case.posting, provider, resume, profile, failure_log)
    report = compare(case, checked.extraction.requirements, checked.result)
    report.citations, report.caught = checked.verification.citations, checked.verification.caught
    report.category_fills = checked.extraction.category_fills
    report.model = checked.logs[-1].model if checked.logs else ""
    report.cost_usd = _original_cost(checked.logs)
    report.latency_s = sum(log.latency_s for log in checked.logs)
    report.calls = checked.calls
    return report


def compare(case: Case, requirements: list[NumberedRequirement], result: FitResult) -> CaseReport:
    """Pair each labeled item with an extracted one by quote, then compare section, kind, and status."""
    report = CaseReport(case.name, labeled=len(case.label.requirements))
    status_of = {r.requirement_id: r.status for r in result.requirements}
    unused = list(requirements)
    for item in case.label.requirements:
        paired = next(
            (n for n in unused if appears_in(item.quote, n.requirement.text) or appears_in(n.requirement.text, item.quote)),
            None,
        )
        if paired is None:
            report.missed.append(item.quote)
            continue
        unused.remove(paired)
        report.found += 1
        req = paired.requirement
        if req.kind != item.kind:
            report.kind_wrong.append(f"{item.quote!r}: labeled {item.kind.value}, extracted {req.kind.value}")
            continue
        if item.section is not None:
            report.section_checked += 1
            report.section_right += req.section == item.section
        if item.kind == Kind.SKILL:
            predicted = status_of.get(paired.id)
            if predicted is None:  # review_manually: nothing was scored, so there's no status to compare
                continue
            report.status_checked += 1
            note = f"{item.quote!r}: labeled {item.status.value}, pipeline {predicted.value}"
            if _RANK[predicted] > _RANK[item.status]:
                report.false_matches.append(note)
            elif _RANK[predicted] < _RANK[item.status]:
                report.false_gaps.append(note)
    report.extra = [n.requirement.text for n in unused]
    report.decision = (result.decision.value, case.label.decision.value)
    report.eligibility = (result.eligibility.value, case.label.eligibility.value)
    return report


def _original_cost(logs: list[CallLog]) -> float:
    return sum(cost_usd(log.model, log.input_tokens, log.output_tokens) for log in logs)


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def format_report(reports: list[CaseReport]) -> str:
    done = [r for r in reports if r.error is None]
    lines = ["| case | recall | section | false matches | false gaps | evidence valid | decision | eligibility "
             "| category fills | cost | latency |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        if r.error:
            lines.append(f"| {r.name} | stopped: {r.error} ||||||||||")
            continue
        lines.append(
            f"| {r.name} | {r.found}/{r.labeled} | {r.section_right}/{r.section_checked} "
            f"| {len(r.false_matches)} | {len(r.false_gaps)} | {_pct(r.citations - r.caught, r.citations)} "
            f"| {'✓' if r.decision_agrees else '✗'} {r.decision[0]} (label {r.decision[1]}) "
            f"| {'✓' if r.eligibility_agrees else '✗'} {r.eligibility[0]} (label {r.eligibility[1]}) "
            f"| {len(r.category_fills)} | ${r.cost_usd:.4f} | {r.latency_s:.1f}s |"
        )
    if done:
        total = lambda attr: sum(getattr(r, attr) for r in done)  # noqa: E731
        lines += [
            "",
            f"Extraction recall: {_pct(total('found'), total('labeled'))}"
            f" · Section accuracy: {_pct(total('section_right'), total('section_checked'))}",
            f"False matches: {sum(len(r.false_matches) for r in done)}"
            f" · False gaps: {sum(len(r.false_gaps) for r in done)}"
            f" (of {total('status_checked')} labeled skill items compared)",
            f"Evidence validity: {_pct(total('citations') - total('caught'), total('citations'))}"
            f" · Decision agreement: {sum(r.decision_agrees for r in done)}/{len(done)}"
            f" · Eligibility agreement: {sum(r.eligibility_agrees for r in done)}/{len(done)}",
            f"Category fills (empty skills filled from category): {sum(len(r.category_fills) for r in done)}",
            f"Cost: ${total('cost_usd'):.4f} total, ${total('cost_usd') / len(done):.4f} per posting (original)"
            f" · Latency: {total('latency_s') / len(done):.1f}s per posting",
        ]
    details = []
    for r in done:
        for title, items in (("FALSE MATCH", r.false_matches), ("false gap", r.false_gaps),
                             ("kind wrong", r.kind_wrong), ("category fill", r.category_fills),
                             ("missed", r.missed), ("extra (not scored)", r.extra)):
            details += [f"- {r.name} {title}: {item}" for item in items]
    if details:
        lines += ["", "Details:"] + details
    return "\n".join(lines)


def _summary(reports: list[CaseReport]) -> dict[str, str]:
    """One model's headline numbers, for the side-by-side table. Stopped cases are left out."""
    done = [r for r in reports if r.error is None]
    if not done:
        stopped = next((r.error for r in reports if r.error), "no cases")
        return {"stopped": stopped}
    total = lambda attr: sum(getattr(r, attr) for r in done)  # noqa: E731
    cost = total("cost_usd")
    return {
        "cases run": f"{len(done)}/{len(reports)}" if len(done) < len(reports) else str(len(done)),
        "extraction recall": _pct(total("found"), total("labeled")),
        "section accuracy": _pct(total("section_right"), total("section_checked")),
        "kind wrong": str(sum(len(r.kind_wrong) for r in done)),
        "FALSE MATCHES": str(sum(len(r.false_matches) for r in done)),
        "false gaps": str(sum(len(r.false_gaps) for r in done)),
        "evidence validity": _pct(total("citations") - total("caught"), total("citations")),
        "decision agreement": f"{sum(r.decision_agrees for r in done)}/{len(done)}",
        "eligibility agreement": f"{sum(r.eligibility_agrees for r in done)}/{len(done)}",
        "calls (incl. retries)": str(total("calls")),
        "cost per posting": f"${cost / len(done):.4f}",
        "latency per posting": f"{total('latency_s') / len(done):.1f}s",
    }


def format_comparison(runs: dict[str, list[CaseReport]]) -> str:
    """M7: a metric-by-model table, then each model's full per-case report."""
    summaries = {name: _summary(reports) for name, reports in runs.items()}
    columns = []
    for name, reports in runs.items():
        model = next((r.model for r in reports if r.model), "")
        columns.append(f"{name} ({model})" if model else name)  # "gemini (gemini-3.8-flash)"
    metrics = list(dict.fromkeys(m for s in summaries.values() for m in s))  # every metric, in order
    lines = ["| metric | " + " | ".join(columns) + " |", "|---|" + "---|" * len(columns)]
    for metric in metrics:
        lines.append(f"| {metric} | " + " | ".join(s.get(metric, "") for s in summaries.values()) + " |")
    for column, reports in zip(columns, runs.values()):
        lines += ["", f"## {column}", "", format_report(reports)]
    return "\n".join(lines)


def save_report(reports: list[CaseReport], body: str, reports_dir: Path = REPORTS_DIR,
                now: datetime | None = None) -> Path:
    """Write the report with a header (time, model, resume, cases) to evals/reports/YYYY-MM-DD_HHMMSS.md."""
    now = now or datetime.now()
    models = sorted({r.model for r in reports if r.model}) or ["none (no calls)"]
    header = (f"# Eval report, {now:%Y-%m-%d %H:%M}\n\n"
              f"Model: {', '.join(models)} · Resume: {FROZEN_RESUME.stem} · "
              f"Cases: {', '.join(r.name for r in reports)}\n\n")
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{now:%Y-%m-%d_%H%M%S}.md"
    path.write_text(header + body + "\n", encoding="utf-8")
    return path
