import { useState, type FormEvent } from "react";

import { createApi, friendlyError } from "./api";
import Splash from "./Splash";

/** Logged out: the token field floats over the landscape. Asked once per device, then kept (tokenStore). */
export default function Login({ apiUrl, onLogin }: { apiUrl: string; onLogin: (token: string) => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await createApi(apiUrl, token.trim()).authCheck(); // GET /auth/check: never calls Claude
      onLogin(token.trim());
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Splash>
      <form className="login" onSubmit={submit}>
        <label className="sr-only" htmlFor="token">
          Access token
        </label>
        <input
          className="input"
          id="token"
          type="password"
          placeholder="Access token"
          autoComplete="current-password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button className="btn" disabled={busy || !token.trim()}>
          {busy ? "Checking…" : "Log in"}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </Splash>
  );
}
