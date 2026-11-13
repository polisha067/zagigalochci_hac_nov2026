import { transportMarkup } from "./mapTransportIcons";
import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { candidateName } from "./model";
import { routeSampler, turnHeading, sampleMapRoute, transportNames, type MapSegment } from "./mapRouteGeometry";
import type { Candidate, Settlement } from "./networkApi";
setWorkerUrl(workerUrl);

type Scene = { select: (id: string | null, playing: boolean) => void; fit: () => void; focus: (id: string | null) => void };
export function StreetRouteMap({ candidates, settlements, recommendation, opened, onOpen, playing = true, fitRequest = 0, onReadyChange }: {
  candidates: Candidate[]; settlements: Settlement[]; recommendation: string | null; opened: string | null;
  onOpen: (id: string) => void; playing?: boolean; fitRequest?: number; onReadyChange?: (ready: boolean) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const click = useRef(onOpen);
  const selection = useRef({ id: opened ?? recommendation, playing });
  const scene = useRef<Scene | null>(null);
  const [ready, setReady] = useState(false);
  click.current = onOpen;
  selection.current = { id: opened ?? recommendation, playing };
  useEffect(() => {
    setReady(false); onReadyChange?.(false);
    if (!container.current) return;
    const catalog = new Map(settlements.map((city) => [city.id, city]));
    const originHub = candidates.find((candidate) => candidate.route_hubs?.length)?.route_hubs[0];
    const origin = originHub?.settlement_id ? catalog.get(originHub.settlement_id)?.longitude : null;
    if (origin == null) return;
    const cities = new Map<string, { name: string; point: L.LatLng }>();
    const routes = candidates.flatMap((candidate, index) => {
      const stops = candidate.route_hubs.map((hub) => {
        const city = hub.settlement_id ? catalog.get(hub.settlement_id) : undefined;
        if (city?.latitude == null || city.longitude == null) return null;
        const point = L.latLng(city.latitude, origin + ((city.longitude - origin + 540) % 360) - 180);
        cities.set(city.id, { name: city.name, point });
        return point;
      });
      return stops.length > 1 && stops.every((stop) => stop !== null) ? [{ candidate, index, stops }] : [];
    });
    if (routes.length !== candidates.length || !routes.length) return;
    const webgl = document.createElement("canvas").getContext("webgl2");
    if (!webgl) return;
    webgl.getExtension("WEBGL_lose_context")?.loseContext();
    const map = L.map(container.current, { scrollWheelZoom: false, zoomControl: false, attributionControl: false, maxZoom: 16 });
    L.control.zoom({ zoomInTitle: "Приблизить", zoomOutTitle: "Отдалить" }).addTo(map);
    const bounds = L.latLngBounds(routes.flatMap((route) => route.stops));
    const host = container.current;
    const padding = () => L.point(Math.min(65, host.clientWidth * .13), Math.min(65, host.clientHeight * .14));
    const fit = () => map.fitBounds(bounds, { padding: padding(), maxZoom: 10, animate: false });
    const focus = (id: string | null) => {
      const route = routes.find(({ candidate }) => candidate.id === id);
      map.fitBounds(route ? L.latLngBounds(route.stops) : bounds, { padding: padding(), maxZoom: 10, animate: false });
    };
    focus(selection.current.id);
    const basemap = maplibreGL({ style: "https://tiles.openfreemap.org/styles/positron" }).addTo(map);
    const vectorMap = basemap.getMaplibreMap();
    const showMap = () => { setReady(true); onReadyChange?.(true); };
    vectorMap.once("load", showMap);
    const drawn = new Map<string, { halo: L.Polyline; core: L.Polyline; hit: L.Polyline; details: L.Polyline[]; segments: MapSegment[] }>();
    let marker: L.Marker | null = null, animation = 0, progress = .06, last = 0, previousId: string | null = null;
    let heading: number | null = null;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    function select(id: string | null, animate: boolean) {
      cancelAnimationFrame(animation); last = 0;
      if (id !== previousId) { progress = .06; previousId = id; heading = null; }
      for (const [key, layer] of drawn) {
        const active = key === id;
        layer.halo.setStyle({ weight: active ? 12 : 7, opacity: active ? 1 : .8 });
        layer.core.setStyle({ color: active || key === recommendation ? "#0065ef" : "#8192a8", weight: active ? 5 : 3, opacity: active ? 1 : .58 });
        layer.details.forEach((detail) => detail.setStyle({ opacity: active ? 1 : 0 }));
        if (active) { layer.halo.bringToFront(); layer.details.forEach((detail) => detail.bringToFront()); layer.core.bringToFront(); layer.hit.bringToFront(); }
      }
      const route = id ? drawn.get(id) : undefined;
      if (!route) { marker?.remove(); marker = null; return; }
      const at = routeSampler(route.segments);
      let shownMode = "";
      const paint = () => {
        const point = at(progress);
        if (!point) return;
        if (!marker) marker = L.marker(map.unproject(L.point(point.x, point.y)), { interactive: false, keyboard: false, zIndexOffset: 1000 }).addTo(map);
        if (shownMode !== point.mode) {
          shownMode = point.mode;
          marker.setIcon(L.divIcon({ className: "route-moving-vehicle", iconSize: [64, 64], iconAnchor: [32, 32], html: `<span title="${transportNames[point.mode]}">${transportMarkup(point.mode, 64)}</span>` }));
        }
        marker.setLatLng(map.unproject(L.point(point.x, point.y)));
        heading = turnHeading(heading, point.heading);
        const icon = marker.getElement()?.querySelector<HTMLElement>(".route-transport-3d");
        if (icon) icon.style.transform = `rotate(${point.mode === "pipeline" ? 0 : heading - 180}deg)`;
      };
      const tick = (now: number) => {
        if (last) progress = (progress + Math.min(now - last, 80) / 16000) % 1;
        last = now; paint(); animation = requestAnimationFrame(tick);
      };
      paint();
      if (animate && !reduced.matches) animation = requestAnimationFrame(tick);
    }
    const draw = () => {
      for (const { candidate, index, stops } of routes) {
        const offset = (index - (routes.length - 1) / 2) * 17;
        const segments = sampleMapRoute(stops.map((stop) => map.project(stop)), candidate.connections.map((leg) => leg.mode), offset);
        const latlngs = segments.flatMap((segment) => segment.points.map((p) => map.unproject(L.point(p.x, p.y))));
        const old = drawn.get(candidate.id);
        if (old) { old.halo.remove(); old.core.remove(); old.hit.remove(); old.details.forEach((layer) => layer.remove()); }
        const halo = L.polyline(latlngs, { color: "#fff", weight: 9, opacity: .9, interactive: false }).addTo(map);
        const details = segments.filter((segment) => segment.mode === "rail").map((segment) => L.polyline(
          segment.points.map((p) => map.unproject(L.point(p.x, p.y))),
          { color: "#0065ef", weight: 10, dashArray: "1 9", lineCap: "butt", opacity: 0, interactive: false },
        ).addTo(map));
        const core = L.polyline(latlngs, { color: "#8192a8", weight: 3, lineCap: "round", lineJoin: "round", interactive: false }).addTo(map);
        const hit = L.polyline(latlngs, { color: "#000", weight: 20, opacity: 0, className: "map-route-hit" }).addTo(map);
        const label = document.createElement("span");
        label.textContent = `Вариант ${index + 1} · ${candidateName(candidate)}`;
        hit.bindTooltip(label, { className: "map-route-tooltip", sticky: true }).on("click", () => click.current(candidate.id));
        drawn.set(candidate.id, { halo, core, hit, details, segments });
      }
      select(selection.current.id, selection.current.playing);
    };
    draw(); map.on("zoomend", draw);
    const first = routes[0].stops[0], end = routes[0].stops.at(-1)!;
    for (const { name, point } of cities.values()) {
      const endpoint = point.equals(first) ? "А" : point.equals(end) ? "Б" : null;
      const label = document.createElement("span"); label.textContent = name;
      if (endpoint) {
        const pin = L.marker(point, { icon: L.divIcon({ className: "route-endpoint", html: `<span>${endpoint}</span>`, iconSize: [30, 30], iconAnchor: [15, 15] }), keyboard: false }).addTo(map);
        pin.bindTooltip(label, { permanent: true, direction: "bottom", offset: [0, 15], className: "route-city-label" });
      } else L.circleMarker(point, { radius: 5, color: "#315b91", weight: 2, fillColor: "white", fillOpacity: 1 }).addTo(map).bindTooltip(label, { direction: "top", className: "route-city-label" });
    }
    scene.current = { select, fit, focus };
    const resize = new ResizeObserver(() => {
      map.invalidateSize({ pan: false });
      if (host.clientWidth && host.clientHeight) focus(selection.current.id);
    });
    resize.observe(host);
    return () => {
      cancelAnimationFrame(animation); marker?.remove(); scene.current = null; resize.disconnect();
      vectorMap.off("load", showMap); map.remove(); onReadyChange?.(false);
    };
  }, [candidates, settlements, recommendation, onReadyChange]);
  useEffect(() => { scene.current?.select(opened ?? recommendation, playing); }, [opened, recommendation, playing]);
  useEffect(() => { scene.current?.focus(opened ?? recommendation); }, [opened, recommendation]);
  useEffect(() => { if (fitRequest) scene.current?.fit(); }, [fitRequest]);
  return <div ref={container} className={`route-street-map${ready ? " ready" : ""}`} role="region" aria-label="Интерактивная карта маршрутов" aria-hidden={!ready} />;
}
