# Labeling rubric (DRAFT: owner edits)

How to write `evals/labels/NNN.yaml`. The label is the answer key: what a careful human reading the
posting and the frozen resume (`evals/fixtures/eval_resume_v1.yaml`) would say. Rules come from
`docs/decision_rules.md`; this file only explains how to apply them when labeling.

## Ground rules
- Label from the posting text and the frozen resume only. **Never look at the pipeline's output first**:
  copying its answers would hide its mistakes.
- Use `eval_resume_v1` even after your live resume changes, so scores stay comparable over time.
- Once written, a label is frozen. If you change your mind, write a new posting/label pair.

## Saving a posting
1. Save the text to `evals/postings/NNN.txt` (gitignored; numbers in order: 001, 002, ...).
2. Copy `evals/label_template.yaml` to `evals/labels/NNN.yaml`.
3. Fill in `source_url`, `saved_on`, and the fingerprint from `shasum -a 256 evals/postings/NNN.txt`
   (the first 64 characters of the output). The harness refuses a posting whose text changed.

## What to list (decision_rules.md section 1)
- Every required, preferred, and "nice to have" item, and eligibility statements wherever they appear.
- Skip: company description, responsibilities, benefits, salary, EEO text, application instructions,
  hours per week, internship dates, pay and stipend rules.
- One entry per distinct requirement. Split a line that mixes kinds or mixes required and preferred
  parts ("deploying to servers (ideally Linux)" → two entries).
- "Python, SQL, and Git" or "Python or Java" stays one entry.

## quote
- A short phrase copied exactly from the posting, about 3 to 8 words, that identifies this item.
- It must be unique in the posting, so it can't be confused with another item.
- The harness pairs your entry with the pipeline's item when either text contains the other
  (ignoring line breaks, spaces, and capitalization).

## kind and section
- kind: `skill`, `soft` (communication, teamwork, ...), or `eligibility`.
- Lines about being excited, curious, or interested in X are `soft`, even when X is a skill
  ("Excited to build data visualizations", "Curiosity about AI", "Interest in AI evaluation").
  The line is a skill only if it also asks for experience, knowledge, or ability with X
  ("Curiosity about and experience using AI-powered tools" is a skill).
- section (skill and soft only): wording first ("preferred", "ideally", "a plus", ...), then the heading,
  otherwise required.

## status (skill items only, for the whole requirement)
Judge each skill the way the rules do, then combine:
- `have`: a bullet or course clearly shows it (status table, section 2). A course is enough for
  "knowledge of"; for hands-on work it's weak.
- `weak`: only in the skills list, a similar tool, smaller-scale work, coursework for hands-on work,
  or years asked for but not stated in the bullet.
- `gap`: nothing on the resume, or a different technique.
- Combine (R3): "or" takes the best skill, "and" the worst.
- When unsure between two, pick the weaker. A false match is worse than a false gap.

## decision and eligibility
- decision: what the section 5 rules give for your statuses (P6: at least 1 have, and at most 2 missed
  or at least 80% have). Preferred items never count.
- An eligibility line marked preferred ("June 2028 (preferred)") is not a requirement: missing it
  doesn't make you ineligible.
- eligibility: **the right answer for you** (with `profile.yaml`, location filter off), even where the
  section 4 rules would get it wrong. Example: "grad date: Dec 2028 – Spring 2029" is eligible for a May 2029
  graduate, though the season rule makes it unclear. Every disagreement then points at a rule worth revisiting.

## What the harness measures from your labels
- Extraction recall: your entries the pipeline found. Extra items it found are listed, not scored.
- Section accuracy: required vs. preferred, on items found.
- False matches (pipeline status better than yours) and false gaps (worse), per requirement.
  Reported separately; false matches are worse.
- Evidence validity: citations that pass verify.py.
- Decision and eligibility agreement. Cost (original, even when cached) and latency per posting.
