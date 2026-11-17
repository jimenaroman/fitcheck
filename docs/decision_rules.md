# Decision rules (source of truth)

The owner (Jimena) approved these rules. Code must follow them exactly.
Visual versions: `docs/fit_checker_decision_rules.html` and `docs/fit_checker_model_steps.html`.
If this file and the HTML disagree, this file wins. Never change a rule without asking the owner.

Statuses: `have`, `weak`, `gap`, `not_scored`.
Posting decisions: `apply`, `dont_apply`, `review_manually`.
Eligibility: `eligible`, `unclear`, `next_year`, `filtered_out`.

---

## 1. Extraction (model)

**Read:** required, minimum, or basic qualifications; preferred or "nice to have" items; eligibility lines anywhere.
**Skip:** company description, the responsibilities section, benefits, salary, EEO statements, application instructions, time commitment (hours per week, internship dates), and pay or stipend rules. These are never eligibility items.
**Responsibilities section (v1):** skipped, except eligibility statements inside it. "Eligibility lines anywhere" overrides every skip rule.
**Negation:** a statement that something is *not* required ("Python experience is not required") is not extracted.
Words that change meaning ("a plus", "at least", "preferred") stay inside `text` and set `section`.
**Priority (section):** explicit wording in the item first ("Python preferred"). Preference words: preferred, preferably, ideally, a plus, nice to have, bonus. Otherwise the item inherits its section heading ("Preferred qualifications"). If neither sets it, required, as an assumption.
**Mixed priority in one line:** split it. The required part and the preferred part become separate items, each quoting its own words: "deploying to servers (ideally Linux)" → required "deploying to servers" + preferred "ideally Linux".
**Mixed "and" and "or" in one line:** split it into one item per group, each quoting its own words, so every item has a single operator (decided 2026-11-10): "at least one language (Python, JavaScript/TypeScript, Go or Scala) and Git" → "at least one language (Python, JavaScript/TypeScript, Go or Scala)" (operator or) + "Git". With one operator for all six skills, "and" demanded every language, so a resume with Python, TypeScript, and Git got a gap.
**Same fact stated twice:** one item, quoting the more specific statement ("Remote" in a header vs. "remote with students completing the work from campus in Chicago" → the second).
**Not a job posting:** judged by content, not source. A LinkedIn post or an email can contain a complete posting.

Fields for the whole posting:

| Field | Values | Notes |
|---|---|---|
| is_job_posting | true, false | P2 |
| has_section_split | true, false | Whether the posting separates required from preferred. When false, items with no priority wording are labeled required as an assumption; items worded as preferred ("Python preferred", "a plus") stay preferred. P4 |

Fields for each requirement:

| Field | Values | Notes |
|---|---|---|
| text | verbatim line | Code checks it appears word for word in the posting |
| section | required, preferred | |
| kind | skill, soft, eligibility | |
| skills | list of lowercase canonical names | Code merges aliases (`js` → `javascript`) |
| operator | and, or | For requirements naming several skills |
| type | category, named_tool, knowledge, hands_on | Sets matching strictness |
| category | e.g. "data visualization" | Lets the matcher spot same-category tools |
| examples | list of lowercase tool names | Skill items only (empty for soft and eligibility). Tools the posting gives as illustrations ("such as", "or another"), not requirements. Ignored when picking the type. Never tallied (L10) |
| min_years | int or null | R9 |
| level | expert, deep, advanced, or null | R10, recorded only |
| locations | list of strings | Eligibility items only |
| grad_earliest, grad_latest | "YYYY-MM" or null | Graduation items only. An explicitly stated graduation year with no month ("Graduating in 2029") → 2029-01 to 2029-12, the whole stated range. "Class of 2029" is class_year (E2). Seasons are never converted to months: "Graduating Summer 2029" keeps its quote with null bounds, and E1 makes it unclear |
| eligibility_type | graduation, class_year, degree, gpa, work_authorization, location, other | Eligibility items only. Picks the rule in section 4 |

The model does not output requirement IDs. Code assigns them (`r1`, `r2`, …) in order after extraction.

Splitting rules:
- "BS in CS with coursework in algorithms" → an eligibility item (degree) and a knowledge item (algorithms).
- "Python, SQL, and Git" → one requirement, skills [python, sql, git], operator and.
- "(Python or Go) and Git" → two requirements: "Python or Go" (operator or) and "Git". One operator per item.
- "Python or Java" → one requirement, operator or. "Tableau or D3 required" → named_tool, skills [tableau, d3], operator or.
- Tools given as examples of a field are examples, not skills:

  | Posting says | type | skills | examples |
  |---|---|---|---|
  | "Visualization tools such as Tableau or D3" | category | [data visualization] | [tableau, d3] |
  | "AWS or another cloud platform" | category | [cloud platforms] | [aws] |
  | "Tableau or D3 required" | named_tool | [tableau, d3], or | [] |

  The item's section still applies: a preferred cloud-platform item stays preferred.
- "Strong communication and experience with React" → a soft item and a named_tool item.

Requirement types (v1 convention: the first that applies wins, in this order). Tools that appear only as examples
don't count: "Knowledge of visualization tools such as Tableau or D3" is knowledge, skills [data visualization], examples [tableau, d3].
1. **knowledge:** explicit "knowledge of" or "understanding of." "Knowledge of statistics."
   Other wording ("background in," "familiarity with") is not an automatic trigger.
2. **named_tool:** names specific tools or languages. "React," "Java," "experience with Python."
3. **hands_on:** names an activity without a specific tool. "Experience building data pipelines."
4. **category:** names a broad field. "Web development," "data analysis," "machine learning."

The schema may later separate *what skill* from *what kind of experience*; this order is the v1 rule.

---

## 2. Matching (model labels facts, code decides status)

The model never outputs a status. For each skill it returns: `requirement_id`, `skill`, `evidence_id` (or null), `quote` (exact text), `relation`, and a short `reason`.
When unsure between two relations, it picks the weaker one. False matches are worse than false gaps.
The model only judges what's written on the resume, never what the owner knows.

Relations: `exact`, `same_category`, `smaller_scale`, `taught`, `coursework`, `different_technique`, `none`.
- exact: the skill itself, or a clear instance of the requested category (Streamlit app → "web development").
- same_category: a different tool that does the same job (Plotly → "Tableau or D3").
- smaller_scale: related but smaller work (CSV cleaning → "data pipelines").
- taught: the owner taught it (tutoring → Java).
- coursework: a course covers it.
- different_technique: sounds related but is a different method (rule-based detector → "machine learning").
- none: nothing supports it.

### Status table (plain Python, owner-controlled)

| relation | category | named_tool | knowledge | hands_on |
|---|---|---|---|---|
| exact | have (M1) | have | have | have |
| same_category | have (M1) | weak (M3) | have* | weak* |
| smaller_scale | weak* | weak* | weak* | weak (M6) |
| taught | have* | have (M2) | have* | have* |
| coursework | weak* | weak* | have (M4) | weak (R6) |
| different_technique | gap (M5) | gap | gap | gap |
| none | gap | gap | gap | gap |

`*` = proposed and not yet confirmed by the owner. Treat as current values and flag them in eval reports.

Calibration rules behind the table:
- M1: a broad category is satisfied by any concrete example of real work in it.
- M2: teaching a skill counts as having it.
- M3: when the posting names tools, a different tool in the same category is only weak.
- M4: a matching course satisfies a knowledge requirement.
- M5: a technique never substitutes for a different one.
- M6: similar work at a smaller scale is weak.

---

## 3. Status ladder for one requirement (first match wins)

- **R1** Soft skill → `not_scored`. Shown in details. Never scored or tallied.
- **R2** Eligibility item → section 4.
- **R3** Several skills in one requirement: each skill gets its own status first.
  - Score: a skill is covered only if its status is `have` (P6). `or` is covered if at least one skill is covered. `and` is covered only if all are.
  - Requirement status (feeds the incl. weak %): `or` takes its best skill's status, `and` its worst (have > weak > gap). `[weak, gap]` with `or` → weak; `[have, gap]` with `and` → gap.
  - Leaderboard: every skill is tallied on its own (see L8).
- **R4** Cited bullet, quote verified verbatim → status from the table. Verbatim uses the same rule as extraction: line breaks, extra spaces, and capitalization are ignored, and so are typographic variants (curly quotes, en/em dashes, the … character, non-breaking spaces count as their plain forms); everything else must match. This is for comparison only: quotes are stored as written.
- **R5** Quote not in the cited item, or the ID doesn't exist → `gap`. Log it as a caught false match.
- **R5b** `exact` on a named_tool requirement: the cited item must name the skill (or one of its aliases), or be linked to it through `evidenced_by`. Otherwise → `gap`, logged as a caught false match. (A real quote isn't enough: it must be about the tool.) Category, knowledge, and hands_on matches are not checked this way, because they need judgment (M1).
- **R6** Only evidence is a course → `have` for knowledge requirements, `weak` for hands-on requirements.
- **R7** Only in the skills list (`evidenced_by` empty) → `weak`, reason `listed_not_shown`. A skill with `evidenced_by` links isn't shown to the matcher as its own line: its name appears under each linked bullet ("skills shown here: …"), so the matcher cites the bullet. That line isn't part of the bullet's text, so it can't be quoted (R5). If the matcher cites a linked skill's ID anyway, it's still a skills-list citation → `weak`; code doesn't guess.
  Skills have no IDs in the resume file. The resume loader derives them from the name: lowercase, and every run of non-letters/digits becomes one hyphen (`SQL` → `skill-sql`, `Microsoft Excel` → `skill-microsoft-excel`). The resume file itself is never edited.
  If two skills produce the same ID (`C++` and `C#` both become `skill-c-`), the loader stops with an error naming both. No aliases yet; add one when the error fires.
- **R8** Nothing → `gap`, reason `not_on_resume`.
- **R8b** Relation `different_technique` → `gap`, reason `not_on_resume` (the technique asked for isn't on the resume).
- **R9** Requirement has `min_years`: `have` stays `have` only if the cited quote itself states a duration that meets it. Otherwise it drops to `weak`, reason `years_not_shown`. Never calculate years from resume dates.
- **R10** Level words: "familiarity," "proficiency," and "strong" are ignored. "Expert," "deep," and "advanced" are recorded, never shown for now, and never change a status.

Every match stores a `reason` code set by code, not by the model: `listed_not_shown`, `similar_tool`, `smaller_scale`, `coursework_only`, `years_not_shown`, `not_on_resume`, `false_match_caught`.

---

## 4. Eligibility (model reads facts, Python compares against resume and profile.yaml)

- **E1 Graduation window:** expected date (2029-05) inside → eligible. Only the early date (2028-12) inside → unclear. Window ends before the expected date → `next_year`. Window starts after it → `filtered_out`. No graduation item → eligible (nothing to check). A graduation item whose dates couldn't be extracted (both null) → unclear, with the quote shown: a restriction exists but isn't resolved.
- **E2 Class-year wording:** translate using the internship season. For Summer 2027, "rising junior" and "current sophomore" mean the owner.
- **E3 Degree:** "Pursuing a BS in CS or related field" or "or equivalent experience" → eligible. MS or PhD only → `filtered_out`.
- **E4 GPA minimums:** ignored entirely. Never filter, flag, or block. Shown in details only.
- **E5 Work authorization:** from `profile.yaml`. "No sponsorship" passes.
- **E6 Location:** off by default (`location_filter.enabled: false`), which means it's ignored completely.
  When on: any posting location in a listed metro (sf, chicago, nyc, dallas_plano), or remote when `remote_ok: true` → eligible.
  Otherwise → `filtered_out`, reason = the city. A location that isn't recognized → unclear. No location → eligible.
  Metros are an alias list in code (Plano, Richardson, and Frisco → dallas_plano).
  Extraction always stores locations. The filter applies at display time.
- **E7 Combine:** any `filtered_out` → filtered_out. Else any `next_year` → next_year. Else any `unclear` → unclear. Else eligible.
- **E8 Other:** `eligibility_type` other (e.g. "must be able to lift 25 lbs") → shown in details only. No effect on eligibility.

---

## 5. Posting decision (score.py, OWNER WRITES)

Uses only scorable required items (not soft, not eligibility).

- **P1** Validation failed twice → `review_manually`, reason `invalid_output`.
- **P2** Not a job posting (`is_job_posting` false) → `review_manually`, reason `not_a_posting`.
- **P3** More than half of citations caught as false (R5) → `review_manually`, reason `unreliable_matching`.
- **P4** No required/preferred split (`has_section_split` false) → items with no priority wording count as required (an assumption, not a fact), items worded as preferred stay preferred, flag `no_split`. (v1 decision; revisit after evals)
- **P5** Zero scorable required items → `apply`, flag `no_skill_requirements`.
- **P6** A required item is covered only if its status is `have`. `weak` does not count. Let n = scorable required items, covered = items that are `have`, missed = n − covered.
  `apply` if covered ≥ 1 **and** (missed ≤ 2 **or** covered ÷ n ≥ 80%). Otherwise `dont_apply`.
  The ≤ 2 part keeps short postings fair (2/3, 3/5 apply). The 80% part keeps long ones fair (12/15 applies).
  The threshold lives in one constant in score.py and is pinned by the section 8 tests.
  Two percentages are stored and shown: **proven** = have ÷ n (the one the decision uses) and **incl. weak** = (have + weak) ÷ n (information only).
- **P7** Retired 2026-09-30. Weak items no longer count toward coverage, so `relies_on_unproven` is no longer needed.
- **P8** Preferred items never change the decision.

---

## 6. What the card shows

| Decision | Eligibility | Card |
|---|---|---|
| apply | eligible | "Apply" plus card-level flags |
| apply | unclear | "Apply, check eligibility first" plus the reason |
| any | filtered_out | Hidden from the main list. Shown in the Filtered out list with the reason |
| any | next_year | Saved to Next year with the grad window and the skills result |
| dont_apply | eligible or unclear | "Not yet: N required not proven" |
| review_manually | any | "Review manually" plus the reason |

Every card with a decision of apply or dont_apply shows both percentages, e.g. "60% proven, 80% incl. weak (3 of 5 required)".
Card-level flags (all shown): eligibility unclear, review manually reason.
`no_split` is shown beside the coverage score as "Required/preferred split unclear", with the full explanation in details.
Details only: `no_skill_requirements`. Saved only: level words (R10), caught false matches (R5).
Never show a bare status. Every weak or gap shows its label, explanation, and fix:

| reason | Label | Fix |
|---|---|---|
| listed_not_shown | Listed, not shown | Add a bullet where you used it |
| similar_tool | Similar tool | Mention it, or try their tool |
| smaller_scale | Smaller scale | Describe or build something bigger |
| coursework_only | Coursework only | Add a project that uses it |
| years_not_shown | Years not shown | State the duration in a bullet |
| not_on_resume | Not on your resume | Learn it, or tap "I've done this" |

---

## 7. Skills leaderboard

Four boards: Strengths in demand (have), Unproven on your resume (weak, any reason; each entry shows its reasons and their fixes from section 6; owner, 2026-11-08, was listed_not_shown only), Not on your resume yet (gaps marked "I've done this"), Most-requested gaps (gap).

- **L1** A skill counts once per posting. Asked for in both sections, it counts as required. Asked for twice in one section, its best status counts (owner, 2026-11-08).
- **L2** Duplicate postings (same text) count once.
- **L3** review_manually postings don't count.
- **L4** Soft skills and eligibility items don't count.
- **L5** next_year postings count. filtered_out postings don't. (Confirmed by owner, 2026-11-06.)
- **L6** Required and preferred are tallied separately. A preferred gap is highlighted when more than half of postings ask for it.
- **L7** "Asked for" counts are fixed. Statuses come from each saved check (owner, 2026-11-08: no automatic re-matching). Checks made against an older resume are marked stale, and a recheck I start myself re-matches them after showing the cost.
- **L8** In combined requirements, every skill is tallied on its own. A gap from an `or` already covered by another skill is marked "as an alternative."
- **L9** "I've done this" moves a gap to Not on your resume yet. It stays a gap in the score until a bullet proves it.
- **L10** Example tools (`examples`) are never tallied as gaps or strengths: if Plotly satisfies "tools such as Tableau or D3," calling Tableau and D3 gaps would be misleading. A separate "Tools mentioned as examples" count is deferred past v1.

---

## 8. Edge cases (each becomes a unit test)

| Posting says | Expected | Rule |
|---|---|---|
| "Proficiency in Python" | have via proj-finance-b1 | R4, R10 |
| "Experience with SQL" | weak, listed_not_shown | R7 |
| "Python or Java" | covered. Tally: python and java → strengths | R3 |
| "Java or C++" | covered. Tally: java → strengths, c++ → gaps as alternative | R3, L8 |
| "Python and Kubernetes" | not covered. Tally: python → strengths, kubernetes → gaps | R3 |
| "AWS or another cloud platform" | category [cloud platforms], examples [aws]. gap. Tally: cloud platforms → gaps; aws not tallied | R8, L10 |
| "2+ years of Java" | weak, years_not_shown, via exp-tutoring-b1 | R9 |
| "2+ years of Java" with a bullet stating "2+ years building Java applications" | have | R9 |
| "Solid understanding of data structures" | have via course-cs2 | R6, M4 |
| "Experience with web development" | have via proj-finance-b1 | M1 |
| "Visualization tools such as Tableau or D3" | category [data visualization], examples [tableau, d3]. have via Plotly (proj-finance-b2). Tally: data visualization → strengths; tableau and d3 not tallied | M1, L10 |
| "Tableau or D3 required" | named_tool [tableau, d3], or. weak, similar_tool, via Plotly. Tally: tableau and d3 → gaps as alternatives | M3, L8 |
| "Experience with Python" | named_tool (not hands_on) | type order |
| "Python experience is not required" | not extracted | negation |
| No split, "Python preferred" | section preferred, no_split | P4 |
| "Graduating in 2029" | grad 2029-01 to 2029-12, eligible | E1 |
| "Class of 2029" | class_year, no grad dates | E2 |
| "Graduating Summer 2029" | graduation, null dates, quote kept. unclear | E1 |
| Heading "Preferred qualifications", bullet "Python" | section preferred | priority |
| "deploying to servers (ideally Linux)" | required "deploying to servers" + preferred "ideally Linux" | priority, mixed line |
| "at least one language (Python, JavaScript/TypeScript, Go or Scala) and Git" | two items: the languages (or) + "Git". Covered with Python and Git | mixed and/or, R3 |
| Header "Remote" and "Location: remote … from campus in Chicago" | one location item quoting the second | same fact twice |
| "Part-time, 15-20 hours/week", "$5,000 stipend" | not extracted | skip |
| "Knowledge of visualization tools such as Tableau or D3" | knowledge [data visualization], examples [tableau, d3] | type order |
| "Experience with machine learning" | gap (rule-based detector is not ML) | M5 |
| "Experience building data pipelines" | weak, smaller_scale | M6 |
| Model cites a React quote that isn't in proj-finance-b2 | gap, false_match_caught | R5 |
| Model cites an ID that doesn't exist | gap, false_match_caught | R5 |
| "Experience with Python", model says exact via exp-cafe-b1 (real quote, no Python) | gap, false_match_caught | R5b |
| "Experience with Python", exact via proj-finance-b1 (no "Python" in it, but linked through evidenced_by) | have | R5b, R4 |
| "Experience with Python", model cites skill-python (which has evidenced_by links) | weak, listed_not_shown | R7 |
| "Excellent communication skills" | not_scored | R1 |
| "Graduating Dec 2028 – Aug 2029" | eligible | E1 |
| "Graduating Sep 2028 – Mar 2029" | unclear (early date only) | E1 |
| "Graduating Dec 2026 – Jun 2027" | next_year | E1 |
| "Open to MS and PhD students only" | filtered_out | E3 |
| "Minimum GPA 3.7" | ignored. Eligibility unaffected | E4 |
| "Must be able to lift 25 lbs" | eligibility_type other, shown in details. Eligibility unaffected | E8 |
| "Located in Boston," filter off | eligible | E6 |
| "Located in Boston," filter on | filtered_out, reason Boston | E6 |
| "New York, Seattle, or Remote," filter on | eligible | E6 |
| "Plano, TX," filter on | eligible (dallas_plano) | E6 |
| 3 required items ask for years or expert level, no degree requirement | years items weak unless a duration is stated. Not filtered | R9, R10 |
| 1 required, 0 have | dont_apply (nothing met) | P6 |
| 2 required, 1 have | apply (1 missed) | P6 |
| 3 required, 2 have | apply (1 missed) | P6 |
| 3 required, 1 have | apply (2 missed) | P6 |
| 5 required, 3 have | apply (2 missed) | P6 |
| 5 required, 2 have | dont_apply (3 missed, 40%) | P6 |
| 5 required: 3 have, 1 weak, 1 gap | apply (2 missed). 60% proven, 80% incl. weak | P6 |
| 5 required: 2 have, 3 weak | dont_apply (weak doesn't count). 40% proven, 100% incl. weak | P6 |
| 15 required, 12 have | apply (80%) | P6 |
| 15 required, 11 have | dont_apply (4 missed, 73%) | P6 |
| No required/preferred split | all treated as required, no_split | P4 |
| Only soft skills and a grad window | apply if eligible, no_skill_requirements | P5 |
| Pasted LinkedIn post | review_manually, not_a_posting | P2 |
| SQL cited as `skill-sql` (skills list only) | weak, listed_not_shown | R7 |
| Same posting twice | second copy skipped by the leaderboard | L2 |

---

## 9. Matches (M10): which postings get checked, and how many

Decided 2026-11-10. These rules only choose **which** postings go through the normal pipeline. They never
change a status, a decision, or a percentage: every match is scored by sections 1-5 like a pasted posting.

- **M1 Ranking (ordering only).** Unchecked postings that pass the filters (Summer 2027, my categories,
  my profile) are ordered by: (1) proven resume skills named in the description, word for word with the
  alias table; (2) then listed-only skills named; (3) then newest. A posting whose description can't be
  fetched is ranked by its title instead. Known weakness: it ignores skills I lack and favors long postings
  that name many tools; the real check gives the verdict.
- **M2 My own postings: at most 5 a day.** Pasted checks are capped at 5 per day (America/Chicago). Only
  checks that cost money count: cache hits and rechecks after a resume change don't.
- **M3 Ten matches a day, found automatically.** Every morning (about 8 AM Chicago) the 10 best-ranked
  unchecked postings are checked. New postings rank first; if there are fewer than 10 good new ones (most
  weekends), the backlog of the last ~2 weeks fills in. A posting is never checked twice. A
  `review_manually` result doesn't count and the next one takes its place, up to 15 tries a day. If fewer
  than 10 postings fit, it checks what fits and says so; it never pads with bad fits. Matches run before
  my rechecks get budget (the $0.50/day guard still stops everything).
- **M4 Duplicates.** The same job on both lists is merged (company + normalized title) before ranking.
  Before a pasted check is paid for, the app compares it with my saved checks (company + title); if it
  looks the same it offers "Open #12 instead?" ($0) with "Check anyway" as the other choice.

---

## 10. Open decisions (ask the owner; never decide for her)

- P4: decided for v1 (see P4). Revisit after evals: is "required" the right assumption?
- E6: does SF mean the city or the whole Bay Area? Does remote count (currently `remote_ok: true`)?
- Responsibilities section: decided for v1 (skip, except eligibility). Revisit: leaderboard only, or score like preferred?
- R10: how to display level words, after 15 labeled postings.
- Status table cells marked `*`.
- P6 and split granularity (found in M7, posting 005): "miss at most 2" counts items, so the same line
  split into one item or two can flip the decision, especially on postings with only 1 to 3 required
  skills. Keep as is, decide by percentage only, or count per requirement line?
