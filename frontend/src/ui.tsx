import type { CSSProperties } from "react";
export function Brand() {
  return (
    <div className="brand">
      <svg
        className="brand-mark"
        viewBox="0 0 44 38"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="M5 11.5 23 4c1.4-.6 2.8.6 2.3 2.1L20.6 22c-.2.7-.7 1.3-1.4 1.6L1.5 31c-.8.3-1.6-.4-1.3-1.2L4 13c.1-.7.5-1.2 1-1.5Z"
          fill="#0271FF"
        />
        <path
          d="m24 17 17.8-7.5c1.4-.6 2.8.7 2.3 2.1l-4.7 15.7c-.2.7-.7 1.3-1.4 1.6l-17.7 7.4c-.8.3-1.6-.4-1.3-1.2l3.8-16.6c.1-.7.5-1.2 1.2-1.5Z"
          fill="#0058EF"
        />
      </svg>
      <span>Попутно</span>
    </div>
  );
}
export function TransportIcon({
  rail = false,
  double = false,
}: {
  rail?: boolean;
  double?: boolean;
}) {
  return (
    <svg
      className="transport-icon"
      viewBox="0 0 104 42"
      fill="none"
      aria-hidden="true"
      style={{ "--icon-color": "currentColor" } as CSSProperties}
    >
      {rail ? (
        <g stroke="currentColor" strokeWidth="1.4">
          <path d="M4 29h45L37 8H15c-5 0-8 5-9 10L4 29Z M9 18h11V11h-5c-3 0-5 3-6 7Z M25 11h10l5 7H25Z M6 24h39 M2 35h50 M12 29v5 M39 29v5" />
          <circle cx="16" cy="31" r="3" />
          <circle cx="37" cy="31" r="3" />
        </g>
      ) : double ? (
        <g stroke="currentColor" strokeWidth="1.4" transform="translate(-5 0)">
          <path d="M6 28V10h29v18H6Z M35 17h8l7 8v6H35 M6 31h44 M38 19v6h8" />
          <circle cx="14" cy="32" r="4" />
          <circle cx="42" cy="32" r="4" />
        </g>
      ) : null}
      <g
        stroke="currentColor"
        strokeWidth="1.4"
        transform={rail || double ? "translate(51 0)" : "translate(25 0)"}
      >
        <path d="M3 28V7h33v21H3Z M36 15h9l8 10v6H36 M3 31h50 M39 18v8h10 M6 10h27 M6 25h27" />
        <circle cx="12" cy="32" r="4" />
        <circle cx="44" cy="32" r="4" />
        <path d="M0 38h56" />
      </g>
    </svg>
  );
}
