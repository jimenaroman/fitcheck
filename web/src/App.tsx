import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ApiError, createApi, tokenStore } from "./api";
import CheckScreen from "./CheckScreen";
import HistoryScreen from "./HistoryScreen";
import LeaderboardScreen from "./LeaderboardScreen";
import Login from "./Login";
import MatchesScreen from "./MatchesScreen";
import ResumeScreen from "./ResumeScreen";
import Splash, { Loading } from "./Splash";

// The API's address is public (it's not a secret), so it's built in. The token is not: it's typed in.
const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8001"; // local API (Transit Lab has 8000)
const SPLASH_MS = 900; // the landscape stays up at least this long, so it doesn't just flash

type Tab = "matches" | "check" | "history" | "gaps" | "resume";

export default function App() {
  const [token, setToken] = useState(() => tokenStore.get());
  const [ready, setReady] = useState(false); // false: showing the loading splash
  const [tab, setTab] = useState<Tab>("matches"); // home: today's matches
  // One api object per token, so screens that load data on mount don't reload on every render.
  const api = useMemo(() => (token ? createApi(API_URL, token) : null), [token]);

  // Already logged in: show the splash while checking the saved token is still accepted (no model call).
  useEffect(() => {
    if (!api || ready) return;
    const minimum = new Promise((done) => setTimeout(done, SPLASH_MS));
    api
      .authCheck()
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) logOut(); // token changed on Railway: ask again
        // Offline or server asleep: go in anyway; each screen says what failed.
      })
      .then(() => minimum)
      .then(() => setReady(true));
  }, [api, ready]);

  function logIn(newToken: string) {
    tokenStore.set(newToken);
    setToken(newToken);
    setReady(true); // just checked by the login form
  }

  function logOut() {
    tokenStore.clear();
    setToken(null);
    setReady(false);
  }

  if (!token || !api) return <Login apiUrl={API_URL} onLogin={logIn} />;
  if (!ready) {
    return (
      <Splash>
        <Loading />
      </Splash>
    );
  }
  return (
    <>
      <div className="page">
        {tab === "matches" && <MatchesScreen api={api} />}
        {tab === "check" && <CheckScreen api={api} />}
        {tab === "history" && <HistoryScreen api={api} />}
        {tab === "gaps" && <LeaderboardScreen api={api} />}
        {tab === "resume" && <ResumeScreen api={api} onLogOut={logOut} />}
      </div>
      <nav className="tabbar" aria-label="Main">
        {TABS.map((t) => (
          <button key={t.id} className="tab" aria-current={t.id === tab ? "page" : undefined} onClick={() => setTab(t.id)}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {t.icon}
            </svg>
            {t.label}
          </button>
        ))}
      </nav>
    </>
  );
}

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  {
    id: "matches",
    label: "Matches",
    icon: (
      <>
        <path d="M12 3.5 14.4 8.6l5.6.7-4.1 3.9 1 5.5L12 16l-4.9 2.7 1-5.5L4 9.3l5.6-.7Z" />
      </>
    ),
  },
  {
    id: "check",
    label: "Check",
    icon: (
      <>
        <rect x="4" y="3" width="16" height="18" rx="3" />
        <path d="m8.5 12.5 2.5 2.5 4.5-5" />
      </>
    ),
  },
  {
    id: "history",
    label: "History",
    icon: (
      <>
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 7.5V12l3 2" />
      </>
    ),
  },
  {
    id: "gaps",
    label: "Gaps",
    icon: (
      <>
        <path d="m2.5 20 7-12 4 6 2.5-3.5L21.5 20Z" />
        <path d="M9.5 8V3l3.5 1.5L9.5 6" />
      </>
    ),
  },
  {
    id: "resume",
    label: "Resume",
    icon: (
      <>
        <path d="M14 3H7a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3V9Z" />
        <path d="M14 3v6h6M8 13h8M8 17h5" />
      </>
    ),
  },
];
