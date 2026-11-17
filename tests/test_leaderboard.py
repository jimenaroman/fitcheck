"""Leaderboard rules L1-L10 (decision_rules.md section 7), on hand-built saved checks."""

from datetime import UTC, datetime

from fitcheck.leaderboard import SavedCheck, build_leaderboard
from fitcheck.schemas import FitResult, NumberedRequirement, Requirement, RequirementResult, SkillResult

RESUME = "resume-v1"
MARKED = datetime(2026, 10, 30, tzinfo=UTC)
_ids = iter(range(1, 1000))


def req(rid, skills, section="required", operator=None, examples=()):
    return NumberedRequirement(id=rid, requirement=Requirement(
        text=f"text {rid}", section=section, kind="skill", skills=list(skills), type="named_tool",
        operator=operator, examples=list(examples)))


def skill(rid, name, status, reason=None):
    return SkillResult(requirement_id=rid, skill=name, status=status, reason_code=reason)


def check(reqs_and_results, posting="p", decision="apply", eligibility="eligible", resume=RESUME):
    """reqs_and_results: [(NumberedRequirement, requirement status, [SkillResult])]"""
    reason = "not_a_posting" if decision == "review_manually" else None
    result = FitResult(decision=decision, decision_reason=reason, eligibility=eligibility, requirements=[
        RequirementResult(requirement_id=r.id, status=status, skills=skills) for r, status, skills in reqs_and_results])
    return SavedCheck(id=next(_ids), posting_sha256=posting, resume_sha256=resume,
                      requirements=[r for r, _, _ in reqs_and_results], result=result)


def sql_gap(posting="p", section="required"):
    return check([(req("r1", ["sql"], section), "gap", [skill("r1", "sql", "gap", "not_on_resume")])], posting)


def board(lb, name):
    return {e.skill: (e.required, e.preferred) for e in getattr(lb, name)}


def test_the_four_boards():
    lb = build_leaderboard([check([
        (req("r1", ["python"]), "have", [skill("r1", "python", "have")]),
        (req("r2", ["git"]), "weak", [skill("r2", "git", "weak", "listed_not_shown")]),
        (req("r3", ["docker"]), "gap", [skill("r3", "docker", "gap", "not_on_resume")]),
        (req("r4", ["sql"]), "gap", [skill("r4", "sql", "gap", "not_on_resume")]),
    ])], done={"docker": MARKED}, current_resume_sha256=RESUME)
    assert board(lb, "strengths") == {"python": (1, 0)}
    assert board(lb, "unproven") == {"git": (1, 0)}
    assert board(lb, "marked_done") == {"docker": (1, 0)}  # L9
    assert board(lb, "gaps") == {"sql": (1, 0)}
    assert lb.marked_done[0].marked_at == MARKED and lb.gaps[0].marked_at is None


def test_decisions_are_counted_once_per_posting_including_skipped_ones():
    lb = build_leaderboard([sql_gap("a"), sql_gap("a"), sql_gap("b"), check([], posting="c", decision="review_manually"),
                            check([], posting="d", decision="dont_apply", eligibility="filtered_out")], {}, RESUME)
    assert lb.decisions == {"apply": 2, "review_manually": 1, "dont_apply": 1}


def test_every_weak_skill_is_unproven_with_its_reasons_counted():
    def react(posting, reason):
        return check([(req("r1", ["react"]), "weak", [skill("r1", "react", "weak", reason)])], posting)

    lb = build_leaderboard([react("a", "similar_tool"), react("b", "similar_tool"), react("c", "listed_not_shown")],
                           {}, RESUME)
    (entry,) = lb.unproven
    assert entry.required == 3 and entry.reasons == {"similar_tool": 2, "listed_not_shown": 1}


def test_most_requested_gaps_come_first():
    checks = [sql_gap("a"), sql_gap("b"), check([(req("r1", ["rust"]), "gap", [skill("r1", "rust", "gap")])], "c")]
    assert [e.skill for e in build_leaderboard(checks, {}, RESUME).gaps] == ["sql", "rust"]


def test_l1_a_skill_counts_once_per_posting_and_required_wins():
    lb = build_leaderboard([check([
        (req("r1", ["sql"]), "gap", [skill("r1", "sql", "gap")]),
        (req("r2", ["sql"], "preferred"), "gap", [skill("r2", "sql", "gap")]),
        (req("r3", ["sql"]), "gap", [skill("r3", "sql", "gap")]),
    ])], {}, RESUME)
    assert board(lb, "gaps") == {"sql": (1, 0)}


def test_l1_the_best_status_counts_within_a_section():
    lb = build_leaderboard([check([
        (req("r1", ["python"]), "gap", [skill("r1", "python", "gap")]),
        (req("r2", ["python"]), "have", [skill("r2", "python", "have")]),
    ])], {}, RESUME)
    assert board(lb, "strengths") == {"python": (1, 0)} and lb.gaps == []


def test_l2_duplicate_postings_count_once():
    lb = build_leaderboard([sql_gap("same"), sql_gap("same")], {}, RESUME)
    assert board(lb, "gaps") == {"sql": (1, 0)} and lb.skipped_duplicates == 1


def test_l3_review_manually_does_not_count():
    lb = build_leaderboard([check([], decision="review_manually")], {}, RESUME)
    assert lb.postings_counted == 0 and lb.skipped_review_manually == 1


def test_l5_next_year_counts_filtered_out_does_not():
    nxt = check([(req("r1", ["sql"]), "gap", [skill("r1", "sql", "gap")])], "a", eligibility="next_year")
    out = check([(req("r1", ["go"]), "gap", [skill("r1", "go", "gap")])], "b", eligibility="filtered_out")
    lb = build_leaderboard([nxt, out], {}, RESUME)
    assert board(lb, "gaps") == {"sql": (1, 0)} and lb.skipped_filtered_out == 1


def test_l4_soft_items_are_not_tallied():
    soft = NumberedRequirement(id="r1", requirement=Requirement(text="Teamwork", section="required", kind="soft"))
    lb = build_leaderboard([check([(soft, "not_scored", [])])], {}, RESUME)
    assert lb.strengths == lb.gaps == [] and lb.postings_counted == 1


def test_l6_preferred_gap_highlighted_when_more_than_half_ask():
    checks = [sql_gap("a", "preferred"), sql_gap("b", "preferred"),
              check([(req("r1", ["python"]), "have", [skill("r1", "python", "have")])], "c")]
    (entry,) = build_leaderboard(checks, {}, RESUME).gaps
    assert (entry.preferred, entry.highlighted) == (2, True)  # 2 of 3
    (entry,) = build_leaderboard(checks + [check([], posting="d")], {}, RESUME).gaps
    assert entry.highlighted is False  # 2 of 4 is not more than half


def test_l8_gap_in_a_covered_or_is_an_alternative():
    lb = build_leaderboard([check([(req("r1", ["python", "java"], operator="or"), "have",
                                    [skill("r1", "python", "have"), skill("r1", "java", "gap")])])], {}, RESUME)
    (entry,) = lb.gaps
    assert (entry.skill, entry.required, entry.as_alternative) == ("java", 1, 1)


def test_l8_every_skill_in_an_and_is_tallied_on_its_own():
    lb = build_leaderboard([check([(req("r1", ["python", "sql"], operator="and"), "gap",
                                    [skill("r1", "python", "have"), skill("r1", "sql", "gap")])])], {}, RESUME)
    assert board(lb, "strengths") == {"python": (1, 0)}
    assert [(e.skill, e.as_alternative) for e in lb.gaps] == [("sql", 0)]


def test_l10_examples_are_never_tallied():
    viz = req("r1", ["data visualization"], examples=["tableau", "d3"])
    lb = build_leaderboard([check([(viz, "have", [skill("r1", "data visualization", "have")])])], {}, RESUME)
    assert board(lb, "strengths") == {"data visualization": (1, 0)} and lb.gaps == []


def test_l7_checks_against_an_older_resume_are_stale():
    old, new = sql_gap("a"), check([], posting="b", resume="resume-v2")
    lb = build_leaderboard([new, old], {}, current_resume_sha256="resume-v2")
    assert lb.stale_check_ids == [old.id]
