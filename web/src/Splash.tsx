import type { ReactNode } from "react";

import landscape from "./landscape.svg?raw"; // my own file from the mockup, built in: no user input goes in it

/** The dusk landscape with the climber. `children` is the login form, or the loading bar. */
export default function Splash({ children }: { children: ReactNode }) {
  return (
    <main className="splash">
      <div dangerouslySetInnerHTML={{ __html: landscape }} />
      <div className="brand">
        <h1>FitCheck</h1>
        <p className="motto-1">Know what skills to build.</p>
        <p className="motto-2">Reach your peak.</p>
      </div>
      {children}
    </main>
  );
}

export function Loading() {
  return (
    <div className="loading" role="status" aria-live="polite">
      Loading your checks
      <div className="loading-track">
        <span />
      </div>
    </div>
  );
}
