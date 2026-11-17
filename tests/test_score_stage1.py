"""Stage 1 of score.py: the status table and the per-skill ladder (decision_rules.md sections 2, 3, 8).

These tests are the spec. If one seems wrong, the fix is to change decision_rules.md first.
"""

import pytest

from fitcheck.schemas import ReasonCode, Relation, ReqType, Requirement, SkillMatch, Status
from fitcheck.score import STATUS_TABLE, skill_status
from fitcheck.verify import VerifiedMatch

HAVE, WEAK, GAP = Status.HAVE, Status.WEAK, Status.GAP

# Section 2, row by row. Columns: category, named_tool, knowledge, hands_on. "*" cells are proposed.
SPEC_TABLE = {
    Relation.EXACT:               (HAVE, HAVE, HAVE, HAVE),
    Relation.SAME_CATEGORY:       (HAVE, WEAK, HAVE, WEAK),   # knowledge*, hands_on*
    Relation.SMALLER_SCALE:       (WEAK, WEAK, WEAK, WEAK),   # all * except hands_on
    Relation.TAUGHT:              (HAVE, HAVE, HAVE, HAVE),   # all * except named_tool
    Relation.COURSEWORK:          (WEAK, WEAK, HAVE, WEAK),   # category*, named_tool*
    Relation.DIFFERENT_TECHNIQUE: (GAP, GAP, GAP, GAP),
    Relation.NONE:                (GAP, GAP, GAP, GAP),
}
COLUMNS = (ReqType.CATEGORY, ReqType.NAMED_TOOL, ReqType.KNOWLEDGE, ReqType.HANDS_ON)
CELLS = [(rel, col, row[i]) for rel, row in SPEC_TABLE.items() for i, col in enumerate(COLUMNS)]


def requirement(type_="named_tool", skill="python", min_years=None, level=None):
    return Requirement(text="x", section="required", kind="skill", skills=[skill], type=type_,
                       min_years=min_years, level=level)


def verified(relation="exact", evidence_id="proj-finance-b1", quote="Built a Streamlit budget app",
             kind="bullet", caught=None, skill="python"):
    if relation == "none":
        evidence_id = quote = kind = None
    match = SkillMatch(requirement_id="r1", skill=skill, evidence_id=evidence_id, quote=quote,
                       relation=relation, reason="test")
    return VerifiedMatch(match, kind, caught)


# ---------- The table ----------

@pytest.mark.parametrize("relation, req_type, expected", CELLS, ids=[f"{r}-{c}" for r, c, _ in CELLS])
def test_status_table_matches_section_2(relation, req_type, expected):
    assert STATUS_TABLE[(relation, req_type)] == expected


def test_status_table_has_every_cell_and_nothing_else():
    assert set(STATUS_TABLE) == {(rel, col) for rel in Relation for col in ReqType}


# ---------- R4: verified bullet -> the table, with reason codes ----------

def test_proficiency_in_python_is_have_via_bullet():
    result = skill_status(verified(), requirement())  # section 8 row 1
    assert (result.status, result.reason_code) == (HAVE, None)
    assert (result.requirement_id, result.skill) == ("r1", "python")
    assert (result.evidence_id, result.quote, result.relation) == (
        "proj-finance-b1", "Built a Streamlit budget app", Relation.EXACT)


def test_web_development_is_have_via_streamlit_app():  # M1
    result = skill_status(verified(skill="web development"), requirement("category", "web development"))
    assert result.status == HAVE


def test_taught_java_is_have():  # M2
    v = verified("taught", "exp-tutoring-b1", "Taught Java fundamentals", skill="java")
    assert skill_status(v, requirement(skill="java")).status == HAVE


def test_similar_tool_is_weak_similar_tool():  # M3: "Tableau or D3 required", Plotly
    v = verified("same_category", "proj-finance-b2", "Designed Plotly visualizations", skill="tableau")
    result = skill_status(v, requirement(skill="tableau"))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.SIMILAR_TOOL)


def test_smaller_scale_is_weak_smaller_scale():  # M6: "Experience building data pipelines"
    v = verified("smaller_scale", skill="data pipelines")
    result = skill_status(v, requirement("hands_on", "data pipelines"))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.SMALLER_SCALE)


def test_different_technique_is_gap():  # M5: rule-based detector is not ML
    v = verified("different_technique", "proj-finance-b3", "Wrote a repeat-charge detector",
                 skill="machine learning")
    result = skill_status(v, requirement("category", "machine learning"))
    assert (result.status, result.reason_code) == (GAP, ReasonCode.NOT_ON_RESUME)  # R8b


def test_honor_citation_uses_the_table_like_a_bullet():
    v = verified(evidence_id="honor-deans-scholar", quote="Dean's Scholar", kind="honor", skill="leadership")
    assert skill_status(v, requirement("category", "leadership")).status == HAVE


# ---------- R5: caught citations ----------

def test_caught_citation_is_gap_false_match_caught_even_if_model_said_exact():
    result = skill_status(verified(caught="R5: quote not found in proj-finance-b2"), requirement())
    assert (result.status, result.reason_code) == (GAP, ReasonCode.FALSE_MATCH_CAUGHT)
    assert result.relation == Relation.EXACT  # what the model claimed is kept for evals


# ---------- R6: course is the only evidence ----------

def test_course_satisfies_knowledge():  # M4: "Solid understanding of data structures"
    v = verified("coursework", "course-cs2", "Intro to Computer Science II", "course", skill="data structures")
    assert skill_status(v, requirement("knowledge", "data structures")).status == HAVE


def test_course_is_weak_for_hands_on_even_if_model_said_exact():
    v = verified("exact", "course-cs2", "Intro to Computer Science II", "course", skill="data pipelines")
    result = skill_status(v, requirement("hands_on", "data pipelines"))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.COURSEWORK_ONLY)


# ---------- R7: skills list only ----------

def test_skills_list_only_is_weak_listed_not_shown():  # "Experience with SQL"
    v = verified("exact", "skill-sql", "SQL", "skill", skill="sql")
    result = skill_status(v, requirement(skill="sql"))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.LISTED_NOT_SHOWN)


def test_skills_list_entry_with_links_is_still_weak():  # R7, cited skill-python instead of the bullet
    result = skill_status(verified("exact", "skill-python", "Python", "skill"), requirement())
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.LISTED_NOT_SHOWN)


# ---------- R8: nothing ----------

def test_nothing_is_gap_not_on_resume():
    result = skill_status(verified("none"), requirement(skill="kubernetes"))
    assert (result.status, result.reason_code) == (GAP, ReasonCode.NOT_ON_RESUME)
    assert result.evidence_id is None and result.quote is None


# ---------- R9: years ----------

def test_years_not_in_quote_drops_have_to_weak():  # "2+ years of Java" via exp-tutoring-b1
    v = verified("taught", "exp-tutoring-b1", "Taught Java fundamentals", skill="java")
    result = skill_status(v, requirement(skill="java", min_years=2))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.YEARS_NOT_SHOWN)


def test_years_stated_in_quote_keep_have():
    v = verified(quote="2+ years building Java applications", skill="java")
    assert skill_status(v, requirement(skill="java", min_years=2)).status == HAVE


def test_too_few_years_in_quote_is_weak():
    v = verified(quote="1 year building Java applications", skill="java")
    result = skill_status(v, requirement(skill="java", min_years=2))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.YEARS_NOT_SHOWN)


def test_years_never_upgrade_a_weak_or_gap():
    v = verified("smaller_scale", quote="3 years of small scripts", skill="java")
    result = skill_status(v, requirement(skill="java", min_years=2))
    assert (result.status, result.reason_code) == (WEAK, ReasonCode.SMALLER_SCALE)


# ---------- R10: level words never change a status ----------

def test_expert_level_does_not_change_status():
    assert skill_status(verified(), requirement(level="expert")).status == HAVE
