import { useLayoutEffect, useRef, useState } from "react";

/** Keep suggestions inside the visible viewport; keyboard navigation scrolls only the list. */
export function useSuggestionMenu(open: boolean, active: number, close: () => void) {
  const anchor = useRef<HTMLInputElement>(null);
  const menu = useRef<HTMLElement>(null);
  const [position, setPosition] = useState<{ top: number | "auto"; bottom: number | "auto"; left: number; width: number; maxHeight: number }>({ top: 0, bottom: "auto", left: 0, width: 0, maxHeight: 280 });
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      if (!anchor.current) return;
      const rect = anchor.current.getBoundingClientRect();
      const viewport = window.visualViewport;
      const top = (viewport?.offsetTop ?? 0) + 8;
      const bottom = top + (viewport?.height ?? window.innerHeight) - 16;
      if (rect.bottom < top || rect.top > bottom) { close(); return; }
      const below = bottom - rect.bottom - 6;
      const above = rect.top - top - 6;
      const up = below < 200 && above > below;
      const maxHeight = Math.max(0, Math.min(280, up ? above : below));
      const width = Math.min(rect.width, window.innerWidth - 16);
      setPosition({ top: up ? "auto" : rect.bottom + 6, bottom: up ? window.innerHeight - rect.top + 6 : "auto",
        left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)), width, maxHeight });
    }
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    window.visualViewport?.addEventListener("resize", place);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
      window.visualViewport?.removeEventListener("resize", place);
    };
  }, [open, close]);
  useLayoutEffect(() => {
    const list = menu.current;
    const item = list?.querySelectorAll<HTMLElement>('[role="option"]')[active];
    if (!open || !list || !item) return;
    if (item.offsetTop < list.scrollTop) list.scrollTop = item.offsetTop;
    else if (item.offsetTop + item.offsetHeight > list.scrollTop + list.clientHeight)
      list.scrollTop = item.offsetTop + item.offsetHeight - list.clientHeight;
  }, [open, active]);
  return { anchor, menu, position: { ...position, position: "fixed" as const, right: "auto", zIndex: 1000 } };
}
