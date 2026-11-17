"""CLI tests: FakeProvider is passed in, so nothing here needs a key or costs money."""

import pytest

from fitcheck.cli import main
from fitcheck.providers.budget import BudgetExceededError
from fitcheck.providers.fake_provider import FakeProvider

POSTING = "Basic qualifications:\nExperience with JS or Python\n"


def answer(split=True, is_posting=True):
    req = {
        "text": "Experience with JS or Python", "section": "required", "kind": "skill",
        "skills": ["js", "python"], "operator": "or", "type": "named_tool",
    }
    return {"is_job_posting": is_posting, "has_section_split": split,
            "requirements": [req] if is_posting else []}


def run(tmp_path, capsys, responses, text=POSTING):
    path = tmp_path / "posting.txt"
    path.write_text(text)
    code = main(["extract", str(path)], provider=FakeProvider(responses), failure_log=tmp_path / "failures.jsonl")
    out, err = capsys.readouterr()
    return code, out, err


def test_prints_numbered_requirements(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, [answer()])
    assert code == 0
    assert "r1" in out and '"Experience with JS or Python"' in out
    assert "skills: javascript or python" in out  # aliases applied
    assert "Cost: $0.0000" in out
    assert "split unclear" not in out


def test_flags_missing_split(tmp_path, capsys):
    _, out, _ = run(tmp_path, capsys, [answer(split=False)])
    assert "Required/preferred split unclear" in out


def test_not_a_posting_says_review_manually(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, [answer(is_posting=False)], text="Great article about AI\n")
    assert code == 0 and "doesn't look like a job posting" in out


def test_two_failures_say_review_manually(tmp_path, capsys):
    code, out, _ = run(tmp_path, capsys, [{"bad": 1}, {"bad": 2}])
    assert code == 1
    assert "Review manually" in out and "attempt 2" in out


def test_budget_limit_is_a_plain_message(tmp_path, capsys):
    code, out, err = run(tmp_path, capsys, [BudgetExceededError("Limit reached: 20 calls")])
    assert code == 2 and "Limit reached" in err and "Review manually" not in out


def test_missing_file(tmp_path, capsys):
    code = main(["extract", str(tmp_path / "nope.txt")], provider=FakeProvider([]))
    assert code == 2 and "No file" in capsys.readouterr().err


# ---------- fitcheck match ----------

FROZEN = "evals/fixtures/eval_resume_v1.yaml"  # read only


def run_match(tmp_path, capsys, responses, resume=FROZEN):
    path = tmp_path / "posting.txt"
    path.write_text(POSTING)
    provider = FakeProvider(responses)
    code = main(["match", str(path), "--resume", str(resume)], provider=provider,
                failure_log=tmp_path / "failures.jsonl")
    out, err = capsys.readouterr()
    return code, out, err, provider


def matches(*items):
    return {"matches": [
        {"requirement_id": "r1", "skill": skill, "evidence_id": eid, "quote": quote,
         "relation": rel, "reason": "test"} for skill, rel, eid, quote in items
    ]}


def test_match_prints_evidence_per_skill(tmp_path, capsys):
    code, out, _, provider = run_match(tmp_path, capsys, [answer(), matches(
        ("javascript", "none", None, None),
        ("python", "exact", "proj-finance-b1", "Built a Streamlit budget app"),
    )])
    assert code == 0 and len(provider.calls) == 2  # one extraction, one matching
    assert "javascript: none" in out and "python: exact" in out
    assert 'proj-finance-b1: "Built a Streamlit budget app"' in out


def test_match_skips_the_matching_call_for_non_postings(tmp_path, capsys):
    code, out, _, provider = run_match(tmp_path, capsys, [answer(is_posting=False)])
    assert "doesn't look like a job posting" in out and len(provider.calls) == 1


def test_match_reports_unusable_resume_before_any_call(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text('projects:\n  - name: A\n    bullets:\n      - {id: b1, text: "me@example.com"}\n')
    code, _, err, provider = run_match(tmp_path, capsys, [], resume=bad)
    assert code == 2 and "contact.private.yaml" in err and provider.calls == []


def test_match_missing_resume_file(tmp_path, capsys):
    code, _, err, _ = run_match(tmp_path, capsys, [], resume=tmp_path / "nope.yaml")
    assert code == 2 and "No file" in err


# ---------- Rejected attempts are visible and logged ----------

def test_rejected_first_attempt_is_shown_and_logged_even_when_retry_succeeds(tmp_path, capsys):
    from fitcheck.providers.base import InvalidOutputError

    code, out, _ = run(tmp_path, capsys, [InvalidOutputError("bad json"), answer()])
    assert code == 0
    assert "extraction attempt 1 rejected: bad json" in out
    assert "2 call(s)" in out and "1 failed call(s) not priced" in out
    (line,) = (tmp_path / "failures.jsonl").read_text().splitlines()
    assert '"step": "extract"' in line and '"attempt": 1' in line


def test_clean_run_writes_no_failure_log(tmp_path, capsys):
    run(tmp_path, capsys, [answer()])
    assert not (tmp_path / "failures.jsonl").exists()


# ---------- fitcheck check ----------

def test_check_prints_the_card(tmp_path, capsys):
    path = tmp_path / "posting.txt"
    path.write_text(POSTING)
    profile = tmp_path / "profile.yaml"
    profile.write_text('expected_graduation: "2029-05"\n'
                       "work_authorization: {us_authorized: true, needs_sponsorship: false}\n")
    provider = FakeProvider([answer(), matches(
        ("javascript", "none", None, None),
        ("python", "exact", "proj-finance-b1", "Built a Streamlit budget app"),
    )])
    code = main(["check", str(path), "--resume", FROZEN, "--profile", str(profile)],
                provider=provider, failure_log=tmp_path / "f.jsonl")
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("APPLY\n")  # "JS or Python": python is have via a linked bullet
    assert "100% proven" in out and "python: have via proj-finance-b1" in out


def test_check_on_a_non_posting_makes_no_matching_call(tmp_path, capsys):
    path = tmp_path / "posting.txt"
    path.write_text("Great article\n")
    provider = FakeProvider([answer(is_posting=False)])
    main(["check", str(path), "--resume", FROZEN, "--profile", "profile.example.yaml"],
         provider=provider, failure_log=tmp_path / "f.jsonl")
    assert "REVIEW MANUALLY: this doesn't look like a job posting" in capsys.readouterr().out
    assert len(provider.calls) == 1


def test_check_bad_profile_fails_before_any_call(tmp_path, capsys):
    path = tmp_path / "posting.txt"
    path.write_text(POSTING)
    bad = tmp_path / "profile.yaml"
    bad.write_text('expected_graduation: "May 2029"\n')
    provider = FakeProvider([])
    code = main(["check", str(path), "--resume", FROZEN, "--profile", str(bad)],
                provider=provider, failure_log=tmp_path / "f.jsonl")
    assert code == 2 and provider.calls == []


def test_cost_line_shows_zero_spent_but_original_cost_for_cache_hits():
    from fitcheck.cli import format_cost
    from fitcheck.providers.base import CallLog

    hit = CallLog("anthropic", "claude-haiku-4-5", 1_000_000, 0, 2.0, cached=True)  # originally $1.00
    line = format_cost([hit], calls=1)
    assert line.startswith("Cost: $0.0000") and "originally $1.0000" in line and "0 in / 0 out" in line


def test_provider_option_builds_the_gemini_chain(monkeypatch):
    # Budget and cache wrap Gemini exactly like Claude. A dummy key: nothing is called.
    pytest.importorskip("google.genai", reason="Gemini is optional: uv sync --extra gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    from fitcheck.cli import default_provider
    from fitcheck.providers.gemini_provider import GeminiProvider
    provider = default_provider("gemini")
    assert isinstance(provider.inner.inner, GeminiProvider) and provider.model == "gemini-3.8-flash"


def test_every_command_accepts_provider(tmp_path):
    path = tmp_path / "p.txt"
    path.write_text("Python required")
    responses = [{"is_job_posting": True, "has_section_split": False, "requirements": []}]
    code = main(["extract", str(path), "--provider", "gemini"], provider=FakeProvider(responses),
                failure_log=tmp_path / "failures.jsonl")
    assert code == 0


def test_gemini_not_installed_says_how_to_install(tmp_path, monkeypatch, capsys):
    import builtins
    real_import = builtins.__import__

    def no_gemini(name, *args, **kwargs):
        if name == "fitcheck.providers.gemini_provider":
            raise ImportError("No module named 'google'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_gemini)
    path = tmp_path / "p.txt"
    path.write_text("Python required")
    assert main(["extract", str(path), "--provider", "gemini"]) == 2
    assert "uv sync --extra gemini" in capsys.readouterr().err


def test_import_resume_writes_a_draft_and_leaves_resume_yaml_alone(tmp_path, capsys):
    from test_resume_import import RESUME_LINES, model_answer
    source, out = tmp_path / "me.txt", tmp_path / "draft.yaml"
    source.write_text("\n".join(RESUME_LINES))
    code = main(["import-resume", str(source), "--out", str(out)], provider=FakeProvider([model_answer()]))
    assert code == 0 and "proj-bike-tracker-b1" in out.read_text()
    printed = capsys.readouterr().out
    assert "4 contact detail(s) removed" in printed and "Listed only" in printed


def test_import_resume_refuses_an_image(tmp_path, capsys):
    source = tmp_path / "me.png"
    source.write_bytes(b"\x89PNG")
    assert main(["import-resume", str(source)], provider=FakeProvider([])) == 1
    assert "Images aren't accepted" in capsys.readouterr().err
