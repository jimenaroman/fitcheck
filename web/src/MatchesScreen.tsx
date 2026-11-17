import { useCallback, useEffect, useState } from "react";

import type { Api, CheckResponse, Matches, MatchItem, Settings } from "./api";
import { friendlyError } from "./api";
import Card from "./Card";
import { DECISION_SHORT, shortDate } from "./present";
import ScreenHead from "./ScreenHead";

const POLL_MS = 5000; // while a run is going, check progress every 5 seconds (free)

/** M10: today's 10, found by the server (decision_rules.md section 9). The home tab. */
export default function MatchesScreen({ api }: { api: Api }) {
  const [data, setData] = useState<Matches | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [open, setOpen] = useState<CheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.matches().then(setData, (e) => setError(friendlyError(e)));
  }, [api]);
  useEffect(load, [load]);
  useEffect(() => {
    api.settings().then(setSettings, () => {});
  }, [api]);
  useEffect(() => {
    if (data?.status !== "running") return;
    const timer = setTimeout(load, POLL_MS);
    return () => clearTimeout(timer);
  }, [data, load]);

  async function findNow() {
    setError(null);
    try {
      await api.runMatches();
      load();
    } catch (e) {
      setError(friendlyError(e));
    }
  }

  async function show(item: MatchItem) {
    setError(null);
    try {
      setOpen(await api.getCheck(item.check_id)); // the card carries the posting's link
    } catch (e) {
      setError(friendlyError(e));
    }
  }

  async function toggle(category: string) {
    if (!settings) return;
    const picked = settings.categories.includes(category)
      ? settings.categories.filter((c) => c !== category)
      : [...settings.categories, category];
    if (picked.length === 0) return; // at least one
    try {
      setSettings(await api.saveSettings(picked));
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
          Matches
        </button>
        <Card check={open} api={api} onApplied={load} />
      </section>
    );
  }

  const apply = data?.matches.filter((m) => m.decision === "apply").length ?? 0;
  const fromToday = data && data.day === data.today;
  return (
    <section>
      <ScreenHead
        title="Today's matches"
        sub={
          data
            ? `${fromToday ? `${data.matches.length} checked · ${apply} to apply` : `From ${shortDate(data.day)}`}` +
              ` · ${data.applied_this_week} applied this week`
            : undefined
        }
      />
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && <RunStatus data={data} onFindNow={findNow} />}

      {data && data.matches.length > 0 && (
        <ul className="history">
          {data.matches.map((m) => (
            <li key={m.check_id}>
              <button className="h-item" onClick={() => show(m)}>
                <div className="h-main">
                  <p className="h-title">{m.title}</p>
                  <p className="h-meta">
                    {m.applied_at && <span className="applied-tag">Applied</span>}
                    {m.company}
                    {m.locations.length > 0 && ` · ${m.locations.slice(0, 2).join(", ")}`}
                    {m.locations.length > 2 && ` +${m.locations.length - 2}`}
                  </p>
                </div>
                <span className={`badge ${m.decision}`}>{DECISION_SHORT[m.decision]}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {data && data.to_open.length > 0 && (
        <>
          <h2 className="section-title">
            More that fit <small>open, then paste on Check</small>
          </h2>
          <p className="hint">These sites can't be read automatically. Pasting one uses 1 of your 5 a day.</p>
          <ul className="skills">
            {data.to_open.map((o) => (
              <li key={o.url} className="skill-row">
                <div className="skill-main">
                  <p className="skill-name">{o.title}</p>
                  <p className="skill-why">
                    {o.company} · {o.proven} of your skills in the title · {shortDate(o.posted_at)}
                  </p>
                </div>
                <a className="btn-ghost open" href={o.url} target="_blank" rel="noreferrer">
                  Open ↗
                </a>
              </li>
            ))}
          </ul>
        </>
      )}

      {settings && (
        <details className="fold">
          <summary>
            Categories <span className="count">{settings.categories.join(", ")}</span>
          </summary>
          <p className="hint">Which roles to look for. Applies from the next run.</p>
          <div className="chips">
            {settings.all_categories.map((c) => (
              <button
                key={c}
                className={settings.categories.includes(c) ? "chip on" : "chip"}
                aria-pressed={settings.categories.includes(c)}
                onClick={() => toggle(c)}
              >
                {c}
              </button>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}

function RunStatus({ data, onFindNow }: { data: Matches; onFindNow: () => void }) {
  const left = data.per_day - data.counted;
  if (data.status === "running") {
    const started = data.tries > 0 || data.message.startsWith("Checking");
    return (
      <div className="progress" role="status" aria-live="polite">
        <div className="progress-row">
          <strong>Finding today's matches</strong>
          <span>
            {data.counted} of {data.per_day}
          </span>
        </div>
        <div className={started ? "progress-track" : "progress-track waiting"}>
          <span style={{ width: started ? `${(100 * data.counted) / data.per_day}%` : undefined }} />
        </div>
        <p className="hint">{data.message || "Starting…"}</p>
        <p className="hint">About 15 seconds per posting. You can leave this screen; it keeps going.</p>
      </div>
    );
  }
  const canFind = data.status === "not_run" || data.status === "stopped";
  return (
    <>
      {data.message && <p className={data.status === "stopped" ? "error" : "hint"}>{data.message}</p>}
      {data.status === "not_run" && (
        <p className="hint">
          {data.autorun ? "Today's run starts at 8 AM (Chicago). " : "The 8 AM run is off. "}
          Or find them now.
        </p>
      )}
      {canFind && left > 0 && (
        <button className="btn" onClick={onFindNow}>
          Find {left === data.per_day ? "today's matches" : `the other ${left}`} now (about $
          {data.estimated_cost_usd.toFixed(2)})
        </button>
      )}
    </>
  );
}
