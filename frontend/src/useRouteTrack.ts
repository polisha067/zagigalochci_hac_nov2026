import type { MapRoute } from "./RouteMap";
import { useEffect, useState } from "react";
import type { Mode, Settlement } from "./networkApi";
export type TrackSegment = { mode: Mode; coordinates: [number, number][]; source: "OSRM" | "BRouter" | null };
const cache = new Map<string, TrackSegment>();
type RailFeature = { geometry?: { coordinates?: number[][] }; properties?: { messages?: string[][] } };
async function json(url: string, signal: AbortSignal) {
  const timeout = AbortSignal.timeout(25000);
  const response = await fetch(url, { signal: AbortSignal.any([signal, timeout]) });
  if (!response.ok) throw new Error("Route geometry unavailable");
  return response.json();
}
function coordinates(value?: number[][]): [number, number][] {
  if (!value || value.length < 2 || value.length > 100000 || value.some((p) => !Number.isFinite(p[0]) || !Number.isFinite(p[1]) || Math.abs(p[0]) > 180 || Math.abs(p[1]) > 90)) throw new Error("Invalid track");
  return value.map((p) => [p[0], p[1]]);
}
async function rail(from: [number, number], to: [number, number], signal: AbortSignal) {
  const query = new URLSearchParams({ lonlats: `${from.join(",")}|${to.join(",")}`, profile: "rail", alternativeidx: "0", format: "geojson" });
  const data = await json(`https://brouter.de/brouter?${query}`, signal);
  const feature = data.features?.find((f: RailFeature) => f.geometry?.coordinates?.length) as RailFeature | undefined;
  if (!feature) throw new Error("No rail geometry");
  return feature;
}
function hasUrbanRail(feature: RailFeature) {
  return feature.properties?.messages?.slice(1).some((row) => /railway=(subway|tram|light_rail)(?:\s|$)/.test(row[9] ?? ""));
}
async function getSegment(mode: Mode, from: [number, number], to: [number, number], signal: AbortSignal): Promise<TrackSegment> {
  const fallback: TrackSegment = { mode, coordinates: [from, to], source: null };
  if (mode !== "road" && mode !== "rail" || from.every((n, i) => Math.abs(n - to[i]) < .00001)) return fallback;
  const key = `${mode}:${from.join(",")}:${to.join(",")}`;
  const cached = cache.get(key);
  if (cached) return cached;
  try {
    let points: [number, number][];
    if (mode === "road") {
      const data = await json(`https://router.project-osrm.org/route/v1/driving/${from.join(",")};${to.join(",")}?overview=full&geometries=geojson&steps=false`, signal);
      points = coordinates(data.routes?.[0]?.geometry?.coordinates);
    } else {
      let feature = await rail(from, to, signal);
      // City centres can snap to metro tracks. Move the endpoints onto mainline
      // rail from the returned OSM ledger, then request the rail route again.
      if (hasUrbanRail(feature)) {
        const mainline = feature.properties?.messages?.slice(1).filter((row) => /railway=rail(?:\s|$)/.test(row[9] ?? "")) ?? [];
        if (mainline.length < 2) throw new Error("No mainline rail");
        const point = (row: string[]): [number, number] => [Number(row[0]) / 1e6, Number(row[1]) / 1e6];
        feature = await rail(point(mainline[0]), point(mainline.at(-1)!), signal);
        if (hasUrbanRail(feature)) throw new Error("Urban rail is not a freight path");
      }
      points = coordinates(feature.geometry?.coordinates);
    }
    const result: TrackSegment = { mode, coordinates: points, source: mode === "road" ? "OSRM" : "BRouter" };
    if (cache.size > 80) cache.delete(cache.keys().next().value!);
    cache.set(key, result);
    return result;
  } catch (error) {
    if (signal.aborted) throw error;
    return fallback;
  }
}
export function useRouteTrack(candidate: MapRoute | undefined, settlements: Settlement[]) {
  const catalog = new Map(settlements.map((city) => [city.id, city]));
  const stops = candidate?.route_hubs.map((hub) => catalog.get(hub.settlement_id ?? ""));
  const valid = !!stops?.length && stops.every((p) => p?.longitude != null && p.latitude != null);
  const requestKey = valid && candidate ? JSON.stringify({ id: candidate.id,
    stops: stops!.map((p) => [p!.longitude, p!.latitude]), modes: candidate.segmentModes ?? candidate.modes }) : "";
  const [state, setState] = useState<{ key: string; segments: TrackSegment[] }>();
  useEffect(() => {
    if (!requestKey) return;
    const controller = new AbortController();
    const { stops, modes } = JSON.parse(requestKey) as { stops: [number, number][]; modes: Mode[] };
    void (async () => {
      const segments: TrackSegment[] = [];
      for (let i = 1; i < stops.length; i++) {
        if (controller.signal.aborted) return;
        segments.push(await getSegment(modes[i - 1] ?? modes[0] ?? "road", stops[i - 1], stops[i], controller.signal));
      }
      if (!controller.signal.aborted) setState({ key: requestKey, segments });
    })().catch(() => {});
    return () => controller.abort();
  }, [requestKey]);
  const segments = state?.key === requestKey ? state.segments : undefined;
  const real = segments?.filter((s) => s.source).length ?? 0;
  const significant = segments?.filter((s) => !s.coordinates[0].every((n, i) => Math.abs(n - s.coordinates.at(-1)![i]) < .00001)) ?? [];
  return { segments: real ? segments : undefined, loading: !!requestKey && state?.key !== requestKey,
    complete: !!significant.length && significant.every((s) => !!s.source) };
}
