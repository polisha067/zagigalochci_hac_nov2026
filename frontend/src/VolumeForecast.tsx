import { useEffect, useState } from "react";
import { errorText, request } from "./api";
import { num } from "./model";

interface VolumeOptions {
  routes: { route_id: string; origin: string; destination: string }[];
  min_year: number;
  max_year: number;
  last_observed_year: number;
  volume_status: string;
}

interface VolumeResult {
  origin: string;
  destination: string;
  start_year: number;
  end_year: number;
  last_observed_year: number;
  volume_status: string;
  forecast_tonnes: number;
  periods: { period: string; forecast_tonnes: number }[];
}

export function VolumeForecast({ origin, destination, targetYear }: {
  origin?: string;
  destination?: string;
  targetYear?: number;
}) {
  const [options, setOptions] = useState<VolumeOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selectedRoute, setSelectedRoute] = useState<string | null>(null);
  const [year, setYear] = useState(String(targetYear ?? 2030));
  const [granularity, setGranularity] = useState("month");
  const [response, setResponse] = useState<{
    key: string; data?: VolumeResult; error?: string;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setOptionsError("");
    request<VolumeOptions>("/network/forecast/options", { signal: controller.signal })
      .then(setOptions)
      .catch((error) => {
        if (!controller.signal.aborted) setOptionsError(errorText(error));
      });
    return () => controller.abort();
  }, [attempt]);

  const matched = options?.routes.find((route) =>
    route.origin === origin && route.destination === destination);
  const routeId = selectedRoute ?? matched?.route_id ?? "";
  const route = options?.routes.find((item) => item.route_id === routeId);
  const validYear = options && Number.isInteger(Number(year)) &&
    Number(year) >= options.min_year && Number(year) <= options.max_year;
  const query = route && validYear
    ? new URLSearchParams({
      route_id: route.route_id, start_year: year, end_year: year, granularity,
    }).toString() : "";

  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    request<VolumeResult>(`/network/forecast?${query}`, { signal: controller.signal })
      .then((data) => setResponse({ key: query, data }))
      .catch((error) => {
        if (!controller.signal.aborted) setResponse({ key: query, error: errorText(error) });
      });
    return () => controller.abort();
  }, [query, attempt]);

  const data = response?.key === query ? response.data : undefined;
  const error = response?.key === query ? response.error : undefined;
  const peak = data?.periods.reduce((best, period) =>
    period.forecast_tonnes > best.forecast_tonnes ? period : best);
  const maximum = Math.max(1, ...data?.periods.map((period) => period.forecast_tonnes) ?? []);
  const points = data?.periods.map((period, index) => ({
    ...period,
    x: data.periods.length === 1 ? 350 : 65 + index * 570 / (data.periods.length - 1),
    y: 220 - period.forecast_tonnes / maximum * 150,
  })) ?? [];
  const path = points.map((point, index) =>
    `${index ? "L" : "M"}${point.x} ${point.y}`).join(" ");

  return (
    <div className="volume-forecast">
      <div className="forecast-overview-heading">
        <span className="forecast-eyebrow">Прогноз грузопотока</span>
        <h2>{route ? `${route.origin} → ${route.destination}` : "Как изменится объём перевозок"}</h2>
        <p>Годовой объём и его распределение по периодам.</p>
      </div>
      {optionsError ? <div className="notice warning" role="alert">
        <p>{optionsError}</p>
        <button className="text-button" onClick={() => setAttempt((value) => value + 1)}>Повторить загрузку</button>
      </div> : !options ? <p role="status">Загружаем направления прогноза…</p> : (
        <>
          {!matched && origin && destination && <p className="notice volume-coverage">
            Для направления {origin} → {destination} нет исторического ряда объёмов.
            Выберите одно из {options.routes.length} доступных направлений.
          </p>}
          <div className="volume-controls">
            <label>Направление прогноза объёма
              <select value={routeId} onChange={(e) => setSelectedRoute(e.target.value)}>
                <option value="">Выберите направление</option>
                {options.routes.map((item) => <option key={item.route_id} value={item.route_id}>
                  {item.origin} → {item.destination}
                </option>)}
              </select>
            </label>
            <label>Год прогноза объёма
              <input type="number" min={options.min_year} max={options.max_year}
                value={year} onChange={(e) => setYear(e.target.value)} />
            </label>
            <label>Детализация
              <select value={granularity} onChange={(e) => setGranularity(e.target.value)}>
                <option value="month">По месяцам</option>
                <option value="quarter">По кварталам</option>
                <option value="year">За год</option>
              </select>
            </label>
          </div>
          {!validYear && <p role="alert">Укажите целый год от {options.min_year} до {options.max_year}.</p>}
          {query && !data && !error && <p role="status">Рассчитываем объём…</p>}
          {error && <div className="notice warning" role="alert">
            <p>{error}</p>
            <button className="text-button" onClick={() => setAttempt((value) => value + 1)}>Повторить расчёт</button>
          </div>}
          {data && <>
            <div className="forecast-metrics" aria-live="polite">
              <div><span>Объём за {data.start_year} год</span><strong>{num(data.forecast_tonnes, 1)} т</strong></div>
              <div className="forecast-metric-primary">
                <span>Максимум за период</span><strong>{num(peak?.forecast_tonnes, 1)} т</strong>
                <small>{peak?.period}</small>
              </div>
              <div><span>Периодов в прогнозе</span><strong>{data.periods.length}</strong></div>
            </div>
            <div className="forecast-chart-card">
              <h3>Объём перевозок · {data.start_year}</h3>
              <svg className="forecast-chart" viewBox="0 0 700 285" role="img"
                aria-label={`Прогноз объёма по периодам, в тоннах. Всего ${num(data.forecast_tonnes, 1)} т.`}>
                {[70, 120, 170, 220].map((y) => <line key={y} x1="65" x2="635" y1={y} y2={y} className="forecast-grid-line" />)}
                <text x="65" y="35">{num(maximum, 1)} т</text>
                <path d={path} fill="none" stroke="#006cf5" strokeWidth="4" />
                {points.map((point, index) => <g key={point.period}>
                  <circle cx={point.x} cy={point.y} r="5" fill="#006cf5">
                    <title>{point.period} · {num(point.forecast_tonnes, 1)} т</title>
                  </circle>
                  {(index === 0 || index === points.length - 1) && (
                    <text x={point.x} y="260" textAnchor="middle">{point.period}</text>
                  )}
                </g>)}
              </svg>
              <details className="volume-periods">
                <summary>Посмотреть значения по периодам</summary>
                <div className="table-scroll"><table className="detail-table">
                  <thead><tr><th>Период</th><th>Прогноз, т</th></tr></thead>
                  <tbody>{data.periods.map((period) => <tr key={period.period}>
                    <th>{period.period}</th><td>{num(period.forecast_tonnes, 1)}</td>
                  </tr>)}</tbody>
                </table></div>
              </details>
            </div>
            <p className="forecast-chart-note">
              {data.volume_status === "synthetic" ? "История объёмов синтетическая. " : ""}
              Модель учитывает линейный тренд и месячную сезонность.
              Последний год истории — {data.last_observed_year}.
              Ряд описывает условного грузоотправителя на направлении, без детализации по выбранному товару и остановкам.
            </p>
          </>}
          {!routeId && <div className="forecast-chart-empty"><span aria-hidden="true">↗</span>
            <p>Выберите направление, чтобы увидеть прогноз в тоннах.</p>
          </div>}
        </>
      )}
    </div>
  );
}
