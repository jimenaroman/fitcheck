# M7 findings: does a second-pass checker earn its cost?

November 6, 2026 · Model: `claude-haiku-4-5` · Resume: `eval_resume_v1` · 5 labeled postings.
Reports: `evals/reports/2026-11-06_142204.md` (before), `2026-11-06_203638.md` (after the prompt
fix), `2026-11-06_205645.md` (005 after the duplicate check).

## The question
M7 was planned as Haiku vs. Gemini Flash. It became: is a second model call that rechecks the
first one worth paying for on every posting, or do cheaper fixes do the job?

## What was measured

| | Before | After prompt fix |
|---|---|---|
| Extraction recall | 95% | 94% |
| False matches (claims a skill I don't have) | 3 | **1** |
| False gaps | 4 | 5 |
| Evidence validity | 100% | 100% |
| Decision agreement | 4/5 | 3/5 |
| Eligibility agreement | 5/5 | 5/5 |
| Cost per posting | $0.015 | $0.018 |

## What changed and why
1. **Prompt rule: excited/curious/interested lines are soft.** The rubric already said so;
   the extraction prompt didn't. Fixed 005's "Excited to learn and build..." line. 002 and 004
   still extract their curiosity lines as skills.
2. **Code check: no two items may share the same text.** On 005, Haiku split one "and" list
   into four items, three quoting the identical full line, so one requirement counted four times
   and the decision flipped. A plain Python check now rejects the answer and retries with
   feedback. Free, deterministic, and always right: a split item quotes only its own words.
3. **Reviewer call: not built.** A reviewer that rechecks `have` claims (and may only
   downgrade them) targets false matches. After fixes 1 and 2 there was 1 false match in 29
   skill items, and neither wrong decision was a false match. An extra call on every posting
   to catch about one item isn't worth it yet.
4. **Gemini: built and tested, not run.** Only the free tier was available, where Google may
   use prompts (resume bullets) to improve its products, and the two smoke calls took 63 to 84
   seconds each (Haiku: about 6). The provider stays to show that providers are swappable.

## What the remaining disagreements are
- **003 (run-to-run variance).** The prompt change cleared the cache, so matching re-ran.
  Haiku judged "data structures" weak this time on the same bullet it called have before,
  and the decision flipped. Nothing about that line had changed.
- **005 (split granularity).** "Grasp of Operating Systems, Data structures, algorithms and at
  least one modern programming language" is one item in the label (worst skill: OS, gap) and
  two in the pipeline ("at least one language" is its own item, have). Both readings are
  defensible. Under P6's "miss at most 2", the finer split turns 0 have into 1 have and flips
  the decision. Logged as an open decision in `docs/decision_rules.md`.

## Limits
- **Five postings is too few to separate a real change from noise.** One posting is 20 points of
  decision agreement, and 003 flipped with no relevant change. These numbers show direction,
  not proof.
- The reviewer decision should be revisited with more labeled postings. If false matches stay
  above about 1 in 20 skill items, the reviewer becomes worth building and measuring.

## Cost
All of M7 cost about $0.12: the Haiku rerun $0.085, the 005 rerun $0.023, and two Gemini smoke
calls $0.002. Every call went through the budget guard.
