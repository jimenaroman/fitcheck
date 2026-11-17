"""Offline matching tests: FakeProvider stands in for the model, so these cost nothing."""

from pathlib import Path

from fitcheck.match import SYSTEM_PROMPT, match
from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.resume import load_resume
from fitcheck.schemas import DecisionReason, NumberedRequirement, Requirement

RESUME = load_resume(Path("evals/fixtures/eval_resume_v1.yaml"))


def numbered(req_id, **fields):
    base = {"text": "Python or Java", "section": "required", "kind": "skill",
            "skills": ["python", "java"], "operator": "or", "type": "named_tool"}
    return NumberedRequirement(id=req_id, requirement=Requirement(**(base | fields)))


SOFT = numbered("r2", text="Strong communication", kind="soft", skills=[], operator=None, type=None)
GRAD = numbered("r3", text="Graduating in 2029", kind="eligibility", skills=[], operator=None, type=None,
                eligibility_type="graduation", grad_earliest="2029-01", grad_latest="2029-12")
REQS = [numbered("r1"), SOFT, GRAD]


def m(req_id, skill, relation="exact", evidence_id="proj-finance-b1", quote="Built a Streamlit budget app"):
    if relation == "none":
        evidence_id = quote = None
    return {"requirement_id": req_id, "skill": skill, "evidence_id": evidence_id,
            "quote": quote, "relation": relation, "reason": "test"}


def answer(*matches):
    return {"matches": list(matches)}


GOOD = answer(m("r1", "python"), m("r1", "java", "taught", "exp-tutoring-b1", "Taught Java fundamentals"))


def test_returns_one_match_per_skill():
    outcome = match(REQS, RESUME, FakeProvider([GOOD]))
    assert outcome.decision_reason is None
    assert [(x.requirement_id, x.skill, x.relation) for x in outcome.matches] == [
        ("r1", "python", "exact"), ("r1", "java", "taught")]


def test_sends_only_skill_items_and_wrapped_resume():
    provider = FakeProvider([GOOD])
    match(REQS, RESUME, provider)
    sent = provider.calls[0]
    assert sent.system == SYSTEM_PROMPT
    assert "[r1]" in sent.user and "[r2]" not in sent.user and "[r3]" not in sent.user
    assert sent.user.startswith("<resume>\n") and "</resume>\n\n<requirements>" in sent.user
    assert "Riley" not in sent.user


def test_examples_and_years_are_sent_as_hints():
    req = numbered("r1", text="3+ years with visualization tools such as Tableau", skills=["data visualization"],
                   operator=None, type="category", examples=["tableau"], min_years=3)
    provider = FakeProvider([answer(m("r1", "data visualization"))])
    match([req], RESUME, provider)
    assert "examples: tableau" in provider.calls[0].user and "years: 3+" in provider.calls[0].user


def test_missing_skill_is_retried_with_feedback():
    provider = FakeProvider([answer(m("r1", "python")), GOOD])
    outcome = match(REQS, RESUME, provider)
    assert outcome.decision_reason is None and len(provider.calls) == 2
    assert "missing" in provider.calls[1].user and "java" in provider.calls[1].user


def test_invented_and_repeated_pairs_are_rejected():
    bad = answer(m("r1", "python"), m("r1", "python"), m("r1", "java"), m("r9", "rust", "none"))
    provider = FakeProvider([bad, GOOD])
    outcome = match(REQS, RESUME, provider)
    assert "more than once" in outcome.errors[0] and "not in the requirements" in outcome.errors[0]


def test_aliased_skill_name_from_model_is_accepted():
    req = numbered("r1", text="JavaScript", skills=["javascript"], operator=None)
    outcome = match([req], RESUME, FakeProvider([answer(m("r1", "JS", "none"))]))
    assert outcome.decision_reason is None and outcome.matches[0].skill == "javascript"


def test_two_failures_return_invalid_output_without_a_third_call():
    provider = FakeProvider([InvalidOutputError("bad json"), answer(), GOOD])
    outcome = match(REQS, RESUME, provider)
    assert outcome.decision_reason == DecisionReason.INVALID_OUTPUT and outcome.matches == []
    assert len(provider.calls) == 2


def test_no_skill_items_means_no_call():
    provider = FakeProvider([])
    outcome = match([SOFT, GRAD], RESUME, provider)
    assert outcome.matches == [] and outcome.decision_reason is None and provider.calls == []


def test_bad_quote_is_not_retried_here():
    # Quote checking is verify.py's job (M5, R5 -> gap). Matching only checks coverage.
    fake_quote = answer(m("r1", "python", quote="Built a React app"), m("r1", "java", "none"))
    provider = FakeProvider([fake_quote])
    assert match(REQS, RESUME, provider).decision_reason is None and len(provider.calls) == 1


def test_prompt_explains_skills_shown_on_bullets():
    assert '"skills shown here: <skills>"' in SYSTEM_PROMPT
