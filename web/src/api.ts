// The one place the app talks to the fitcheck API. Screens call these functions; none of them use
// fetch directly, so errors are turned into plain sentences in one spot (friendlyError).

export type Decision = "apply" | "dont_apply" | "review_manually";
export type Eligibility = "eligible" | "unclear" | "next_year" | "filtered_out";
export type Status = "have" | "weak" | "gap" | "not_scored";

// The scored result, as score.py made it (schemas.py FitResult). Only the fields the app shows.
export interface SkillResult {
  skill: string;
  status: Status;
  reason_code: string | null;
  quote: string | null; // the resume text that proves it (verified word for word)
}

export interface RequirementResult {
  requirement_id: string;
  status: Status;
  skills: SkillResult[];
}

export interface FitResult {
  decision: Decision;
  decision_reason: string | null; // review_manually only
  required_total: number;
  required_have: number;
  required_weak: number;
  proven_pct: number | null; // null when there's nothing to score
  incl_weak_pct: number | null;
  eligibility: Eligibility;
  eligibility_checks: { status: Eligibility | null; reason: string | null }[];
  requirements: RequirementResult[];
}

/** Set when a daily match made the check (M10): where the job is, and its link to apply. */
interface FromListing {
  company: string | null;
  locations: string[];
  url: string | null;
  applied_at: string | null; // when I marked "I applied" (M11)
}

export interface NumberedRequirement {
  id: string; // r1, r2, ...: what RequirementResult.requirement_id points to
  requirement: { text: string; section: "required" | "preferred"; kind: string };
}

export interface CheckResponse extends FromListing {
  id: number;
  created_at: string;
  decision: Decision;
  eligibility: Eligibility;
  first_line: string; // usually the job title
  card: string; // the same text `fitcheck check` prints
  result: FitResult;
  requirements: NumberedRequirement[];
  cost_usd: number; // spent by this request; 0 when every answer came from the cache
  calls: number;
}

export interface CheckSummary extends FromListing {
  id: number;
  created_at: string;
  decision: Decision;
  eligibility: Eligibility;
  first_line: string; // usually the job title
}

export interface BoardEntry {
  skill: string;
  required: number; // postings that ask for it as required
  preferred: number;
  as_alternative: number; // gaps: an "or" another skill already covered (L8)
  highlighted: boolean; // gaps: preferred by more than half of postings (L6)
  reasons: Record<string, number>; // unproven: why it's weak -> postings
  marked_at: string | null; // marked_done: when I marked it
}

export interface Leaderboard {
  postings_counted: number;
  skipped_review_manually: number;
  skipped_filtered_out: number;
  skipped_duplicates: number;
  decisions: Partial<Record<Decision, number>>; // one per posting, including skipped ones
  stale_check_ids: number[]; // checked against an older resume (L7)
  strengths: BoardEntry[];
  unproven: BoardEntry[];
  marked_done: BoardEntry[];
  gaps: BoardEntry[];
}

export interface ImportResponse {
  draft: string; // resume YAML to review; saving it is a separate step (saveResume)
  items: number;
  contact_removed: number; // emails, phones, and links taken out before the model saw the file
  listed_only: string[]; // skills no bullet names: weak at best
  cost_usd: number;
}

export interface SavedResume {
  version: string;
  items: number;
  sha256: string;
}

export interface RecheckPlan {
  stale_check_ids: number[];
  estimated_cost_usd: number;
}

export interface RecheckResult {
  rechecked_ids: number[];
  remaining_ids: number[]; // still stale: it stopped early
  cost_usd: number;
  stopped: string | null; // why it stopped, e.g. the daily limit
}

export interface MatchItem {
  check_id: number;
  decision: Decision;
  eligibility: Eligibility;
  title: string;
  company: string;
  locations: string[];
  url: string;
  applied_at: string | null;
}

export interface OpenItem {
  title: string;
  company: string;
  locations: string[];
  url: string;
  posted_at: string;
  proven: number;
}

export type RunStatus = "not_run" | "running" | "complete" | "partial" | "stopped";

export interface Matches {
  day: string; // the day shown: today, or the last day that has matches
  today: string;
  status: RunStatus;
  message: string;
  counted: number;
  tries: number;
  per_day: number;
  autorun: boolean;
  categories: string[];
  estimated_cost_usd: number;
  applied_this_week: number; // M11: my success metric, last 7 days
  matches: MatchItem[]; // apply first
  to_open: OpenItem[]; // fit, but on sites the server can't read: open and paste
}

export interface Settings {
  categories: string[];
  all_categories: string[];
}

/** The server answered with an error status. `detail` is its explanation, when it gave one. */
export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

/** The request never got an answer: no network, or the server is down. */
export class NetworkError extends Error {}

/** How a request body is sent: a file upload, raw text (resume YAML), or JSON for everything else. */
function encode(body: unknown): { body?: BodyInit; contentType?: string } {
  if (body === undefined) return {};
  if (body instanceof FormData) return { body }; // the browser writes the multipart header itself
  if (typeof body === "string") return { body, contentType: "text/plain; charset=utf-8" };
  return { body: JSON.stringify(body), contentType: "application/json" };
}

export function createApi(baseUrl: string, token: string, fetchFn: typeof fetch = fetch) {
  async function send(method: string, path: string, body?: unknown): Promise<Response> {
    const encoded = encode(body);
    let response: Response;
    try {
      response = await fetchFn(`${baseUrl}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${token}`, // typed in at login, kept on the phone, never in this code
          ...(encoded.contentType ? { "Content-Type": encoded.contentType } : {}),
        },
        body: encoded.body,
      });
    } catch (e) {
      throw new NetworkError(String(e));
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(response.status, typeof data.detail === "string" ? data.detail : response.statusText);
    }
    return response;
  }

  const request = async <T>(method: string, path: string, body?: unknown) =>
    (await (await send(method, path, body)).json()) as T;

  function importResume(file: File) {
    const form = new FormData();
    form.append("file", file);
    return request<ImportResponse>("POST", "/resume/import", form);
  }

  return {
    authCheck: () => request<{ ok: boolean }>("GET", "/auth/check"),
    createCheck: (posting: string) => request<CheckResponse>("POST", "/checks", { posting }),
    listChecks: () => request<CheckSummary[]>("GET", "/checks"),
    getCheck: (id: number) => request<CheckResponse>("GET", `/checks/${id}`),
    leaderboard: () => request<Leaderboard>("GET", "/leaderboard"),
    markDone: (skill: string) => request<unknown>("PUT", `/done/${encodeURIComponent(skill)}`),
    unmarkDone: (skill: string) => request<unknown>("DELETE", `/done/${encodeURIComponent(skill)}`),
    markApplied: (id: number) => request<{ applied_at: string }>("PUT", `/checks/${id}/applied`), // free
    unmarkApplied: (id: number) => request<{ applied_at: null }>("DELETE", `/checks/${id}/applied`),
    importResume, // costs one model call (unless the same file was imported before); saves nothing
    savedResume: async () => (await send("GET", "/resume")).text(), // the YAML as saved
    saveResume: (yamlText: string) => request<SavedResume>("PUT", "/resume", yamlText),
    recheckPlan: () => request<RecheckPlan>("GET", "/recheck"), // free: shows what a recheck would cost
    recheck: () => request<RecheckResult>("POST", "/recheck"),
    matches: () => request<Matches>("GET", "/matches"), // free
    runMatches: () => request<{ started: boolean }>("POST", "/matches/run"), // starts; poll matches()
    settings: () => request<Settings>("GET", "/settings"),
    saveSettings: (categories: string[]) => request<Settings>("PUT", "/settings", { categories }),
    quota: () => request<{ used: number; limit: number; left: number }>("GET", "/checks/quota"),
    similar: (posting: string) => request<{ check: CheckSummary | null }>("POST", "/checks/similar", { posting }),
  };
}

export type Api = ReturnType<typeof createApi>;

/** One sentence the screen can show for any failure. */
export function friendlyError(error: unknown): string {
  if (error instanceof NetworkError) return "Can't reach the server. Check your connection and try again.";
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  switch (error.status) {
    case 401:
      return "Your token was refused. Log out and enter it again.";
    case 409: // the server is missing an upload: "upload first: PUT /resume, PUT /profile"
      return missingUploads(error.detail);
    case 429:
      return `Spending limit reached: ${error.detail}`;
    case 413: // file too big
    case 415: // images and other file types
    case 422:
      return error.detail;
    default:
      return `The server had a problem (${error.status}). Try again in a minute.`;
  }
}

function missingUploads(detail: string): string {
  const resume = detail.includes("/resume");
  const profile = detail.includes("/profile"); // grad dates, work authorization: no screen for it yet
  if (resume && profile) return "Upload your resume on the Resume tab, and your profile.yaml (PUT /profile), first.";
  if (resume) return "Upload your resume on the Resume tab first.";
  if (profile) return "The server has no profile yet (grad dates, work authorization). Upload profile.yaml (PUT /profile).";
  return detail;
}

// The token lives in this browser's storage on my phone only. Anyone with my unlocked phone could use
// it; for a one-person app that's the accepted trade-off (CLAUDE.md, M9).
const TOKEN_KEY = "fitcheck.token";

export const tokenStore = {
  get: (storage: Storage = localStorage) => storage.getItem(TOKEN_KEY),
  set: (token: string, storage: Storage = localStorage) => storage.setItem(TOKEN_KEY, token),
  clear: (storage: Storage = localStorage) => storage.removeItem(TOKEN_KEY),
};
