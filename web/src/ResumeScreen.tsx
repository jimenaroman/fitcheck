import { useCallback, useEffect, useState } from "react";

import type { Api, ImportResponse, RecheckPlan, RecheckResult } from "./api";
import { ApiError, friendlyError } from "./api";
import ScreenHead from "./ScreenHead";

/** M9 step 5: file -> draft -> I review and edit it -> save. Then recheck the checks the old resume made. */
export default function ResumeScreen({ api, onLogOut }: { api: Api; onLogOut: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [imported, setImported] = useState<ImportResponse | null>(null);
  const [draft, setDraft] = useState<string | null>(null); // what Save sends; editable
  const [saved, setSaved] = useState<string | null>(null);
  const [plan, setPlan] = useState<RecheckPlan | null>(null);
  const [rechecked, setRechecked] = useState<RecheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // what's running, shown on its button

  const loadPlan = useCallback(() => {
    api.recheckPlan().then(setPlan, (e) => {
      if (!(e instanceof ApiError && e.status === 409)) setError(friendlyError(e)); // 409: no resume yet
    });
  }, [api]);
  useEffect(loadPlan, [loadPlan]);

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

  const makeDraft = () =>
    run("draft", async () => {
      setSaved(null);
      const result = await api.importResume(file!);
      setImported(result);
      setDraft(result.draft);
    });

  const editSaved = () =>
    run("load", async () => {
      setImported(null);
      setSaved(null);
      setDraft(await api.savedResume());
    });

  const save = () =>
    run("save", async () => {
      const result = await api.saveResume(draft!); // the server checks it: valid resume, no contact info
      setSaved(`Saved ${result.version}: ${result.items} items. New checks use it now.`);
      setDraft(null);
      setImported(null);
      setFile(null);
      setRechecked(null);
      loadPlan();
    });

  const recheck = () =>
    run("recheck", async () => {
      setRechecked(await api.recheck());
      loadPlan();
    });

  const stale = plan?.stale_check_ids.length ?? 0;
  return (
    <section>
      <ScreenHead title="Your resume" sub="Every check compares against the saved version." ridge={false} />

      <label className="dropzone">
        <input
          type="file"
          accept=".pdf,.docx,.txt"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            e.target.value = ""; // picking the same file again still counts as a change
          }}
        />
        <svg viewBox="0 0 64 44" aria-hidden="true">
          <path
            className="c-top"
            d="M12,40 C3,40 2,28 11,26 C11,16 23,11 30,17 C34,6 52,6 54,18 C62,18 64,28 58,32 C63,36 60,40 54,40 Z"
          />
          <path className="c-arrow" d="M32,34 V20 m-6,6 6,-6 6,6" />
        </svg>
        <p className="t">Upload a new version</p>
        <p className="s">PDF, DOCX or TXT. Contact info is removed before the model reads it.</p>
      </label>

      {file && (
        <div className="file-chip">
          <div className="file-ic">{file.name.split(".").pop()?.toUpperCase().slice(0, 4)}</div>
          <div>
            <p className="name">{file.name}</p>
            <p className="meta">{Math.max(1, Math.round(file.size / 1000))} KB · nothing saved until you approve</p>
          </div>
        </div>
      )}
      <button className="btn" onClick={makeDraft} disabled={!file || busy !== null}>
        {busy === "draft" ? "Reading your resume… (about 15 seconds)" : "Make a draft (about $0.01)"}
      </button>
      <button className="link" onClick={editSaved} disabled={busy !== null}>
        {busy === "load" ? "Loading…" : "Or edit the saved resume"}
      </button>

      {error && <p className="error">{error}</p>}
      {saved && <p className="notice">{saved}</p>}

      {draft !== null && (
        <>
          <h2 className="section-title">
            Review the draft <small>edit anything that's wrong</small>
          </h2>
          {imported && (
            <ul className="facts">
              <li>
                {imported.items} items · {imported.contact_removed} contact detail(s) removed · cost $
                {imported.cost_usd.toFixed(4)}
              </li>
              {imported.listed_only.length > 0 && (
                <li>Listed, but no bullet names them (weak at best): {imported.listed_only.join(", ")}</li>
              )}
            </ul>
          )}
          <textarea
            className="textarea code"
            aria-label="Resume draft"
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button className="btn" onClick={save} disabled={busy !== null || !draft.trim()}>
            {busy === "save" ? "Saving…" : "Approve resume"}
          </button>
          <button className="link" onClick={() => setDraft(null)} disabled={busy !== null}>
            Discard
          </button>
        </>
      )}

      {stale > 0 && (
        <>
          <h2 className="section-title">Recheck old results</h2>
          <p className="hint">
            {stale} check(s) used an older resume. Rechecking re-matches them against this one.
          </p>
          <button className="btn" onClick={recheck} disabled={busy !== null}>
            {busy === "recheck" ? "Rechecking…" : `Recheck ${stale} (about $${plan!.estimated_cost_usd.toFixed(3)})`}
          </button>
        </>
      )}
      {rechecked && (
        <p className="notice">
          Rechecked {rechecked.rechecked_ids.length} for ${rechecked.cost_usd.toFixed(4)}.
          {rechecked.stopped && ` Stopped early: ${rechecked.stopped} (${rechecked.remaining_ids.length} left).`}
        </p>
      )}

      <button className="link" onClick={onLogOut} style={{ marginTop: "var(--s7)" }}>
        Log out of this device
      </button>
    </section>
  );
}
