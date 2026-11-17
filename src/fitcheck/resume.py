"""Load a resume YAML into the evidence the matcher may cite. Rules: docs/decision_rules.md §3 (R7).

Only evidence leaves this module: bullets, courses, honors, and the skills list. Name, GPA, dates,
locations, and contact info are never included, and contact.private.yaml is never opened.
As a backstop, loading fails if any text that would be sent looks like an email or phone number.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}\b")


class ResumeError(ValueError):
    """The resume file can't be used as-is. The message says what to fix."""


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str  # bullet, course, honor, skill
    text: str
    shown_in: tuple[str, ...] = ()  # skill items: bullets the owner says prove it (`evidenced_by`)


@dataclass(frozen=True)
class Resume:
    version: str
    items: tuple[EvidenceItem, ...]
    prompt_text: str  # what the model sees, inside <resume> tags

    @cached_property
    def by_id(self) -> dict[str, EvidenceItem]:
        return {item.id: item for item in self.items}

    @cached_property
    def sha256(self) -> str:
        """Fingerprint of what the model sees. Any edit to a bullet changes it (L7: stale checks)."""
        return hashlib.sha256(self.prompt_text.encode()).hexdigest()


def skill_id(name: str) -> str:
    # R7: lowercase, and every run of non-letters/digits becomes one hyphen.
    return "skill-" + re.sub(r"[^a-z0-9]+", "-", name.lower())


def load_resume(path: Path) -> Resume:
    return parse_resume(Path(path).read_text(encoding="utf-8"), source=str(path))


def parse_resume(text: str, source: str = "the resume") -> Resume:
    """The same checks as load_resume, for YAML that didn't come from a file (an API upload)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ResumeError(f"{source} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ResumeError(f"{source} is not a resume: expected a YAML mapping at the top")
    items: list[EvidenceItem] = []
    skills = _load_skills(data)
    # R7: a skill linked to bullets (`evidenced_by`) is shown on those bullets, not as its own
    # citable line, so the matcher can't cite the short skills-list entry instead of the bullet.
    shown_here: dict[str, list[str]] = {}
    for skill in skills:
        for bullet_id in skill.shown_in:
            shown_here.setdefault(bullet_id, []).append(skill.text)

    lines: list[str] = []

    def add(item: EvidenceItem) -> None:
        items.append(item)
        lines.append(f"[{item.id}] {item.text}")
        if item.id in shown_here:  # not part of the item's text, so it can't be quoted (R5)
            lines.append(f"    skills shown here: {', '.join(shown_here[item.id])}")

    lines.append("## Education")
    for edu in data.get("education", []):
        lines.append(", ".join(edu.get("degrees", [])) + f" at {edu['school']}")
        for honor in edu.get("honors", []):
            add(EvidenceItem(honor["id"], "honor", honor["text"]))
        for course in edu.get("coursework", []):
            add(EvidenceItem(course["id"], "course", f"Course: {course['name']}"))

    for heading, key, label in (("Experience", "experience", ("org", "title")), ("Projects", "projects", ("name",))):
        lines.append(f"\n## {heading}")
        for entry in data.get(key, []):
            lines.append(" - ".join(entry[k] for k in label if entry.get(k)))  # context, not citable
            for bullet in entry.get("bullets", []):
                add(EvidenceItem(bullet["id"], "bullet", bullet["text"]))

    lines.append("\n## Skills section (listed only)")
    for skill in skills:
        if skill.shown_in:
            items.append(skill)  # still a real ID: if cited anyway, verify and R7 handle it
        else:
            add(skill)

    _check_unique_ids(items)
    _check_shown_in_exists(items)
    prompt_text = "\n".join(lines)
    check_no_contact_info(prompt_text, source)
    return Resume(version=str(data.get("version", "unversioned")), items=tuple(items), prompt_text=prompt_text)


def _load_skills(data: dict) -> list[EvidenceItem]:
    skills: list[EvidenceItem] = []
    seen: dict[str, str] = {}
    for group, entries in (data.get("skills") or {}).items():
        if not isinstance(entries, list):
            continue  # e.g. the section's `tag: keep`
        for skill in entries:
            sid = skill_id(skill["name"])
            if sid in seen:
                raise ResumeError(
                    f"skills {seen[sid]!r} and {skill['name']!r} both become {sid!r}. "
                    "Rename one (decision_rules.md R7)."
                )
            seen[sid] = skill["name"]
            skills.append(EvidenceItem(sid, "skill", skill["name"], tuple(skill.get("evidenced_by") or ())))
    return skills


def _check_unique_ids(items: list[EvidenceItem]) -> None:
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise ResumeError(f"resume ID {item.id!r} is used twice")
        seen.add(item.id)


def _check_shown_in_exists(items: list[EvidenceItem]) -> None:
    bullets = {item.id for item in items if item.kind == "bullet"}
    for item in items:
        unknown = [ref for ref in item.shown_in if ref not in bullets]
        if unknown:
            raise ResumeError(f"skill {item.text!r} lists evidenced_by {unknown}, which aren't bullet IDs")


def check_no_contact_info(text: str, source: str) -> None:
    for label, pattern in (("an email address", EMAIL), ("a phone number", PHONE)):
        found = pattern.search(text)
        if found:
            raise ResumeError(
                f"{source} contains what looks like {label} ({found.group()!r}). "
                "Contact info belongs in contact.private.yaml and is never sent to a model."
            )
