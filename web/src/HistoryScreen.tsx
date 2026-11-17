import { useCallback, useEffect, useState } from "react";

import type { Api, CheckResponse, CheckSummary } from "./api";
import { friendlyError } from "./api";
import Card from "./Card";
import { DECISION_SHORT, isThisWeek, placeLine, shortDate } from "./present";
import ScreenHead from "./ScreenHead";

/** Every saved check, newest first. Tapping one opens its card (re-rendered by the server: no model call). */
export default function HistoryScreen({ api }: { api: Api }) {
  const [checks, setChecks] = useState<CheckSummary[] | null>(null);
  const [open, setOpen] = useState<CheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.listChecks().then(setChecks, (e) => setError(friendlyError(e)));
  }, [api]);
  useEffect(load, [load]);

  async function show(id: number) {
    setError(null);
    try {
      setOpen(await api.getCheck(id));
    } catch (e) {
      setError(friendlyError(e));
    }
  }

  if (open) {
    return (
      <section>
        <button className="link" onClick={() => setOpen(null)}>
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m15 5-7 7 7 7" />
          </svg>
          History
        </button>
        <Card check={open} api={api} onApplied={load} />
      </section>
    );
  }

  const recent = checks?.filter((c) => isThisWeek(c.created_at)) ?? [];
  const earlier = checks?.filter((c) => !isThisWeek(c.created_at)) ?? [];
  return (
    <section>
      <ScreenHead title="History" sub={checks ? `${checks.length} posting(s) checked` : undefined} />
      {error && <p className="error">{error}</p>}
      {checks === null && !error && <p className="muted">Loading…</p>}
      {checks?.length === 0 && <p className="muted">No checks yet. Paste a posting on the Check tab.</p>}
      <Group title="This week" checks={recent} onOpen={show} />
      <Group title="Earlier" checks={earlier} onOpen={show} />
    </section>
  );
}

function Group({ title, checks, onOpen }: { title: string; checks: CheckSummary[]; onOpen: (id: number) => void }) {
  if (checks.length === 0) return null;
  return (
    <>
      <p className="h-group">{title}</p>
      <ul className="history">
        {checks.map((c) => (
          <li key={c.id}>
            <button className="h-item" onClick={() => onOpen(c.id)}>
              <div className="h-main">
                <p className="h-title">{c.first_line || `Check #${c.id}`}</p>
                <p className="h-meta">
                  {c.applied_at && <span className="applied-tag">Applied</span>}
                  {placeLine(c.company, c.locations) ?? "Your posting"} · {shortDate(c.created_at)}
                </p>
              </div>
              <span className={`badge ${c.decision}`}>{DECISION_SHORT[c.decision]}</span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
