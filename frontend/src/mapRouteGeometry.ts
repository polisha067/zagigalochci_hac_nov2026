import type { Mode } from "./networkApi";

export const ROUTE_ANIMATION_MS = 32000;

export type MapPoint = { x: number; y: number };
export type MapSegment = { mode: Mode; points: MapPoint[] };
export const transportNames: Record<Mode, string> = {
  road: "Автомобиль", rail: "Поезд", air: "Самолёт", sea: "Судно", river: "Речное судно", pipeline: "Трубопровод",
};
/** Deliberately schematic connections. Shared by the basemap and offline fallback. */
export function sampleMapRoute(stops: MapPoint[], modes: Mode[], offset: number): MapSegment[] {
  return stops.slice(1).map((end, index) => {
    const start = stops[index];
    const dx = end.x - start.x, dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    const direction = dx < 0 || (dx === 0 && dy < 0) ? -1 : 1;
    const mode = modes[index] ?? modes[0] ?? "road";
    const points = Array.from({ length: 41 }, (_, sample) => {
      const t = sample / 40;
      if (length < 1) return { ...start };
      const bend = 4 * t * (1 - t) * offset * direction;
      return { x: start.x + t * dx - dy / length * bend, y: start.y + t * dy + dx / length * bend };
    });
    return { mode, points };
  });
}
export function segmentPath(segments: MapSegment[]) {
  return segments.map(({ points }) => points.map((point, i) => `${i ? "L" : "M"}${point.x},${point.y}`).join(" ")).join(" ");
}
export function routeSampler(segments: MapSegment[]) {
  let total = 0;
  const edges = segments.flatMap(({ points, mode }) => points.slice(1).map((end, i) => {
    const start = points[i], length = Math.hypot(end.x - start.x, end.y - start.y);
    const edge = { start, end, mode, from: total, to: total + length };
    total += length;
    return edge;
  }));
  return (progress: number) => {
    const distance = Math.max(0, Math.min(1, progress)) * total;
    const edge = edges.find((edge) => edge.to >= distance && edge.to > edge.from) ?? edges[edges.length - 1];
    if (!edge) return null;
    const t = edge.to === edge.from ? 0 : (distance - edge.from) / (edge.to - edge.from);
    return { x: edge.start.x + (edge.end.x - edge.start.x) * t,
      y: edge.start.y + (edge.end.y - edge.start.y) * t, mode: edge.mode,
      heading: edge.to === edge.from ? 0 : Math.atan2(edge.end.y - edge.start.y, edge.end.x - edge.start.x) * 180 / Math.PI + 90 };

  };
}

/** Follow the shortest turn across north, avoiding a 359° → 0° full spin. */
export function turnHeading(previous: number | null, target: number) {
  if (previous == null) return (target + 360) % 360;
  const delta = ((target - previous + 540) % 360) - 180;
  return (previous + delta * .2 + 360) % 360;
}
