"""The answer-key format: typos and impossible combinations fail loudly."""

import pytest
import yaml
from pydantic import ValidationError

from fitcheck.labels import Label, LabeledRequirement, load_label, sha256_of

TEMPLATE = "evals/label_template.yaml"


def template(**changes):
    data = yaml.safe_load(open(TEMPLATE)) | {"posting_sha256": "0" * 64}
    return data | changes


def test_template_is_a_valid_label_once_the_fingerprint_is_filled_in():
    label = Label.model_validate(template())
    assert label.resume == "eval_resume_v1" and len(label.requirements) == 3


def test_template_fingerprint_placeholder_is_rejected():
    with pytest.raises(ValidationError):
        Label.model_validate(yaml.safe_load(open(TEMPLATE)))


@pytest.mark.parametrize("change", [
    {"source_url": "linkedin.com/jobs/1"},        # not a URL
    {"saved_on": "last tuesday"},                 # not a date
    {"decision": "maybe"},                        # not a decision
    {"statuss": "have"},                          # typo'd key
])
def test_bad_fields_fail(change):
    with pytest.raises(ValidationError):
        Label.model_validate(template(**change))


@pytest.mark.parametrize("item", [
    {"quote": "Python", "kind": "skill", "section": "required"},                          # no status
    {"quote": "Python", "kind": "skill", "section": "required", "status": "not_scored"},  # not labelable
    {"quote": "Python", "kind": "skill", "status": "have"},                               # no section
    {"quote": "Teamwork", "kind": "soft", "section": "required", "status": "have"},      # soft is never scored
    {"quote": "US work authorization", "kind": "eligibility", "section": "required"},   # no section
])
def test_impossible_requirement_entries_fail(item):
    with pytest.raises(ValidationError):
        LabeledRequirement.model_validate(item)


def test_load_label_and_fingerprint(tmp_path):
    posting = tmp_path / "001.txt"
    posting.write_text("Required\nPython\n")
    path = tmp_path / "001.yaml"
    path.write_text(yaml.safe_dump(template(posting_sha256=sha256_of(posting))))
    assert load_label(path).posting_sha256 == sha256_of(posting)
    posting.write_text("Required\nPython and Rust\n")  # the text changed after labeling
    assert load_label(path).posting_sha256 != sha256_of(posting)
