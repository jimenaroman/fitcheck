import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";

import type { Api, BoardEntry, Leaderboard } from "./api";
import { friendlyError } from "./api";
import { describeReasons, frequency, shortDate } from "./present";
import ScreenHead from "./ScreenHead";

const TOP = 5; // the two boards I act on show their top 5; "Show all" opens the rest

/** decision_rules.md section 7: what to learn, what to prove, what I already have. */
export default function LeaderboardScreen({ api }: { api: Api }) {
  const [board, setBoard] = useState<Leaderboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.leaderboard().then(setBoard, (e) => setError(friendlyError(e)));
  }, [api]);
  useEffect(load, [load]);

  async function toggleDone(skill: string, done: boolean) {
    setError(null);
    try {
      await (done ? api.unmarkDone(skill) : api.markDone(skill));
      load(); // the server moves it between boards (L9); reload to show where it went
    } catch (e) {
      setError(friendlyError(e));
    }
  }

  const n = board?.postings_counted ?? 0;
  return (
    <section>
      <ScreenHead title="Your gaps" sub={board ? `What blocks you most, across ${n} posting(s)` : undefined} />
      {error && <p className="error">{error}</p>}
      {!board && !error && <p className="muted">Loading…</p>}
      {board && n === 0 && <p className="muted">Check a few postings first.</p>}
      {board && n > 0 && (
        <>
          <div className="summary-strip">
            <div>
              <strong>{board.decisions.apply ?? 0}</strong>Apply
            </div>
            <div>
              <strong>{board.decisions.review_manually ?? 0}</strong>Review
            </div>
            <div>
              <strong>{board.decisions.dont_apply ?? 0}</strong>Not yet
            </div>
          </div>
          {board.stale_check_ids.length > 0 && (
            <p className="notice">
              {board.stale_check_ids.length} check(s) used an older resume, so these may be out of date. Recheck them on
              the Resume tab.
            </p>
          )}

          <Board
            title="Top gaps"
            hint="Not on your resume. Learn these first."
            entries={board.gaps}
            postings={n}
            bar=""
            action={(e) => (
              <button className="btn-ghost" onClick={() => toggleDone(e.skill, false)}>
                I've done this
              </button>
            )}
          />
          <Board
            title="Unproven on your resume"
            hint="You have something; the resume doesn't prove it yet."
            entries={board.unproven}
            postings={n}
            bar="weak"
            details={(e) => describeReasons(e.reasons)}
          />

          <details className="fold">
            <summary>
              Strengths <Count n={board.strengths.length} />
            </summary>
            <Board entries={board.strengths} postings={n} bar="strong" showAll />
          </details>
          <details className="fold">
            <summary>
              Done <Count n={board.marked_done.length} />
            </summary>
            <p className="hint">Learned, but not on your resume yet: add a bullet, and the next check proves it.</p>
            <Board
              entries={board.marked_done}
              postings={n}
              done
              showAll
              details={(e) => (e.marked_at ? [`Marked done ${shortDate(e.marked_at)}`] : [])}
              action={(e) => (
                <button className="btn-ghost subtle" onClick={() => toggleDone(e.skill, true)}>
                  Undo
                </button>
              )}
            />
          </details>
        </>
      )}
    </section>
  );
}

function Count({ n }: { n: number }) {
  return (
    <span className="count">
      {n} skill(s)
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </span>
  );
}

function Board({ title, hint, entries, postings, bar = "", done = false, details, action, showAll = false }: {
  title?: string;
  hint?: string;
  entries: BoardEntry[];
  postings: number;
  bar?: "" | "weak" | "strong";
  done?: boolean;
  details?: (entry: BoardEntry) => string[];
  action?: (entry: BoardEntry) => ReactNode;
  showAll?: boolean;
}) {
  const [expanded, setExpanded] = useState(showAll);
  const shown = expanded ? entries : entries.slice(0, TOP);
  return (
    <>
      {title && (
        <h2 className="section-title">
          {title} <small>postings asking</small>
        </h2>
      )}
      {hint && <p className="hint">{hint}</p>}
      {entries.length === 0 && <p className="muted">None.</p>}
      <ul className="skills">
        {shown.map((e, i) => {
          const freq = frequency(e, postings);
          return (
            <li key={e.skill} className={done ? "skill-row done" : "skill-row"}>
              <span className="rank">{done ? "✓" : i + 1}</span>
              <div className="skill-main">
                <p className="skill-name">
                  {e.skill} {e.highlighted && <span className="badge eligibility">often preferred</span>}
                </p>
                <div className="skill-count">
                  {!done && (
                    <div className={`freq ${bar}`}>
                      <span style={{ "--v": `${freq.pct}%` } as CSSProperties} />
                    </div>
                  )}
                  {freq.label}
                </div>
                {details?.(e).map((line) => (
                  <p key={line} className="skill-why">
                    {line}
                  </p>
                ))}
              </div>
              {action?.(e)}
            </li>
          );
        })}
      </ul>
      {!expanded && entries.length > TOP && (
        <button className="link" onClick={() => setExpanded(true)}>
          Show all {entries.length}
        </button>
      )}
    </>
  );
}
