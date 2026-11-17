import { useState } from "react";

import type { Api, CheckResponse, FitResult } from "./api";
import { friendlyError } from "./api";
import type { RequirementRow } from "./present";
import {
  DECISION,
  ELIGIBILITY,
  REVIEW_REASON,
  STATUS_LABEL,
  coverage,
  eligibilityNote,
  percentLine,
  placeLine,
  requirementRows,
  shortDate,
  whyDecision,
} from "./present";

/** One check's result. Every number and status here was decided by score.py; the card only shows them.
 * `onApplied` lets the screen that opened it refresh its list after "I applied". */
export default function Card({ check, api, onApplied }: { check: CheckResponse; api: Api; onApplied?: () => void }) {
  const { result } = check;
  const rows = requirementRows(check);
  const required = rows.filter((r) => r.section === "required");
  const preferred = rows.filter((r) => r.section === "preferred");
  const eligibility = ELIGIBILITY[check.eligibility];

  return (
    <article className="card result">
      <div className="result-top">
        <span className={`badge lg ${check.decision}`}>{DECISION[check.decision]}</span>
        {eligibility && <span className="badge eligibility">{eligibility}</span>}
        <span className="result-date">{shortDate(check.created_at)}</span>
      </div>
      <h2>{check.first_line || `Check #${check.id}`}</h2>
      {placeLine(check.company, check.locations) && <p className="company">{placeLine(check.company, check.locations)}</p>}

      {result.decision_reason && <p className="notice">{REVIEW_REASON[result.decision_reason] ?? result.decision_reason}</p>}
      {result.required_total > 0 && result.decision !== "review_manually" && <Coverage result={result} />}
      {eligibilityNote(result) && (
        <p className="bar-note">
          <b>{ELIGIBILITY[check.eligibility]}:</b> {eligibilityNote(result)}
        </p>
      )}

      <Requirements title="Required" rows={required} />
      <Requirements title="Preferred" note="never changes the decision" rows={preferred} />

      <details className="full-card">
        <summary className="link">Full card</summary>
        <pre>{check.card}</pre>
      </details>
      {check.url && (
        <a className="btn" href={check.url} target="_blank" rel="noreferrer">
          Open the posting to apply ↗
        </a>
      )}
      <Applied check={check} api={api} onChange={onApplied} />
      <div className="cost">
        <span>Cost of this check</span>
        <b>${check.cost_usd.toFixed(3)}</b>
      </div>
    </article>
  );
}

/** M11: "I applied", my success metric. A record only: it never changes the score or the matches. */
function Applied({ check, api, onChange }: { check: CheckResponse; api: Api; onChange?: () => void }) {
  const [at, setAt] = useState(check.applied_at);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      setAt(at ? (await api.unmarkApplied(check.id)).applied_at : (await api.markApplied(check.id)).applied_at);
      onChange?.();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      {at ? (
        <p className="applied">
          <span>✓ Applied {shortDate(at)}</span>
          <button className="btn-ghost subtle" onClick={toggle} disabled={busy}>
            Undo
          </button>
        </p>
      ) : (
        <button className="btn secondary" onClick={toggle} disabled={busy}>
          I applied
        </button>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}

/** The required skills as one bar: proven, on the resume but not proven (weak), and missing. */
function Coverage({ result }: { result: FitResult }) {
  const c = coverage(result);
  const width = (n: number) => `${(100 * n) / c.total}%`;
  return (
    <div className="coverage">
      <p className="coverage-head">
        <strong>
          {c.have} of {c.total}
        </strong>{" "}
        required skills proven
      </p>
      <div className="stack" aria-hidden="true">
        <span className="have" style={{ width: width(c.have) }} />
        <span className="weak" style={{ width: width(c.weak) }} />
      </div>
      <p className="legend">
        <span className="key have" /> Proven {c.have}
        <span className="key weak" /> Not shown {c.weak}
        <span className="key missing" /> Missing {c.missing}
      </p>
      {percentLine(result) && <p className="pct-line">{percentLine(result)}</p>}
      <p className="bar-note">{whyDecision(result)}</p>
    </div>
  );
}

function Requirements({ title, note, rows }: { title: string; note?: string; rows: RequirementRow[] }) {
  if (rows.length === 0) return null;
  return (
    <>
      <h3 className="section-title">
        {title} <small>{note ?? `${rows.length} total`}</small>
      </h3>
      <ul className="reqs">
        {rows.map((r) => (
          <li key={r.id} className="req">
            <span className={`status ${r.status}`}>{STATUS_LABEL[r.status]}</span>
            <div>
              <p className="req-name">{r.text}</p>
              <p className="req-why">{r.why}</p>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
