import pytest
from pydantic import ValidationError

from fitcheck.schemas import (
    Decision,
    DecisionReason,
    Extraction,
    FitResult,
    MatchSet,
    Relation,
    Requirement,
    SkillMatch,
)


def skill_req(**overrides):
    fields = {
        "text": "Python or Java",
        "section": "required",
        "kind": "skill",
        "skills": ["Python", " Java "],
        "operator": "or",
        "type": "named_tool",
    }
    return Requirement(**(fields | overrides))


def grad_req(**overrides):
    fields = {
        "text": "Graduating Dec 2028 - Aug 2029",
        "section": "required",
        "kind": "eligibility",
        "eligibility_type": "graduation",
        "grad_earliest": "2028-12",
        "grad_latest": "2029-08",
    }
    return Requirement(**(fields | overrides))


def exact_match(**overrides):
    fields = {
        "requirement_id": "r1",
        "skill": "python",
        "evidence_id": "proj-finance-b1",
        "quote": "Built a Streamlit budget app",
        "relation": "exact",
        "reason": "Python project",
    }
    return SkillMatch(**(fields | overrides))


# ---------- Requirement ----------

def test_or_requirement_parses_and_lowercases_skills():
    req = skill_req()
    assert req.skills == ["python", "java"]
    assert req.operator == "or"


def test_grad_window_parses():
    req = grad_req()
    assert (req.grad_earliest, req.grad_latest) == ("2028-12", "2029-08")


@pytest.mark.parametrize("bad", ["2028", "2028-13", "2028-1", "Dec 2028"])
def test_bad_grad_date_rejected(bad):
    with pytest.raises(ValidationError):
        grad_req(grad_earliest=bad)


def test_grad_window_backwards_rejected():
    with pytest.raises(ValidationError):
        grad_req(grad_earliest="2029-08", grad_latest="2028-12")


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        skill_req(type="nice_to_have")


def test_eligibility_item_needs_eligibility_type():
    with pytest.raises(ValidationError):
        grad_req(eligibility_type=None, grad_earliest=None, grad_latest=None)


def test_skill_item_cannot_have_eligibility_fields():
    with pytest.raises(ValidationError):
        skill_req(locations=["Boston"])


def test_several_skills_need_operator():
    with pytest.raises(ValidationError):
        skill_req(operator=None)


def test_such_as_parses_as_category_with_examples():
    req = skill_req(
        text="Visualization tools such as Tableau or D3",
        skills=["data visualization"], operator=None, type="category", examples=["Tableau", "D3"],
    )
    assert req.examples == ["tableau", "d3"]


def test_knowledge_item_can_have_examples():
    req = skill_req(
        text="Knowledge of visualization tools such as Tableau or D3",
        skills=["data visualization"], operator=None, type="knowledge", examples=["tableau", "d3"],
    )
    assert req.type == "knowledge" and req.examples == ["tableau", "d3"]


def test_examples_rejected_on_non_skill_items():
    with pytest.raises(ValidationError):
        grad_req(examples=["tableau"])


def test_model_cannot_send_an_id():
    with pytest.raises(ValidationError):
        skill_req(id="r1")


# ---------- SkillMatch ----------

def test_exact_match_parses():
    assert exact_match().relation == Relation.EXACT


def test_typo_relation_rejected():
    with pytest.raises(ValidationError):
        exact_match(relation="exat")


def test_none_relation_with_quote_rejected():
    with pytest.raises(ValidationError):
        exact_match(relation="none")


def test_none_relation_without_evidence_parses():
    m = exact_match(relation="none", evidence_id=None, quote=None)
    assert m.relation == Relation.NONE


def test_exact_relation_without_quote_rejected():
    with pytest.raises(ValidationError):
        exact_match(quote=None)


def test_model_cannot_send_a_status():
    with pytest.raises(ValidationError):
        exact_match(status="have")


# ---------- The model-facing schemas contain no code-only fields ----------

def property_names(schema):
    """Every property name anywhere in a JSON schema, including nested $defs."""
    names = set()
    if isinstance(schema, dict):
        names |= set(schema.get("properties", {}))
        for value in schema.values():
            names |= property_names(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= property_names(item)
    return names


@pytest.mark.parametrize("model", [Extraction, MatchSet])
def test_model_facing_schema_has_no_code_only_fields(model):
    names = property_names(model.model_json_schema())
    assert names.isdisjoint({"status", "decision", "decision_reason", "reason_code", "id"})


# ---------- FitResult ----------

def test_fit_result_apply_parses():
    result = FitResult(
        decision="apply", required_total=5, required_have=3, required_weak=1,
        proven_pct=60.0, incl_weak_pct=80.0, eligibility="eligible",
    )
    assert result.decision == Decision.APPLY


def test_missing_eligibility_rejected():
    with pytest.raises(ValidationError):
        FitResult(decision="apply")


def test_unknown_decision_rejected():
    with pytest.raises(ValidationError):
        FitResult(decision="maybe", eligibility="eligible")


def test_review_manually_needs_reason():
    with pytest.raises(ValidationError):
        FitResult(decision="review_manually", eligibility="unclear")
    ok = FitResult(decision="review_manually", decision_reason="invalid_output", eligibility="unclear")
    assert ok.decision_reason == DecisionReason.INVALID_OUTPUT


def test_counts_cannot_exceed_total():
    with pytest.raises(ValidationError):
        FitResult(
            decision="apply", required_total=3, required_have=3, required_weak=1,
            eligibility="eligible",
        )


def test_numbered_skill_items_need_a_skill():
    # Requirement allows empty skills (extract.py fills them); NumberedRequirement doesn't.
    from fitcheck.schemas import NumberedRequirement, Requirement
    req = Requirement(text="Databases", section="required", kind="skill", type="category", category="databases")
    with pytest.raises(ValueError, match="at least one skill"):
        NumberedRequirement(id="r1", requirement=req)


# ---------- Extraction item shapes ----------

from anthropic import transform_schema  # noqa: E402

from fitcheck.schemas import Extraction, GraduationItem, SkillItem  # noqa: E402


def api_shapes():
    return transform_schema(Extraction.model_json_schema())["$defs"]


def test_api_schema_requires_every_key_and_at_least_one_skill():
    # The first eval run: `skills` was optional in the schema, and Haiku skipped it.
    skill = api_shapes()["SkillItem"]
    assert set(skill["required"]) == set(skill["properties"])
    assert skill["properties"]["skills"]["minItems"] == 1
    assert skill["properties"]["kind"]["enum"] == ["skill"]  # enforced, unlike "const"


def test_grad_dates_only_exist_on_graduation_items():
    # The 004 failure: grad dates on a degree item. Now only one shape has those fields.
    shapes = api_shapes()
    with_dates = [name for name, s in shapes.items() if "grad_earliest" in s.get("properties", {})]
    assert with_dates == ["GraduationItem"]
    with pytest.raises(ValueError):
        Extraction.model_validate({"is_job_posting": True, "has_section_split": True, "requirements": [
            {"kind": "eligibility", "text": "BS in CS", "section": "required",
             "eligibility_type": "degree", "grad_earliest": "2028-12"}]})


def test_items_convert_to_requirements():
    grad = GraduationItem(kind="eligibility", text="Dec 2028", section="required",
                          eligibility_type="graduation", grad_earliest="2028-12")
    assert grad.to_requirement().eligibility_type == "graduation"
    skill = SkillItem(kind="skill", text="Python", section="required", skills=["Python"], type="named_tool")
    assert skill.to_requirement().skills == ["python"]


def test_shapes_keep_requirement_rules():
    with pytest.raises(ValueError, match="operator"):
        SkillItem(kind="skill", text="Python and Go", section="required", skills=["python", "go"], type="named_tool")
