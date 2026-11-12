import { useEffect, useId, useState } from "react";
import { StreetRouteMap } from "./StreetRouteMap";
import { request } from "./api";
import countriesUrl from "./assets/natural-earth-countries.json?url";
import { airForecastFlightHours, candidateName, forecastMoney, hours, money } from "./model";
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

function plotHubs(hubs: Hub[], settlements: Settlement[], endpoints: Hub[], countries: Countries) {
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
  const xs = raw.map((point) => point.x);
  const ys = raw.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const scale = Math.min(690 / Math.max(maxX - minX, 0.1), 200 / Math.max(maxY - minY, 0.1));
  const plot = (x: number, y: number): Point => ({
    x: width / 2 + (x - (maxX + minX) / 2) * scale,
    y: height / 2 + (y - (maxY + minY) / 2) * scale,
  });
  return {
    geographic,
    land: basemap(countries, (lon, latitude) => plot((lon - origin.longitude!) * lat, -latitude)),
    points: raw.map(({ x, y }) => plot(x, y)),
    names: coordinates.map((city) => city!.name),
  };
}

function routePath(points: Point[], offset: number) {
  return points.slice(1).reduce((path, end, index) => {
    const start = points[index];
    const dx = end.x - start.x, dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (length < 1) {
      const radius = 20 + Math.abs(offset) / 2;
      const side = offset < 0 ? -1 : 1;
      return path + ` C ${start.x + radius} ${start.y + radius * side}, ${start.x - radius} ${start.y + radius * side}, ${end.x} ${end.y}`;
    }
    // Keep parallel options apart even when they use the same pair of cities.
    const direction = dx < 0 || (dx === 0 && dy < 0) ? -1 : 1;
    const x = (start.x + end.x) / 2 - (dy / length) * offset * direction;
    const y = (start.y + end.y) / 2 + (dx / length) * offset * direction;
    return path + ` Q ${x} ${y}, ${end.x} ${end.y}`;
  }, `M ${points[0].x} ${points[0].y}`);
}

export function RouteMap({ candidates, recommendation, opened, onOpen }: {
  candidates: Candidate[];
  recommendation: string | null;
  opened: string | null;
  onOpen: (id: string) => void;
}) {
  const gridId = useId();
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

  const hubs = [...new Map(candidates.flatMap((candidate) => (candidate.route_hubs || [])
    .map((hub) => [hubKey(hub), hub] as const))).values()];
  const reference = candidates.find((candidate) => candidate.route_hubs?.length);
  if (!reference) return null;

  const { points, geographic, names, land } = plotHubs(hubs, settlements, reference.route_hubs, countries);
  const position = new Map(hubs.map((hub, index) => [hubKey(hub), points[index]]));
  const routes = candidates.map((candidate, index) => ({
    candidate,
    index,
    points: (candidate.route_hubs || []).map((hub) => position.get(hubKey(hub))!),
  }));
  const active = opened ?? recommendation ?? candidates[0]?.id;
  const drawnRoutes = routes.filter((route) => route.points.length > 1)
    .sort((a, b) => Number(a.candidate.id === active) - Number(b.candidate.id === active));

  return (
    <section className="route-map" aria-label="Карта маршрутов">
      <h2>Карта маршрутов</h2>
      <p>Выберите вариант на схеме или в списке, чтобы открыть расчёт.</p>
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
            const path = routePath(routePoints, (index - (routes.length - 1) / 2) * Math.min(18, 82 / routes.length));
            return <g key={candidate.id}
              className={`route-map-line${candidate.id === active ? " active" : ""}`}
              onClick={() => onOpen(candidate.id)}>
              <title>Вариант {index + 1} · {candidateName(candidate)}</title>
              <path className="route-map-hit" d={path} />
              <path className="route-map-stroke" d={path} />
            </g>;
          })}
          {points.map((point, index) => (
            <g className="route-map-point" key={hubKey(hubs[index])}>
              <circle cx={point.x} cy={point.y} r="6" />
              <text x={point.x} y={point.y - 13} textAnchor="middle">{names[index]}</text>
            </g>
          ))}
        </svg>
        {geographic && <StreetRouteMap candidates={candidates} settlements={settlements}
          recommendation={recommendation} opened={opened} onOpen={onOpen} />}
      </div>
      <div className="route-map-options" aria-label="Варианты на карте">
        {routes.map(({ candidate, index }) => (
          <button type="button" key={candidate.id} onClick={() => onOpen(candidate.id)}
            aria-pressed={opened === candidate.id}
            className={candidate.id === active ? "map-selected" : undefined}>
            <b><span className="route-option-number">{index + 1}</span>{candidateName(candidate)}</b>
            {candidate.id === recommendation && <small>Рекомендуем</small>}
            <span>{candidate.total_cost_rub == null && candidate.forecast_price?.air_median_rub != null
              ? <>{candidate.multimodal && candidate.known_cost_subtotal_rub != null
                  ? `${money(candidate.known_cost_subtotal_rub)} · учтённые этапы`
                  : `${money(candidate.forecast_price.air_median_rub)} · перелёт и авианакладная`}
                  {airForecastFlightHours(candidate) != null && <> · Перелёт около {hours(airForecastFlightHours(candidate))}</>}</>
              : candidate.issues.some(({ code }) => code === "unconfirmed_stop_continuity") && candidate.known_cost_subtotal_rub != null
                ? `${money(candidate.known_cost_subtotal_rub)} · ставки отдельных плеч`
                : <>{candidate.forecast_price
              ? forecastMoney(candidate.complete ? candidate.total_cost_rub : null, candidate.forecast_price.long_term)
              : money(candidate.complete ? candidate.total_cost_rub : null)} · {hours(candidate.max_delivery_hours)}</>}</span>
          </button>
        ))}
      </div>
      </div>
      {geographic && <small className="route-map-credit route-map-credit-online">
        © <a href="https://openmaptiles.org/">OpenMapTiles</a> · Данные ©{" "}
        <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>
      </small>}
      {land.length > 0 && <small className="route-map-credit route-map-credit-fallback">Подложка Natural Earth</small>}
    </section>
  );
}
