from pathlib import Path

import pytest

from fitcheck.resume import ResumeError, load_resume, skill_id

FROZEN = Path("evals/fixtures/eval_resume_v1.yaml")  # read only; never written by tests


def write(tmp_path, text):
    path = tmp_path / "resume.yaml"
    path.write_text(text)
    return path


@pytest.mark.parametrize("name, expected", [
    ("SQL", "skill-sql"), ("Microsoft Excel", "skill-microsoft-excel"), ("C++", "skill-c-"),
])
def test_skill_ids_follow_r7(name, expected):
    assert skill_id(name) == expected


def test_frozen_resume_loads_every_kind_of_evidence():
    resume = load_resume(FROZEN)
    assert resume.version == "eval_resume_v1"
    assert {"proj-finance-b1", "course-cs2", "honor-scholarship", "skill-sql"} <= set(resume.by_id)
    assert resume.by_id["skill-microsoft-excel"].kind == "skill"


def test_prompt_text_leaves_out_personal_details():
    text = load_resume(FROZEN).prompt_text
    for private in ["Riley", "Example", "3.8", "2028-05", "Springfield", "contact.private"]:
        assert private not in text


def test_skill_id_collision_stops_the_loader(tmp_path):
    path = write(tmp_path, "skills:\n  langs:\n    - {name: C++}\n    - {name: C#}\n")
    with pytest.raises(ResumeError, match="skill-c-"):
        load_resume(path)


def test_duplicate_ids_rejected(tmp_path):
    path = write(tmp_path, """
projects:
  - name: A
    bullets:
      - {id: b1, text: "Built a thing"}
      - {id: b1, text: "Built another thing"}
""")
    with pytest.raises(ResumeError, match="used twice"):
        load_resume(path)


@pytest.mark.parametrize("leak", ["Email me at jane.doe@example.com", "Call (312) 555-0199"])
def test_contact_info_in_evidence_stops_the_loader(tmp_path, leak):
    path = write(tmp_path, f"projects:\n  - name: A\n    bullets:\n      - {{id: b1, text: \"{leak}\"}}\n")
    with pytest.raises(ResumeError, match="contact.private.yaml"):
        load_resume(path)


def test_numbers_that_arent_phones_are_fine():
    # "40+ patron questions", "ages 11-14", "1-on-1" are all in the frozen resume.
    load_resume(FROZEN)


def test_evidenced_by_links_are_kept_and_shown():
    resume = load_resume(FROZEN)
    assert resume.by_id["skill-python"].shown_in == ("proj-finance-b1", "proj-finance-b3")
    assert resume.by_id["skill-sql"].shown_in == ()
    assert "[skill-sql] SQL\n" in resume.prompt_text  # no link: its own citable line


def test_linked_skills_are_shown_on_their_bullets_not_as_their_own_lines():
    # The 003 eval: the matcher cited [skill-java] instead of the tutoring bullet, so R7 made it weak.
    resume = load_resume(FROZEN)
    text = resume.prompt_text
    assert "[skill-java]" not in text and "[skill-python]" not in text
    assert ("[proj-finance-b1] Built a Streamlit budget app" in text
            and "    skills shown here: Streamlit, Python, pandas, Data cleaning" in text)
    assert "[exp-tutoring-b1] Taught Java" in text and "    skills shown here: Java\n" in text
    assert resume.by_id["skill-java"].kind == "skill"  # still a real ID if cited anyway (R7 -> weak)
    assert "skills shown here" not in resume.by_id["proj-finance-b1"].text  # not quotable (R5)


def test_evidenced_by_must_point_at_a_real_bullet(tmp_path):
    path = write(tmp_path, "skills:\n  langs:\n    - {name: Python, evidenced_by: [proj-typo-b1]}\n")
    with pytest.raises(ResumeError, match="proj-typo-b1"):
        load_resume(path)
