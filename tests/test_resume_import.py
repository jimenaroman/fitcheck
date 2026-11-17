"""Resume import, offline: tiny files for a made-up person (never real resume data), and a FakeProvider."""

import io
from datetime import date

import pytest
import yaml
from docx import Document

from fitcheck.providers.base import InvalidOutputError
from fitcheck.providers.fake_provider import FakeProvider
from fitcheck.resume import parse_resume
from fitcheck.resume_import import (ResumeImportError, file_to_text, import_resume, strip_contact,
                                    to_resume_yaml, ImportedResume)

RESUME_LINES = [
    "Alex Rivera",
    "alex.rivera@example.com | 312-555-0142 | linkedin.com/in/alexrivera | https://github.com/arivera",
    "Education",
    "Lakeview University",
    "B.S. Computer Science",
    "Honors: Dean's List",
    "Coursework: Data Structures, Intro CS I, II & III",
    "Projects",
    "Bike Tracker",
    "- Built a Python app that maps bike-share stations with Flask and SQLite",
    "Experience",
    "Robotics Club - Treasurer",
    "- Managed a $2,000 budget in Google Sheets",
    "Skills",
    "Languages: Python, SQL",
    "Tools: Flask, Git",
]
TODAY = date(2026, 11, 8)


def make_pdf(lines: list[str]) -> bytes:
    """A one-page PDF built by hand (no PDF library needed to write one)."""
    def escape(line):
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    ops = ["BT /F1 11 Tf 72 720 Td 14 TL"] + [f"({escape(line)}) Tj T*" for line in lines] + ["ET"]
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> "
        b"/Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def make_docx(lines: list[str]) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def model_answer(**changes) -> dict:
    """What a correct model answer for RESUME_LINES looks like."""
    return {
        "is_resume": True,
        "education": [{"school": "Lakeview University", "degrees": ["B.S. Computer Science"],
                       "honors": ["Dean's List"], "coursework": ["Data Structures", "Intro CS I, II & III"]}],
        "experience": [{"name": "Robotics Club", "title": "Treasurer",
                        "bullets": ["Managed a $2,000 budget in Google Sheets"]}],
        "projects": [{"name": "Bike Tracker", "title": "",
                      "bullets": ["Built a Python app that maps bike-share stations with Flask and SQLite"]}],
        "skills": [{"group": "Languages", "skills": ["Python", "SQL"]}, {"group": "Tools", "skills": ["Flask", "Git"]}],
    } | changes


NOT_A_RESUME = {"is_resume": False, "education": [], "experience": [], "projects": [], "skills": []}


# ---------- 1. File -> text ----------

@pytest.mark.parametrize("name,data", [("r.pdf", make_pdf(RESUME_LINES)), ("r.docx", make_docx(RESUME_LINES)),
                                       ("r.txt", "\n".join(RESUME_LINES).encode())])
def test_pdf_docx_and_txt_all_give_the_text(name, data):
    text = file_to_text(name, data)
    assert "Built a Python app that maps bike-share stations with Flask and SQLite" in text
    assert "Intro CS I, II & III" in text


@pytest.mark.parametrize("name", ["resume.png", "resume.JPG", "scan.heic"])
def test_images_are_refused(name):
    with pytest.raises(ResumeImportError, match="Images aren't accepted") as caught:
        file_to_text(name, b"\x89PNG")
    assert caught.value.status == 415


def test_other_file_types_are_refused():
    with pytest.raises(ResumeImportError, match="Unsupported file type .pages") as caught:
        file_to_text("resume.pages", b"x")
    assert caught.value.status == 415


def test_a_huge_file_is_refused():
    with pytest.raises(ResumeImportError) as caught:
        file_to_text("r.txt", b"x" * 2_000_001)
    assert caught.value.status == 413


@pytest.mark.parametrize("name,data", [("r.pdf", b"not really a pdf"), ("r.docx", b"not a zip"),
                                       ("r.txt", b"\xff\xfe\xfa")])
def test_a_corrupt_file_says_so(name, data):
    with pytest.raises(ResumeImportError, match="Couldn't read"):
        file_to_text(name, data)


def test_a_pdf_with_no_text_layer_says_so():
    with pytest.raises(ResumeImportError, match="No text found"):
        file_to_text("scan.pdf", make_pdf([]))


# ---------- 2. Contact info out ----------

def test_contact_info_is_removed_before_the_model():
    clean, removed = strip_contact(file_to_text("r.txt", "\n".join(RESUME_LINES).encode()))
    assert removed == 4
    for contact in ("alex.rivera@example.com", "312-555-0142", "linkedin.com", "github.com"):
        assert contact not in clean
    assert "Alex Rivera" in clean and "Lakeview University" in clean  # the rest stays
    assert "|" not in clean  # the emptied header line is gone, not left as "| | |"


def test_the_model_never_sees_contact_info():
    provider = FakeProvider([model_answer()])
    import_resume("r.pdf", make_pdf(RESUME_LINES), provider, TODAY)
    sent = provider.calls[0].user
    assert "@" not in sent and "555-0142" not in sent and "linkedin" not in sent


def test_ordinary_words_with_dots_survive():
    clean, removed = strip_contact("Built with Node.js and Vue, e.g. dashboards")
    assert removed == 0 and "Node.js" in clean


# ---------- 3 and 4. The import ----------

def test_a_resume_becomes_a_valid_draft():
    result = import_resume("r.pdf", make_pdf(RESUME_LINES), FakeProvider([model_answer()]), TODAY)
    resume = parse_resume(result.draft)  # the same validation as PUT /resume
    assert result.items == len(resume.items) and result.contact_removed == 4
    data = yaml.safe_load(result.draft)
    assert data["projects"][0]["bullets"][0] == {
        "id": "proj-bike-tracker-b1", "text": "Built a Python app that maps bike-share stations with Flask and SQLite"}
    assert data["experience"][0] == {"id": "exp-robotics-club", "org": "Robotics Club", "title": "Treasurer",
                                     "bullets": [{"id": "exp-robotics-club-b1",
                                                  "text": "Managed a $2,000 budget in Google Sheets"}]}


def test_skills_are_linked_only_to_bullets_that_name_them():
    result = import_resume("r.txt", "\n".join(RESUME_LINES).encode(), FakeProvider([model_answer()]), TODAY)
    links = {s["name"]: s["evidenced_by"] for group in yaml.safe_load(result.draft)["skills"].values() for s in group}
    assert links == {"Python": ["proj-bike-tracker-b1"], "Flask": ["proj-bike-tracker-b1"], "SQL": [], "Git": []}
    assert result.listed_only == ["SQL", "Git"]  # "SQLite" doesn't count as SQL


def test_an_invented_bullet_is_retried_with_feedback():
    invented = model_answer(projects=[{"name": "Bike Tracker", "title": "",
                                       "bullets": ["Led a team of 10 engineers at Google"]}])
    provider = FakeProvider([invented, model_answer()])
    result = import_resume("r.pdf", make_pdf(RESUME_LINES), provider, TODAY)
    assert result.draft and len(provider.calls) == 2
    assert "Led a team of 10 engineers at Google" in provider.calls[1].user
    assert "not found" in result.rejections[0].error


def test_two_failures_mean_review_manually_without_a_third_call():
    invented = model_answer(skills=[{"group": "Languages", "skills": ["Rust"]}])
    provider = FakeProvider([invented, invented])
    with pytest.raises(ResumeImportError, match="Review manually"):
        import_resume("r.pdf", make_pdf(RESUME_LINES), provider, TODAY)
    assert len(provider.calls) == 2


def test_not_a_resume_is_refused_without_a_retry():
    # The monkey test: a file with text that isn't a resume (here, a grocery list).
    provider = FakeProvider([NOT_A_RESUME])
    with pytest.raises(ResumeImportError, match="doesn't look like a resume"):
        import_resume("list.txt", b"eggs\nmilk\nbananas for the monkey\n", provider, TODAY)
    assert len(provider.calls) == 1


def test_an_unusable_file_costs_nothing():
    provider = FakeProvider([])
    with pytest.raises(ResumeImportError):
        import_resume("photo.png", b"\x89PNG", provider, TODAY)
    assert provider.calls == []


def test_a_refused_or_cut_off_answer_counts_as_a_failure():
    provider = FakeProvider([InvalidOutputError("refused"), model_answer()])
    assert import_resume("r.txt", "\n".join(RESUME_LINES).encode(), provider, TODAY).draft


def test_duplicate_names_get_distinct_ids():
    twice = ImportedResume.model_validate(model_answer(projects=[
        {"name": "Bike Tracker", "title": "", "bullets": ["Built a Python app"]},
        {"name": "Bike Tracker", "title": "", "bullets": ["Built a Python app"]}]))
    ids = [p["id"] for p in yaml.safe_load(to_resume_yaml(twice, TODAY))["projects"]]
    assert ids == ["proj-bike-tracker", "proj-bike-tracker-2"]


def test_the_draft_says_it_needs_review():
    draft = to_resume_yaml(ImportedResume.model_validate(model_answer()), TODAY)
    assert draft.startswith("# Imported 2026-11-08 from a file. Review before saving")
