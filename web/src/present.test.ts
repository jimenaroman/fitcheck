import { describe, expect, it } from "vitest";

import type { BoardEntry, CheckResponse } from "./api";
import { coverage, describeReasons, percentLine, frequency, isThisWeek, requirementRows, whyDecision } from "./present";

function check(): CheckResponse {
  const skill = (name: string, status: string, extra = {}) => ({ skill: name, status, reason_code: null, quote: null, ...extra });
  return {
    id: 1,
    created_at: "2026-11-10T12:00:00Z",
    decision: "apply",
    eligibility: "eligible",
    first_line: "Data Intern",
    company: null,
    locations: [],
    url: null,
    applied_at: null,
    card: "",
    cost_usd: 0,
    calls: 2,
    requirements: [
      { id: "r1", requirement: { text: "Python", section: "required", kind: "skill" } },
      { id: "r2", requirement: { text: "Graduating 2028-2029", section: "required", kind: "eligibility" } },
      { id: "r3", requirement: { text: "Tableau", section: "required", kind: "skill" } },
      { id: "r4", requirement: { text: "dbt", section: "preferred", kind: "skill" } },
    ],
    result: {
      decision: "apply",
      decision_reason: null,
      required_total: 2,
      required_have: 1,
      required_weak: 1,
      eligibility_checks: [],
      proven_pct: 50,
      incl_weak_pct: 100,
      eligibility: "eligible",
      requirements: [
        { requirement_id: "r1", status: "have", skills: [skill("python", "have", { quote: "Built a Python app" })] },
        { requirement_id: "r3", status: "weak", skills: [skill("tableau", "weak", { reason_code: "listed_not_shown" })] },
        { requirement_id: "r4", status: "gap", skills: [skill("dbt", "gap", { reason_code: "not_on_resume" })] },
      ],
    },
  } as CheckResponse;
}

describe("requirementRows", () => {
  it("joins each scored requirement's wording to its status, with one line of why", () => {
    expect(requirementRows(check())).toEqual([
      { id: "r1", text: "Python", section: "required", status: "have", why: "“Built a Python app”" },
      { id: "r3", text: "Tableau", section: "required", status: "weak", why: "Listed, not shown → Add a bullet where you used it" },
      { id: "r4", text: "dbt", section: "preferred", status: "gap", why: "Not on your resume" },
    ]); // r2 (eligibility) has no scored result, so it's not a row
  });

  it("says when verification caught a false match", () => {
    const c = check();
    c.result.requirements[2].skills[0].reason_code = "false_match_caught";
    expect(requirementRows(c)[2].why).toBe("A claimed match didn't check out");
  });
});

describe("leaderboard text", () => {
  const entry: BoardEntry = {
    skill: "git", required: 3, preferred: 0, as_alternative: 0, highlighted: false, reasons: {}, marked_at: null,
  };

  it("describes reasons, most common first, with their fixes", () => {
    expect(describeReasons({ similar_tool: 1, listed_not_shown: 3 })).toEqual([
      "Listed, not shown (3) → Add a bullet where you used it",
      "Similar tool (1) → Mention it, or try their tool",
    ]);
  });

  it("shows an unknown reason code as it is", () => {
    expect(describeReasons({ brand_new: 1 })).toEqual(["brand_new (1) → see the card"]);
  });

  it("says how many postings ask for a skill, and sizes the bar", () => {
    expect(frequency(entry, 12)).toEqual({ label: "3 of 12", pct: 25 });
    expect(frequency({ ...entry, preferred: 1, as_alternative: 2 }, 8)).toEqual({
      label: "4 of 8 · 1 preferred · 2 as an alternative",
      pct: 50,
    });
    expect(frequency(entry, 0).pct).toBe(0);
  });
});

it("groups history by the last 7 days", () => {
  const now = new Date("2026-11-10T12:00:00Z");
  expect(isThisWeek("2026-11-04T13:00:00Z", now)).toBe(true);
  expect(isThisWeek("2026-11-02T12:00:00Z", now)).toBe(false);
});

describe("the card's plain-language coverage", () => {
  const result = (decision: string, total: number, have: number, weak: number) =>
    ({ ...check().result, decision, required_total: total, required_have: have, required_weak: weak }) as never;

  it("shows both percentages the way section 6 words them", () => {
    const r = { ...check().result, required_total: 5, required_have: 3, proven_pct: 60, incl_weak_pct: 80 } as never;
    expect(percentLine(r)).toBe("60% proven, 80% incl. weak (3 of 5 required)");
    expect(percentLine({ ...check().result, proven_pct: null, incl_weak_pct: null } as never)).toBeNull();
  });

  it("splits required skills three ways", () => {
    expect(coverage(result("apply", 5, 3, 1))).toEqual({ have: 3, weak: 1, missing: 1, total: 5 });
  });

  it.each([
    ["apply", 2, 2, 0, "Your resume proves every required skill."],
    ["apply", 5, 3, 0, "2 required skills aren't proven. Up to 2 is OK."],
    ["apply", 15, 12, 0, "You prove 80% of the required skills. 80% or more is OK."],
    ["dont_apply", 4, 0, 0, "None of the required skills are proven on your resume yet."],
    ["dont_apply", 5, 2, 0, "3 required skills aren't proven (more than 2), and 40% is under 80%."],
  ])("%s with %i required, %i proven, %i weak", (decision, total, have, weak, sentence) => {
    expect(whyDecision(result(decision, total, have, weak))).toBe(sentence);
  });

  it("points out weak items that could change the answer", () => {
    expect(whyDecision(result("dont_apply", 5, 2, 2))).toContain("2 are on your resume but not shown in a bullet");
  });
});
