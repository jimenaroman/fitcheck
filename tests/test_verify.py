"""verify.py against the frozen resume: every R4/R5/R5b row in decision_rules.md section 8."""

from pathlib import Path

import pytest

from fitcheck.resume import load_resume
from fitcheck.schemas import NumberedRequirement, Requirement, SkillMatch
from fitcheck.textmatch import appears_in, names_term
from fitcheck.verify import verify

RESUME = load_resume(Path("evals/fixtures/eval_resume_v1.yaml"))  # read only


def req(req_id="r1", type_="named_tool", skills=("python",)):
    return NumberedRequirement(id=req_id, requirement=Requirement(
        text="x", section="required", kind="skill", skills=list(skills),
        operator="or" if len(skills) > 1 else None, type=type_))


def cite(evidence_id, quote, relation="exact", skill="python", req_id="r1"):
    if relation == "none":
        evidence_id = quote = None
    return SkillMatch(requirement_id=req_id, skill=skill, evidence_id=evidence_id,
                      quote=quote, relation=relation, reason="test")


def check(match, requirement=None):
    result = verify([match], [requirement or req()], RESUME)
    return result.matches[0], result


# ---------- R4: real quotes hold up ----------

def test_real_quote_from_linked_bullet_holds_up():
    v, result = check(cite("proj-finance-b1", "Built a Streamlit budget app"))
    assert v.caught is None and v.evidence_kind == "bullet"
    assert (result.citations, result.caught) == (1, 0)


def test_quote_check_ignores_line_breaks_and_case():
    v, _ = check(cite("proj-finance-b1", "built a STREAMLIT\nbudget   app"))
    assert v.caught is None


def test_relation_none_has_nothing_to_check():
    v, result = check(cite(None, None, relation="none"))
    assert v.caught is None and v.evidence_kind is None and result.citations == 0


def test_evidence_kind_is_passed_on_for_r6_and_r7():
    course, _ = check(cite("course-cs2", "Intro to Computer Science II", skill="data structures"),
                      req(type_="knowledge", skills=("data structures",)))
    listed, _ = check(cite("skill-python", "Python"))
    assert course.evidence_kind == "course" and listed.evidence_kind == "skill"


# ---------- R5: invented evidence is caught ----------

def test_quote_not_in_cited_item_is_caught():
    v, result = check(cite("proj-finance-b2", "Built a React dashboard", skill="react"),
                      req(skills=("react",)))
    assert v.caught.startswith("R5:") and result.caught == 1


def test_quote_from_a_different_item_is_caught():
    # The text is real, but it's in proj-finance-b1, not the cited b2.
    v, _ = check(cite("proj-finance-b2", "Built a Streamlit budget app"))
    assert v.caught.startswith("R5:")


def test_unknown_id_is_caught():
    v, _ = check(cite("proj-nope-b9", "anything"))
    assert v.caught.startswith("R5:") and v.evidence_kind is None


# ---------- R5b: an exact named-tool match must be about the tool ----------

def test_real_quote_that_doesnt_name_the_tool_is_caught():
    v, _ = check(cite("exp-cafe-b1", "Served the morning rush line"))
    assert v.caught.startswith("R5b:")


def test_bullet_that_names_the_tool_holds_up():
    v, _ = check(cite("exp-tutoring-b1", "Taught Java fundamentals", skill="java"), req(skills=("java",)))
    assert v.caught is None


def test_alias_counts_as_naming_the_tool(tmp_path):
    path = tmp_path / "resume.yaml"
    path.write_text('projects:\n  - name: A\n    bullets:\n      - {id: b1, text: "Wrote JS widgets"}\n')
    v = verify([cite("b1", "Wrote JS widgets", skill="javascript")], [req(skills=("javascript",))],
               load_resume(path)).matches[0]
    assert v.caught is None


def test_r5b_only_applies_to_exact_named_tool_matches():
    # Category matches need judgment (M1): a Streamlit app is exact for "web development".
    category = cite("proj-finance-b1", "Built a Streamlit budget app", skill="web development")
    v, _ = check(category, req(type_="category", skills=("web development",)))
    assert v.caught is None
    weaker = cite("exp-cafe-b2", "Showed new baristas", relation="smaller_scale")
    v, _ = check(weaker)
    assert v.caught is None


# ---------- textmatch ----------

@pytest.mark.parametrize("text, term, found", [
    ("Wrote Go services", "go", True),
    ("A good team", "go", False),
    ("Used Node.js daily", "node.js", True),
    ("C++ and Java", "c++", True),
    ("Java", "javascript", False),
])
def test_names_term_matches_whole_words_only(text, term, found):
    assert names_term(text, term) is found


def test_appears_in_is_the_shared_verbatim_rule():
    assert appears_in("Python  OR\njava", "Experience with Python or Java")


def test_quote_from_the_skills_shown_line_is_caught():
    # The prompt shows "skills shown here: ... Python ..." under proj-finance-b1, but that line
    # isn't the bullet's text: quoting it is not verbatim evidence (R5).
    v, result = check(cite("proj-finance-b1", "Python"))
    assert v.caught is not None and result.caught == 1
