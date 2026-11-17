import { useCallback, useEffect, useState } from "react";

import type { Api, CheckResponse, CheckSummary } from "./api";
import { friendlyError } from "./api";
import Card from "./Card";
import { shortDate } from "./present";
import ScreenHead from "./ScreenHead";

/** Postings I found myself: up to 5 a day (M2). Before paying, it asks if I already checked it (M4). */
export default function CheckScreen({ api }: { api: Api }) {
  const [posting, setPosting] = useState("");
  const [result, setResult] = useState<CheckResponse | null>(null);
  const [seen, setSeen] = useState<CheckSummary | null>(null); // "Looks like #12"
  const [left, setLeft] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadQuota = useCallback(() => {
    api.quota().then((q) => setLeft(q.left), () => {});
  }, [api]);
  useEffect(loadQuota, [loadQuota]);

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  const check = () =>
    run("check", async () => {
      setResult(null);
      const { check: same } = await api.similar(posting); // free
      if (same) {
        setSeen(same);
        return;
      }
      await checkAnyway();
    });

  async function checkAnyway() {
    setSeen(null);
    setBusy("check");
    setResult(await api.createCheck(posting));
    loadQuota();
  }

  const openSeen = () =>
    run("open", async () => {
      setResult(await api.getCheck(seen!.id));
      setSeen(null);
    });

  return (
    <section>
      <ScreenHead title="Check a posting" sub="Found one yourself? Paste the full job description." />
      <label className="field-label" htmlFor="posting">
        Job posting
      </label>
      <textarea
        className="textarea"
        id="posting"
        rows={8}
        placeholder="Software Engineer Intern, Summer 2027&#10;Company, City&#10;&#10;Requirements…"
        value={posting}
        onChange={(e) => {
          setPosting(e.target.value);
          setSeen(null);
        }}
      />
      <button className="btn" onClick={check} disabled={busy !== null || !posting.trim() || left === 0}>
        {busy === "check" ? "Checking… (about 15 seconds)" : "Check (about $0.015)"}
      </button>
      {left !== null && (
        <p className="hint">{left === 0 ? "No checks of your own left today. More tomorrow." : `${left} of 5 left today`}</p>
      )}

      {seen && (
        <div className="notice">
          Looks like #{seen.id} ({seen.first_line}), checked {shortDate(seen.created_at)}. Open it instead?
          <div className="row-actions">
            <button className="btn-ghost" onClick={openSeen} disabled={busy !== null}>
              Open #{seen.id}
            </button>
            <button className="btn-ghost subtle" onClick={() => run("check", checkAnyway)} disabled={busy !== null}>
              Check anyway
            </button>
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {result && <Card check={result} api={api} />}
    </section>
  );
}
