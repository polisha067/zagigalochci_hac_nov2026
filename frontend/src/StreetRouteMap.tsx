import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import "maplibre-gl/dist/maplibre-gl.css";
import { candidateName } from "./model";
import type { Candidate, Settlement } from "./networkApi";

setWorkerUrl(workerUrl);

function routeLine(map: L.Map, stops: L.LatLng[], offset: number) {
  const points = stops.map((stop) => map.project(stop));
  return points.slice(1).reduce<L.LatLng[]>((line, end, index) => {
    const start = points[index];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (length < 1) {
      line.push(map.unproject(start.add(L.point(10 + Math.abs(offset), offset))), stops[index + 1]);
      return line;
    }
    const direction = dx < 0 || (dx === 0 && dy < 0) ? -1 : 1;
    const bend = offset * direction;
    for (let sample = 1; sample <= 12; sample++) {
      const t = sample / 12;
      const arc = 4 * t * (1 - t) * bend;
      line.push(map.unproject(L.point(
        start.x + t * dx - (dy / length) * arc,
        start.y + t * dy + (dx / length) * arc,
      )));
    }
    return line;
  }, [stops[0]]);
}

export function StreetRouteMap({ candidates, settlements, recommendation, opened, onOpen }: {
  candidates: Candidate[];
  settlements: Settlement[];
  recommendation: string | null;
  opened: string | null;
  onOpen: (id: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const click = useRef(onOpen);
  const lines = useRef(new Map<string, L.Polyline>());
  const [ready, setReady] = useState(false);

  useEffect(() => {
    click.current = onOpen;
  }, [onOpen]);

  useEffect(() => {
    setReady(false);
    if (!container.current) return;
    const catalog = new Map(settlements.map((city) => [city.id, city]));
    const originHub = candidates.find((candidate) => candidate.route_hubs?.length)?.route_hubs[0];
    const origin = originHub?.settlement_id ? catalog.get(originHub.settlement_id)?.longitude : null;
    if (origin == null) return;
    const cities = new Map<string, { name: string; point: L.LatLng }>();
    const routes = candidates.flatMap((candidate, index) => {
      const stops = (candidate.route_hubs || []).map((hub) => {
        const city = hub.settlement_id ? catalog.get(hub.settlement_id) : undefined;
        if (city?.latitude == null || city.longitude == null) return null;
        const lon = origin + ((city.longitude - origin + 540) % 360) - 180;
        const point = L.latLng(city.latitude, lon);
        cities.set(city.id, { name: city.name, point });
        return point;
      });
      return stops.length > 1 && stops.every((stop) => stop !== null)
        ? [{ candidate, index, stops }]
        : [];
    });
    if (routes.length !== candidates.length || !routes.length) return;
    const webgl = document.createElement("canvas").getContext("webgl2");
    if (!webgl) return;
    webgl.getExtension("WEBGL_lose_context")?.loseContext();
    const map = L.map(container.current, {
      scrollWheelZoom: false,
      zoomControl: false,
      attributionControl: false,
      maxZoom: 19,
    });
    L.control.zoom({ zoomInTitle: "Приблизить", zoomOutTitle: "Отдалить" }).addTo(map);
    map.fitBounds(L.latLngBounds(routes.flatMap((route) => route.stops)), {
      padding: [38, 45],
      maxZoom: 12,
    });
    const basemap = maplibreGL({
      style: "https://tiles.openfreemap.org/styles/liberty",
    }).addTo(map);
    const vectorMap = basemap.getMaplibreMap();
    const showMap = () => setReady(true);
    vectorMap.once("load", showMap);
    const currentLines = new Map<string, L.Polyline>();
    lines.current = currentLines;
    const draw = () => {
      for (const { candidate, index, stops } of routes) {
        const offset = (index - (routes.length - 1) / 2) * 12;
        const points = routeLine(map, stops, offset);
        const existing = currentLines.get(candidate.id);
        if (existing) {
          existing.setLatLngs(points);
          continue;
        }
        const line = L.polyline(points, {
          color: candidate.id === recommendation ? "#006cf5" : "#64748b",
          weight: candidate.id === recommendation ? 6 : 4,
        }).addTo(map);
        const label = document.createElement("span");
        label.textContent = `Вариант ${index + 1} · ${candidateName(candidate)}`;
        line.bindTooltip(label).on("click", () => click.current(candidate.id));
        currentLines.set(candidate.id, line);
      }
      if (recommendation) currentLines.get(recommendation)?.bringToFront();
    };
    draw();
    map.on("zoomend", draw);
    for (const { name, point } of cities.values()) {
      const label = document.createElement("span");
      label.textContent = name;
      L.circleMarker(point, { radius: 5, color: "#344c67", fillColor: "#fff", fillOpacity: 1 })
        .addTo(map).bindTooltip(label, { direction: "top" });
    }
    const resize = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      vectorMap.off("load", showMap);
      map.remove();
      lines.current.clear();
    };
  }, [candidates, settlements, recommendation]);

  useEffect(() => {
    for (const [id, line] of lines.current) {
      line.setStyle({ weight: id === opened || id === recommendation ? 6 : 4 });
    }
  }, [opened, recommendation, candidates, settlements]);

  return <div ref={container} className={`route-street-map${ready ? " ready" : ""}`}
    role="region" aria-label="Интерактивная карта маршрутов" aria-hidden={!ready} />;
}
