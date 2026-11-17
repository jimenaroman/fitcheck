"""The eval harness, offline: a temporary evals/ folder with a made-up posting and label."""

import shutil
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from fitcheck.cli import run_eval
from fitcheck.config import MissingKeyError
from fitcheck.evaluate import CaseError, compare, load_cases
from fitcheck.labels import sha256_of
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.schemas import FitResult, NumberedRequirement, Requirement, RequirementResult

POSTING = """Required
Experience with Python
Experience with Kubernetes
Strong communication
Preferred
Linux a plus
"""
LABEL_ITEMS = [
    {"quote": "Experience with Python", "kind": "skill", "section": "required", "status": "have"},
    {"quote": "Experience with Kubernetes", "kind": "skill", "section": "required", "status": "gap"},
    {"quote": "Strong communication", "kind": "soft", "section": "required"},
    {"quote": "Linux a plus", "kind": "skill", "section": "preferred", "status": "gap"},
]


@pytest.fixture
def evals(tmp_path):
    (tmp_path / "postings").mkdir()
    (tmp_path / "labels").mkdir()
    (tmp_path / "fixtures").mkdir()
    shutil.copy("evals/fixtures/eval_resume_v1.yaml", tmp_path / "fixtures")  # a copy; the original is untouched
    posting = tmp_path / "postings" / "001.txt"
    posting.write_text(POSTING)
    write_label(tmp_path, posting_sha256=sha256_of(posting))
    return tmp_path


def write_label(root, **changes):
    label = {
        "posting": "001.txt", "source_url": "https://example.com/job", "saved_on": "2026-11-05",
        "posting_sha256": "0" * 64, "is_job_posting": True, "has_section_split": True,
        "requirements": LABEL_ITEMS, "decision": "apply", "eligibility": "eligible",
    } | changes
    (root / "labels" / "001.yaml").write_text(yaml.safe_dump(label, sort_keys=False))


# ---------- Loading refuses anything that can't be trusted ----------

def test_loads_a_valid_case(evals):
    (case,) = load_cases(evals)
    assert case.name == "001" and case.posting == POSTING


def test_changed_posting_is_refused(evals):
    (evals / "postings" / "001.txt").write_text(POSTING + "Rust required\n")
    with pytest.raises(CaseError, match="changed since it was labeled"):
        load_cases(evals)


def test_missing_posting_is_refused(evals):
    (evals / "postings" / "001.txt").unlink()
    with pytest.raises(CaseError, match="no posting"):
        load_cases(evals)


def test_quote_not_in_posting_is_refused(evals):
    items = LABEL_ITEMS + [{"quote": "Experience with Go", "kind": "skill", "section": "required", "status": "gap"}]
    write_label(evals, posting_sha256=sha256_of(evals / "postings" / "001.txt"), requirements=items)
    with pytest.raises(CaseError, match="Experience with Go"):
        load_cases(evals)


# ---------- compare(): pairing and the metrics ----------

def numbered(req_id, text, kind="skill", section="required"):
    fields = {"skills": ["x"], "type": "named_tool"} if kind == "skill" else {}
    return NumberedRequirement(id=req_id, requirement=Requirement(text=text, section=section, kind=kind, **fields))


def result(statuses, decision="apply", eligibility="eligible"):
    return FitResult(decision=decision, eligibility=eligibility, requirements=[
        RequirementResult(requirement_id=i, status=s) for i, s in statuses.items()])


def test_compare_counts_false_matches_false_gaps_missed_and_extra(evals):
    (case,) = load_cases(evals)
    extracted = [
        numbered("r1", "Experience with Python"),           # labeled have, pipeline gap -> false gap
        numbered("r2", "Experience with Kubernetes"),       # labeled gap, pipeline weak -> FALSE MATCH
        numbered("r3", "Linux a plus", section="required"), # section wrong (labeled preferred)
        NumberedRequirement(id="r4", requirement=Requirement(text="Bachelor's degree", section="required",
                        kind="eligibility", eligibility_type="degree")),  # not in the label -> extra
    ]                                                       # "Strong communication" not extracted -> missed
    report = compare(case, extracted, result({"r1": "gap", "r2": "weak", "r3": "gap"}))
    assert (report.found, report.labeled) == (3, 4)
    assert report.missed == ["Strong communication"]
    assert report.extra == ["Bachelor's degree"]
    assert (report.section_right, report.section_checked) == (2, 3)
    assert len(report.false_matches) == 1 and "Kubernetes" in report.false_matches[0]
    assert len(report.false_gaps) == 1 and "Python" in report.false_gaps[0]


def test_pairing_works_when_the_extracted_text_is_longer_or_shorter(evals):
    (case,) = load_cases(evals)
    extracted = [numbered("r1", "Required: Experience with Python daily"),  # contains the quote
                 numbered("r2", "Kubernetes")]                               # inside the quote
    report = compare(case, extracted, result({"r1": "have", "r2": "gap"}))
    assert report.found == 2 and report.false_matches == report.false_gaps == []


def test_wrong_kind_is_reported_not_scored_as_a_status(evals):
    (case,) = load_cases(evals)
    report = compare(case, [numbered("r1", "Strong communication")], result({"r1": "gap"}))
    assert report.kind_wrong and report.false_gaps == []


def test_review_manually_compares_no_statuses_instead_of_crashing(evals):
    (case,) = load_cases(evals)
    failed = FitResult(decision="review_manually", decision_reason="invalid_output", eligibility="eligible")
    report = compare(case, [numbered("r1", "Experience with Python")], failed)
    assert report.found == 1 and report.status_checked == 0 and report.decision[0] == "review_manually"


def test_decision_and_eligibility_agreement(evals):
    (case,) = load_cases(evals)
    report = compare(case, [], result({}, decision="dont_apply", eligibility="unclear"))
    assert not report.decision_agrees and not report.eligibility_agrees


# ---------- The command ----------

def args(**changes):
    return Namespace(**({"check": False, "only": None, "profile": Path("profile.example.yaml"),
                         "provider": "claude"} | changes))


def extraction_answer():
    reqs = [
        {"text": "Experience with Python", "section": "required", "kind": "skill", "skills": ["python"], "type": "named_tool"},
        {"text": "Experience with Kubernetes", "section": "required", "kind": "skill", "skills": ["kubernetes"], "type": "named_tool"},
        {"text": "Strong communication", "section": "required", "kind": "soft"},
        {"text": "Linux a plus", "section": "preferred", "kind": "skill", "skills": ["linux"], "type": "named_tool"},
    ]
    return {"is_job_posting": True, "has_section_split": True, "requirements": reqs}


def matching_answer():
    def m(rid, skill, eid=None, quote=None, rel="none"):
        return {"requirement_id": rid, "skill": skill, "evidence_id": eid, "quote": quote, "relation": rel, "reason": "t"}
    return {"matches": [m("r1", "python", "proj-finance-b1", "Built a Streamlit budget app", "exact"),
                        m("r2", "kubernetes"), m("r4", "linux")]}


def test_check_mode_validates_without_calls(evals, capsys):
    provider = FakeProvider([])
    assert run_eval(args(check=True), provider, evals / "f.jsonl", evals) == 0
    assert "1 case(s) OK" in capsys.readouterr().out and provider.calls == []


def test_full_run_reports_a_perfect_case(evals, capsys):
    provider = FakeProvider([extraction_answer(), matching_answer()])
    assert run_eval(args(), provider, evals / "f.jsonl", evals) == 0
    out = capsys.readouterr().out
    assert "| 001 | 4/4 | 4/4 | 0 | 0 | 100% | ✓ apply (label apply) | ✓ eligible (label eligible)" in out
    assert "Extraction recall: 100%" in out and "False matches: 0" in out


def test_budget_limit_stops_the_run_cleanly(evals, capsys):
    provider = FakeProvider([BudgetExceededError("Limit reached: 20 calls in the last hour.")])
    assert run_eval(args(), provider, evals / "f.jsonl", evals) == 0
    assert "stopped: Limit reached" in capsys.readouterr().out


def test_every_run_is_saved_to_reports(evals, capsys):
    provider = FakeProvider([extraction_answer(), matching_answer()])
    run_eval(args(), provider, evals / "f.jsonl", evals)
    [saved] = (evals / "reports").glob("*.md")
    text = saved.read_text()
    assert text.startswith("# Eval report, ") and "Model: fake · Resume: eval_resume_v1 · Cases: 001" in text
    assert "| 001 | 4/4 |" in text and f"Saved to {saved}" in capsys.readouterr().out


def test_check_mode_saves_nothing(evals):
    run_eval(args(check=True), FakeProvider([]), evals / "f.jsonl", evals)
    assert not (evals / "reports").exists()


def test_category_fills_are_counted_in_the_report(evals, capsys):
    answer = extraction_answer()
    answer["requirements"][1] |= {"skills": [" "], "type": "category", "category": "kubernetes"}
    run_eval(args(), FakeProvider([answer, matching_answer()]), evals / "f.jsonl", evals)
    out = capsys.readouterr().out
    assert "| 1 | $" in out  # the category fills column
    assert "Category fills (empty skills filled from category): 1" in out
    assert "- 001 category fill: Experience with Kubernetes" in out


# ---------- M7: two models side by side ----------

def test_both_providers_get_a_side_by_side_comparison(evals, capsys):
    providers = {"claude": FakeProvider([extraction_answer(), matching_answer()]),
                 "gemini": FakeProvider([extraction_answer(), matching_answer()])}
    assert run_eval(args(provider="both"), None, evals / "f.jsonl", evals, providers=providers) == 0
    out = capsys.readouterr().out
    assert "| metric | claude (fake) | gemini (fake) |" in out
    assert "| extraction recall | 100% | 100% |" in out and "| FALSE MATCHES | 0 | 0 |" in out
    assert "## claude (fake)" in out and "## gemini (fake)" in out
    [saved] = (evals / "reports").glob("*.md")
    assert "| metric | claude (fake) | gemini (fake) |" in saved.read_text()


def test_a_missing_key_stops_only_that_provider(evals, capsys, monkeypatch):
    def no_gemini_key(name):
        raise MissingKeyError("GEMINI_API_KEY is not set. Copy .env.example to .env and fill it in.")
    monkeypatch.setattr("fitcheck.cli.default_provider", no_gemini_key)
    providers = {"claude": FakeProvider([extraction_answer(), matching_answer()])}
    run_eval(args(provider="both"), None, evals / "f.jsonl", evals, providers=providers)
    out = capsys.readouterr().out
    assert "| extraction recall | 100% |  |" in out
    assert "| stopped |  | GEMINI_API_KEY is not set." in out
