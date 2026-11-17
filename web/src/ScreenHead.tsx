import type { ReactNode } from "react";

/** Each screen's title, with the small mountain ridge and flag from the mockup. */
export default function ScreenHead({ title, sub, ridge = true }: { title: string; sub?: ReactNode; ridge?: boolean }) {
  return (
    <header className="screen-head">
      <div>
        <h1>{title}</h1>
        {sub && <p className="sub">{sub}</p>}
      </div>
      {ridge && (
        <svg className="ridge" viewBox="0 -8 92 52" aria-hidden="true">
          <circle className="sun" cx="16" cy="20" r="7" />
          <path className="back" d="M0,44 C14,34 24,28 34,30 C42,32 48,36 56,40 L56,44 Z" />
          <path className="front" d="M22,44 C34,36 44,24 52,18 L56,13 C59,11 62,11 64,13 C70,18 80,30 92,40 L92,44 Z" />
          <line className="pole" x1="60" y1="12" x2="60" y2="2" />
          <path className="flag" d="M60.5,2 C62.5,1.4 63.6,3 65.6,2.4 C65,3.6 65,4.4 65.6,5.4 C63.6,6 62.5,4.4 60.5,5 Z" />
        </svg>
      )}
    </header>
  );
}
