import type { Mode } from "./networkApi";
const paths: Record<Mode, string> = {
  road: '<path d="M3 6h11v11H3zM14 10h4l3 4v3h-7"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
  rail: '<rect x="6" y="3" width="12" height="15" rx="4"/><path d="M6 10h12M12 3v7M8 18l-3 4M16 18l3 4M7 21h10"/><path d="M9 14h.01M15 14h.01"/>',
  air: '<path d="m21 3-7 18-3-8-8-3 18-7ZM11 13l6-6"/>',
  sea: '<path d="M3 13l9-3 9 3-3 6H6l-3-6ZM7 11V6h10v5M10 6V3h4v3M2 21c2 2 3-2 5 0s3-2 5 0 3-2 5 0 3-2 5 0"/>',
  river: '<path d="M3 14h18l-4 5H7l-4-5ZM7 14V8h10v6M10 8V4h4v4M2 21c2 2 3-2 5 0s3-2 5 0 3-2 5 0 3-2 5 0"/>',
  pipeline: '<path d="M3 8h6v8h12M3 5v6M18 13v6M9 5v3M6 5h6"/><circle cx="9" cy="5" r="2"/>',
};
export function TransportGlyph({ mode }: { mode: Mode }) {
  return <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" dangerouslySetInnerHTML={{__html: paths[mode]}} />;
}
