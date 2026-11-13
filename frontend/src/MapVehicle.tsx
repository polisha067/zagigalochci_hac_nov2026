import { transportMarkup } from "./mapTransportIcons";
import { useEffect, useRef } from "react";
import { ROUTE_ANIMATION_MS, routeSampler, turnHeading, transportNames, type MapSegment } from "./mapRouteGeometry";

export function MapVehicle({ segments, playing }: { segments: MapSegment[]; playing: boolean }) {
  const marker = useRef<SVGGElement>(null);
  const symbol = useRef<SVGForeignObjectElement>(null);
  const progress = useRef(.06);
  const heading = useRef<number | null>(null);
  const routeKey = JSON.stringify(segments);
  useEffect(() => {
    const at = routeSampler(segments);
    let frame = 0, last = 0, mode = "";
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const paint = () => {
      const point = at(progress.current);
      if (!point || !marker.current || !symbol.current) return;
      marker.current.setAttribute("transform", `translate(${point.x} ${point.y})`);
      if (mode !== point.mode) {
        mode = point.mode;
        symbol.current.innerHTML = `<div xmlns="http://www.w3.org/1999/xhtml" class="route-vehicle-symbol" title="${transportNames[point.mode]}">${transportMarkup(point.mode, 64)}</div>`;
      }
      heading.current = turnHeading(heading.current, point.heading);
      const icon = symbol.current.querySelector<HTMLElement>(".route-transport-3d");
      if (icon) icon.style.transform = `rotate(${point.mode === "pipeline" ? 0 : heading.current - 180}deg)`;
    };
    function tick(now: number) {
      if (last) progress.current = (progress.current + Math.min(now - last, 80) / ROUTE_ANIMATION_MS) % 1;
      last = now;
      paint();
      frame = requestAnimationFrame(tick);
    }
    const start = () => { cancelAnimationFrame(frame); last = 0; paint(); if (playing && !reduced.matches) frame = requestAnimationFrame(tick); };
    start();
    reduced.addEventListener("change", start);
    return () => { cancelAnimationFrame(frame); reduced.removeEventListener("change", start); };
  }, [routeKey, playing]);
  return <g ref={marker} className="route-vehicle-fallback" aria-hidden="true">
    <foreignObject ref={symbol} x="-32" y="-32" width="64" height="64" />
  </g>;
}
