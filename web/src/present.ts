// Turns what the server sent into what the screens show. No rules are applied here: decisions, statuses,
// and percentages all come from score.py. This file only picks words and widths.

import type { BoardEntry, CheckResponse, Decision, Eligibility, FitResult, Status } from "./api";

export const DECISION: Record<Decision, string> = {
  apply: "Apply",
  dont_apply: "Not yet",
  review_manually: "Review manually",
};

/** Shorter badges for list rows, so long titles keep their room. */
export const DECISION_SHORT: Record<Decision, string> = { ...DECISION, review_manually: "Review" };

export const ELIGIBILITY: Record<Eligibility, string | null> = {
  eligible: null, // nothing to say
  unclear: "Eligibility unclear",
  next_year: "Next year",
  filtered_out: "Outside your filter",
};

/** Why a check needs a human look (decision_rules.md P1-P3). */
export const REVIEW_REASON: Record<string, string> = {
  invalid_output: "The model's answer failed the checks twice, so nothing was scored.",
  not_a_posting: "This doesn't look like a job posting.",
  unreliable_matching: "Too many matches failed verification to trust the result.",
};

/** P6, restated in words for the card. Explains the decision score.py made; never makes one. */
export const MAX_MISSING = 2;
export const APPLY_PCT = 80;

export const STATUS_LABEL: Record<Status, string> = { have: "Have", weak: "Weak", gap: "Gap", not_scored: "Info" };

/** Label and fix for each weak reason: the card's table (decision_rules.md section 6). */
export const REASONS: Record<string, { label: string; fix: string }> = {
  listed_not_shown: { label: "Listed, not shown", fix: "Add a bullet where you used it" },
  similar_tool: { label: "Similar tool", fix: "Mention it, or try their tool" },
  smaller_scale: { label: "Smaller scale", fix: "Describe or build something bigger" },
  coursework_only: { label: "Coursework only", fix: "Add a project that uses it" },
  years_not_shown: { label: "Years not shown", fix: "State the duration in a bullet" },
};

export interface RequirementRow {
  id: string;
  text: string;
  section: "required" | "preferred";
  status: Status;
  why: string;
}

/** One row per scored requirement, in posting order: its wording, status, and one line of why. */
export function requirementRows(check: CheckResponse): RequirementRow[] {
  const results = new Map(check.result.requirements.map((r) => [r.requirement_id, r]));
  return check.requirements.flatMap(({ id, requirement }) => {
    const result = results.get(id);
    if (!result) return []; // eligibility lines: shown as a badge, not a row
    const skill = result.skills.find((s) => s.status === result.status) ?? result.skills[0];
    return [{ id, text: requirement.text, section: requirement.section, status: result.status, why: why(result.status, skill) }];
  });
}

function why(status: Status, skill?: { reason_code: string | null; quote: string | null }): string {
  if (status === "have") return skill?.quote ? `“${skill.quote}”` : "On your resume";
  if (status === "weak") {
    const reason = skill?.reason_code ? REASONS[skill.reason_code] : undefined;
    return reason ? `${reason.label} → ${reason.fix}` : "Weak evidence; see the full card";
  }
  if (status === "gap") {
    return skill?.reason_code === "false_match_caught" ? "A claimed match didn't check out" : "Not on your resume";
  }
  return "Not scored";
}

/** The most common reason first: "Listed, not shown (3)". Unknown codes are shown as they are. */
export function describeReasons(reasons: Record<string, number>): string[] {
  return Object.entries(reasons)
    .sort(([a, x], [b, y]) => y - x || a.localeCompare(b))
    .map(([code, n]) => `${REASONS[code]?.label ?? code} (${n}) → ${REASONS[code]?.fix ?? "see the card"}`);
}

/** "7 of 12 · 2 preferred" and the bar width. A posting counts a skill once (L1), so it never passes 100%. */
export function frequency(entry: BoardEntry, postings: number): { label: string; pct: number } {
  const asking = entry.required + entry.preferred;
  const parts = [`${asking} of ${postings}`];
  if (entry.preferred) parts.push(`${entry.preferred} preferred`);
  if (entry.as_alternative) parts.push(`${entry.as_alternative} as an alternative`);
  return { label: parts.join(" · "), pct: postings ? Math.min(100, (100 * asking) / postings) : 0 };
}

/** History groups: checks from the last 7 days, then the rest. */
export function isThisWeek(iso: string, now: Date = new Date()): boolean {
  return now.getTime() - new Date(iso).getTime() < 7 * 24 * 60 * 60 * 1000;
}

export function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** "Stripe · Chicago, IL +2", or null for my own pastes (no company known). */
export function placeLine(company: string | null, locations: string[]): string | null {
  if (!company) return null;
  const place = locations.length ? ` · ${locations[0]}${locations.length > 1 ? ` +${locations.length - 1}` : ""}` : "";
  return company + place;
}

export interface Coverage {
  have: number;
  weak: number;
  missing: number;
  total: number;
}

/** The required skills split three ways, for the card's one bar. */
export function coverage(result: FitResult): Coverage {
  const have = result.required_have;
  const weak = result.required_weak;
  return { have, weak, missing: result.required_total - have - weak, total: result.required_total };
}

/** Both percentages, worded as section 6 requires: "60% proven, 80% incl. weak (3 of 5 required)". */
export function percentLine(result: FitResult): string | null {
  if (result.proven_pct === null || result.incl_weak_pct === null) return null;
  const pct = (n: number) => `${Math.round(n)}%`;
  return `${pct(result.proven_pct)} proven, ${pct(result.incl_weak_pct)} incl. weak (${result.required_have} of ${result.required_total} required)`;
}

/** One sentence: why the card says Apply or Not yet (P6), plus a nudge when weak items could flip it. */
export function whyDecision(result: FitResult): string | null {
  const { have, weak, missing, total } = coverage(result);
  if (result.decision === "review_manually" || total === 0) return null;
  const notProven = missing + weak; // only "have" counts (P6)
  const pct = Math.round((100 * have) / total);
  let why: string;
  if (result.decision === "apply") {
    why =
      notProven === 0
        ? "Your resume proves every required skill."
        : notProven <= MAX_MISSING
          ? `${notProven} required ${notProven === 1 ? "skill isn't" : "skills aren't"} proven. Up to ${MAX_MISSING} is OK.`
          : `You prove ${pct}% of the required skills. ${APPLY_PCT}% or more is OK.`;
  } else {
    why =
      have === 0
        ? "None of the required skills are proven on your resume yet."
        : `${notProven} required skills aren't proven (more than ${MAX_MISSING}), and ${pct}% is under ${APPLY_PCT}%.`;
  }
  if (weak > 0) why += ` ${weak} ${weak === 1 ? "is" : "are"} on your resume but not shown in a bullet: proving ${weak === 1 ? "it" : "them"} counts.`;
  return why;
}

/** Why eligibility isn't plain "eligible": the first reason score.py gave. */
export function eligibilityNote(result: FitResult): string | null {
  if (result.eligibility === "eligible") return null;
  return result.eligibility_checks.find((c) => c.status === result.eligibility && c.reason)?.reason ?? null;
}
