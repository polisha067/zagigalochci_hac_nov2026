import { useRouteTrack } from "./useRouteTrack";
import { transportMarkup } from "./mapTransportIcons";
import { useEffect, useId, useMemo, useState } from "react";
import { StreetRouteMap } from "./StreetRouteMap";
import { MapVehicle } from "./MapVehicle";
import { sampleMapRoute, segmentPath } from "./mapRouteGeometry";
import { request } from "./api";
import countriesUrl from "./assets/natural-earth-countries.json?url";
import { airForecastFlightHours, candidateName, hours, money } from "./model";
import type { Candidate, Settlement, SettlementPage } from "./networkApi";

type Hub = Candidate["route_hubs"][number];
type Point = { x: number; y: number };
const width = 900;
const height = 360;
const pageSize = 250;
// Natural Earth, 1:110m admin-0 countries, public domain.
type Countries = [number, number][][][];

function basemap(countries: Countries, project: (longitude: number, latitude: number) => Point) {
  return countries.flatMap((rings) =>
    [-360, 0, 360].flatMap((shift) => {
      const outline = rings[0].map(([lon, lat]) => project(lon + shift, lat));
      if (outline.every(({ x }) => x < 0) || outline.every(({ x }) => x > width) ||
          outline.every(({ y }) => y < 0) || outline.every(({ y }) => y > height)) return [];
      const path = rings.map((ring) => "M" + ring.map(([lon, lat]) => {
        const { x, y } = project(lon + shift, lat);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join("L") + "Z").join("");
      return [path];
    }),
  );
}
const hubKey = (hub: Hub) => hub.settlement_id || hub.id;

function plotHubs(hubs: Hub[], settlements: Settlement[], endpoints: Hub[], countries: Countries, extra: [number, number][] = []) {
  const cities = new Map(settlements.map((city) => [city.id, city]));
  const coordinates = hubs.map((hub) => cities.get(hubKey(hub)));
  const geographic = coordinates.every(
    (city) => typeof city?.latitude === "number" && typeof city.longitude === "number",
  );
  if (!geographic) {
    const first = hubKey(endpoints[0]);
    const last = hubKey(endpoints[endpoints.length - 1]);
    const intermediate = hubs.filter((hub) => hubKey(hub) !== first && hubKey(hub) !== last);
    return {
      geographic,
      project: undefined,
      land: [],
      points: hubs.map((hub) => {
        const key = hubKey(hub);
        if (key === first) return { x: first === last ? 450 : 100, y: 180 };
        if (key === last) return { x: 800, y: 180 };
        const index = intermediate.indexOf(hub);
        return { x: 100 + (700 * (index + 1)) / (intermediate.length + 1), y: index % 2 ? 235 : 125 };
      }),
      names: hubs.map((hub) => cities.get(hubKey(hub))?.name || hub.name),
    };
  }
  const origin = coordinates[0]!;
  const lat = Math.cos((origin.latitude! * Math.PI) / 180);
  const raw = coordinates.map((city) => ({
    x: ((((city!.longitude! - origin.longitude! + 540) % 360) - 180) * lat),
    y: -city!.latitude!,
  }));
  const extent = [...raw, ...extra.map(([lon, latitude]) => ({ x: (((lon - origin.longitude! + 540) % 360) - 180) * lat, y: -latitude }))];
  const xs = extent.map((point) => point.x);
  const ys = extent.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min(690 / Math.max(maxX - minX, 0.1), 200 / Math.max(maxY - minY, 0.1));
  const plot = (x: number, y: number): Point => ({
    x: width / 2 + (x - (maxX + minX) / 2) * scale,
    y: height / 2 + (y - (maxY + minY) / 2) * scale,
  });
  return {
    geographic,
    project: (lon: number, latitude: number) => plot((((lon - origin.longitude! + 540) % 360) - 180) * lat, -latitude),
    land: basemap(countries, (lon, latitude) => plot((lon - origin.longitude!) * lat, -latitude)),
    points: raw.map(({ x, y }) => plot(x, y)),
    names: coordinates.map((city) => city!.name),
  };
}

export function RouteMap({ candidates, recommendation, opened, onOpen }: {
  candidates: Candidate[];
  recommendation: string | null;
  opened: string | null;
  onOpen: (id: string) => void;
}) {
  const gridId = useId();
  const [selectedId, setSelectedId] = useState(opened ?? recommendation ?? candidates[0]?.id ?? null);
  const [playing, setPlaying] = useState(true);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [fitRequest, setFitRequest] = useState(0);
  const [onlineReady, setOnlineReady] = useState(false);
  useEffect(() => { if (opened) setSelectedId(opened); }, [opened]);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(preference.matches);
    update(); preference.addEventListener("change", update);
    return () => preference.removeEventListener("change", update);
  }, []);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [countries, setCountries] = useState<Countries>([]);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(countriesUrl, { signal: controller.signal })
      .then((response) => response.ok ? response.json() : [])
      .then((data: Countries) => {
        if (!controller.signal.aborted) setCountries(data);
      })
      .catch(() => {});
    const load = (offset: number) => request<SettlementPage>(
      `/network/russia/options?limit=${pageSize}&offset=${offset}`,
      { signal: controller.signal },
    );
    async function loadSettlements() {
      const first = await load(0);
      const rest = await Promise.all(Array.from(
        { length: Math.ceil(first.total / pageSize) - 1 },
        (_, index) => load((index + 1) * pageSize),
      ));
      if (!controller.signal.aborted)
        setSettlements([first, ...rest].flatMap((page) => page.settlements));
    }
    void loadSettlements().catch(() => {
      // The route topology remains usable if coordinates cannot be loaded.
    });
    return () => controller.abort();
  }, []);

  const selected = candidates.find((candidate) => candidate.id === selectedId) ?? candidates.find((candidate) => candidate.id === recommendation) ?? candidates[0];
  const visibleCandidates = useMemo(() => selected ? [selected] : [], [selected]);
  const track = useRouteTrack(selected, settlements);
  const hubs = [...new Map(visibleCandidates.flatMap((candidate) => (candidate.route_hubs || [])
    .map((hub) => [hubKey(hub), hub] as const))).values()];
  const reference = selected ?? candidates.find((candidate) => candidate.route_hubs?.length);
  if (!reference) return null;

  const { points, geographic, names, land, project } = plotHubs(hubs, settlements, reference.route_hubs, countries, track.segments?.flatMap((s) => s.coordinates));
  const projectedTrack = project && track.segments?.map((segment) => ({ mode: segment.mode, points: segment.coordinates.map(([lon, lat]) => project(lon, lat)) }));
  const position = new Map(hubs.map((hub, index) => [hubKey(hub), points[index]]));
  const routes = candidates.map((candidate, index) => ({
    candidate,
    index,
    points: (candidate.route_hubs || []).map((hub) => position.get(hubKey(hub))!),
  }));
  const selectedRoute = routes.find((route) => route.candidate.id === selected?.id);
  const moving = playing && !reducedMotion && !selected?.modes.every((mode) => mode === "pipeline");
  const selectedSegments = projectedTrack || (selectedRoute ? sampleMapRoute(selectedRoute.points,
    selected.connections.map((leg) => leg.mode), 0) : []);

  const drawnRoutes = routes.filter((route) => route.points.length > 1 && route.candidate.id === selected?.id)
    .sort((a, b) => Number(a.candidate.id === selected?.id) - Number(b.candidate.id === selected?.id));

  return (
    <section className="route-map" aria-label="Карта маршрутов">
      <div className="route-map-heading">
        <div><span className="forecast-eyebrow">География перевозки</span><h2>Ваш груз в пути</h2></div>
        <div className="route-map-controls">
          <button type="button" disabled={reducedMotion || selected?.modes.every((mode) => mode === "pipeline")} aria-pressed={moving}
            onClick={() => setPlaying((value) => !value)}>{moving ? "Ⅱ Пауза" : "▶ Движение"}</button>
          <button type="button" disabled={!onlineReady} onClick={() => setFitRequest((value) => value + 1)}>Весь маршрут ⤢</button>
        </div>
      </div>
      <p className="route-map-intro">Выберите вариант на карте или в списке. Движение показывает последовательность участков маршрута.</p>
      <div className="route-map-layout">
      <div className="route-map-canvas">
        <svg viewBox={`0 0 ${width} ${height}`} aria-label="Схема маршрутов перевозки">
          <defs>
            <pattern id={gridId} width="30" height="30" patternUnits="userSpaceOnUse">
              <circle cx="1" cy="1" r="1" fill="#c6d7ea" />
            </pattern>
          </defs>
          {land.length > 0
            ? <g className="route-map-land" aria-hidden="true">
                {land.map((path, index) => <path key={index} d={path} />)}
              </g>
            : <rect width={width} height={height} fill={`url(#${gridId})`} />}
          {drawnRoutes.map(({ candidate, index, points: routePoints }) => {
            const segments = projectedTrack || sampleMapRoute(routePoints, candidate.connections.map((leg) => leg.mode), 0);
            const path = segmentPath(segments);
            return <g key={candidate.id}
              className={`route-map-line${candidate.id === recommendation ? " recommended" : ""}${candidate.id === selected?.id ? " active" : ""}`}
              role="button" tabIndex={0} aria-label={`Выбрать вариант ${index + 1}: ${candidateName(candidate)}`}
              onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedId(candidate.id); } }}
              onClick={() => setSelectedId(candidate.id)}>
              <title>Вариант {index + 1} · {candidateName(candidate)}</title>
              <path className="route-map-halo" d={path} />
              {candidate.id === selected?.id && segments.filter((segment) => segment.mode === "rail").map((segment, leg) =>
                <path key={leg} className="route-map-rail-ties" d={segmentPath([segment])} />)}
              <path className="route-map-stroke" d={path} />
              <path className="route-map-hit" d={path} />
            </g>;
          })}
          {selectedRoute && <MapVehicle key={selected!.id} segments={selectedSegments} playing={!!moving && !onlineReady} />}
          {points.map((point, index) => selected?.route_hubs.some((hub) => hubKey(hub) === hubKey(hubs[index])) && (
            <g className="route-map-point" key={hubKey(hubs[index])}>
              <circle cx={point.x} cy={point.y} r="6" />
              <text x={point.x} y={point.y - 13} textAnchor="middle">{names[index]}</text>
            </g>
          ))}
        </svg>
        {geographic && <StreetRouteMap candidates={visibleCandidates} settlements={settlements}
          recommendation={recommendation} opened={selected?.id ?? null} onOpen={setSelectedId}
          playing={!!moving} fitRequest={fitRequest} onReadyChange={setOnlineReady} track={track.segments} />}
      </div>
      <div className="route-map-options" aria-label="Варианты на карте">
        <div className="route-options-heading"><b>Маршруты</b><span>{routes.length} вариантов</span></div>
        {routes.map(({ candidate, index }) => (
          <button type="button" key={candidate.id} onClick={() => setSelectedId(candidate.id)}
            aria-pressed={selected?.id === candidate.id}
            className={candidate.id === recommendation ? "recommended" : undefined}>
            <span className="route-option-title"><span className="route-mode-icon" aria-hidden="true" dangerouslySetInnerHTML={{ __html: transportMarkup(candidate.connections[0]?.mode ?? candidate.modes[0] ?? "road", 36) }} /><b>{index + 1} · {candidateName(candidate)}</b></span>
            {candidate.id === recommendation && <small>Рекомендуем</small>}
            <span>{candidate.total_cost_rub == null && candidate.forecast_price?.air_median_rub != null
              ? <>{candidate.multimodal && candidate.known_cost_subtotal_rub != null
                  ? `${money(candidate.known_cost_subtotal_rub)} · учтённые этапы`
                  : `${money(candidate.forecast_price.air_median_rub)} · перелёт и авианакладная`}
                  {airForecastFlightHours(candidate) != null && <> · Перелёт около {hours(airForecastFlightHours(candidate))}</>}</>
              : <>{money(candidate.complete ? candidate.total_cost_rub : null)} · {hours(candidate.max_delivery_hours)}</>}</span>
          </button>
        ))}
      </div>
      </div>
      {selected && <div className="route-map-selection" aria-live="polite">
        <div><span className="forecast-eyebrow">Выбранный маршрут{selected.id === recommendation ? " · Рекомендуем" : ""}</span>
          <strong>{candidateName(selected)}</strong>
          <span>{money(selected.complete ? selected.total_cost_rub : null)} · {hours(selected.max_delivery_hours)}</span>
        </div>
        <button type="button" className="primary" onClick={() => onOpen(selected.id)}>Подробный расчёт ↗</button>
      </div>}
      <div className="route-map-caption"><span><i /> Выбранный маршрут</span>
        <span role="status">{track.loading ? "Уточняем геометрию дорог и путей…" : track.complete ? "Геометрия дорог и железнодорожных путей по OpenStreetMap" : track.segments ? "Геометрия OSM · часть участков показана схемой" : "Схема между узлами · подробный трек недоступен"}</span></div>
      {geographic && <small className="route-map-credit route-map-credit-online">
        <a href="https://openfreemap.org/">OpenFreeMap</a> · © <a href="https://openmaptiles.org/">OpenMapTiles</a> · Данные ©{" "}
        <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>
      </small>}
      {land.length > 0 && <small className="route-map-credit route-map-credit-fallback">Подложка Natural Earth</small>}
    </section>
  );
}
