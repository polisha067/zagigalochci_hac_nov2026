import { ForecastChart } from "./ForecastChart";
import { Select } from "./Select";
import { useEffect, useState } from "react";
import { errorText, request } from "./api";
import type { VolumeForecastSelection } from "./assistantApi";
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

export function VolumeForecast({
  origin,
  destination,
  targetYear,
  selection,
}: {
  origin?: string;
  destination?: string;
  targetYear?: number;
  selection?: VolumeForecastSelection;
}) {
  const [options, setOptions] = useState<VolumeOptions | null>(null);
  const [optionsError, setOptionsError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selectedRoute, setSelectedRoute] = useState<string | null>(selection?.routeId ?? null);
  const [startYear, setStartYear] = useState(selection?.startYear ?? targetYear ?? 2030);
  const [endYear, setEndYear] = useState(selection?.endYear ?? targetYear ?? 2030);
  const isRange = startYear !== endYear;
  const [singleYearGranularity, setSingleYearGranularity] = useState<string>(selection?.granularity ?? "month");
  const [rangeGranularity, setRangeGranularity] = useState<string>(selection?.granularity ?? "year");
  const granularity = isRange ? rangeGranularity : singleYearGranularity;
  const compareYears = isRange && granularity === "year";
  const peakLabel = { year: "Максимум за год", quarter: "Максимум за квартал", month: "Максимум за месяц" }[granularity];
  const [response, setResponse] = useState<{
    key: string;
    data?: VolumeResult;
    error?: string;
  } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setOptionsError("");
    request<VolumeOptions>("/network/forecast/options", {
      signal: controller.signal,
    })
      .then(setOptions)
      .catch((error) => {
        if (!controller.signal.aborted) setOptionsError(errorText(error));
      });
    return () => controller.abort();
  }, [attempt]);

  const matched = options?.routes.find(
    (route) => route.origin === origin && route.destination === destination,
  );
  const routeId = selectedRoute ?? matched?.route_id ?? "";
  const route = options?.routes.find((item) => item.route_id === routeId);
  const years = options
    ? Array.from({ length: options.max_year - options.min_year + 1 }, (_, i) => options.min_year + i)
    : [];
  const validRange = options && startYear >= options.min_year &&
    endYear <= options.max_year && startYear <= endYear;
  const query =
    route && validRange
      ? new URLSearchParams({
          route_id: route.route_id,
          start_year: String(startYear),
          end_year: String(endYear),
          granularity,
        }).toString()
      : "";

  useEffect(() => {
    if (!query) return;
    const controller = new AbortController();
    request<VolumeResult>(`/network/forecast?${query}`, {
      signal: controller.signal,
    })
      .then((data) => setResponse({ key: query, data }))
      .catch((error) => {
        if (!controller.signal.aborted)
          setResponse({ key: query, error: errorText(error) });
      });
    return () => controller.abort();
  }, [query, attempt]);

  const data = response?.key === query ? response.data : undefined;
  const error = response?.key === query ? response.error : undefined;
  const peak = data?.periods.length
    ? data.periods.reduce((best, period) =>
        period.forecast_tonnes > best.forecast_tonnes ? period : best,
      )
    : undefined;
  const periodName = (period: string) => {
    const month = /^(\d{4})-(\d{2})$/.exec(period);
    if (month) {
      const name = new Intl.DateTimeFormat("ru-RU", { month: "short", timeZone: "UTC" })
        .format(new Date(Date.UTC(Number(month[1]), Number(month[2]) - 1, 1)))
        .replace(".", "");
      return isRange ? `${name} ${month[1]}` : name;
    }
    const quarter = /^(\d{4})-Q([1-4])$/.exec(period);
    return quarter ? (isRange ? `${quarter[2]} кв. ${quarter[1]}` : `${quarter[2]} кв.`) : period;
  };
  const points =
    data?.periods.map((period) => ({
      label: periodName(period.period),
      value: period.forecast_tonnes,
    })) ?? [];
  const resultPeriod = data && (isRange ? `${data.start_year}–${data.end_year}` : String(data.start_year));

  return (
    <div className="volume-forecast">
      <div className="forecast-overview-heading">
        <span className="forecast-eyebrow">Прогноз грузопотока</span>
        <h2>
          {route
            ? `${route.origin} → ${route.destination}`
            : "Как изменится объём перевозок"}
        </h2>
        <p>Выберите один год или сравните объёмы за несколько лет.</p>
      </div>
      {optionsError ? (
        <div className="notice warning" role="alert">
          <p>{optionsError}</p>
          <button
            className="text-button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Повторить загрузку
          </button>
        </div>
      ) : !options ? (
        <p role="status">Загружаем направления прогноза…</p>
      ) : (
        <>
          {!matched && origin && destination && (
            <p className="notice volume-coverage">
              Для направления {origin} → {destination} нет исторического ряда
              объёмов. Выберите одно из {options.routes.length} доступных
              направлений.
            </p>
          )}
          <div className="volume-controls">
            <label>
              Направление прогноза объёма
              <Select
                value={routeId}
                onChange={(e) => setSelectedRoute(e.target.value)}
              >
                <option value="">Выберите направление</option>
                {options.routes.map((item) => (
                  <option key={item.route_id} value={item.route_id}>
                    {item.origin} → {item.destination}
                  </option>
                ))}
              </Select>
            </label>
            <label>
              С года
              <Select
                value={startYear}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  setStartYear(next);
                  setEndYear((previous) => isRange ? Math.max(next, previous) : next);
                }}
              >
                {years.map((year) => <option key={year} value={year}>{year}</option>)}
              </Select>
            </label>
            <label>
              По год
              <Select value={endYear} onChange={(event) => setEndYear(Number(event.target.value))}>
                {years.filter((year) => year >= startYear)
                  .map((year) => <option key={year} value={year}>{year}</option>)}
              </Select>
            </label>
            <label>
              Детализация
              <Select
                value={granularity}
                onChange={(event) => {
                  const setGranularity = isRange ? setRangeGranularity : setSingleYearGranularity;
                  setGranularity(event.target.value);
                }}
              >
                <option value="month">По месяцам</option>
                <option value="quarter">По кварталам</option>
                <option value="year">{isRange ? "По годам" : "За год"}</option>
              </Select>
            </label>
          </div>
          {query && !data && !error && <p role="status">Рассчитываем объём…</p>}
          {error && (
            <div className="notice warning" role="alert">
              <p>{error}</p>
              <button
                className="text-button"
                onClick={() => setAttempt((value) => value + 1)}
              >
                Повторить расчёт
              </button>
            </div>
          )}
          {data && (
            <>
              <div className="forecast-metrics" aria-live="polite">
                <div>
                  <span>Объём за {resultPeriod}</span>
                  <strong>{num(data.forecast_tonnes, 1)} т</strong>
                </div>
                <div className="forecast-metric-primary">
                  <span>{peakLabel}</span>
                  <strong>{num(peak?.forecast_tonnes, 1)} т</strong>
                  <small>{peak && periodName(peak.period)}</small>
                </div>
                <div>
                  <span>Периодов в прогнозе</span>
                  <strong>{data.periods.length}</strong>
                </div>
              </div>
              <div className="forecast-chart-card">
                <h3>Объём перевозок · {resultPeriod}</h3>
                <ForecastChart
                  unit="т"
                  points={points}
                  variant={points.length === 1 ? "bar" : "line"}
                  label={`Объём перевозок за ${resultPeriod}. Всего ${num(data.forecast_tonnes, 1)} т.`}
                />
                <p className="forecast-chart-note">
                  Наведите на точку графика, чтобы увидеть объём.
                </p>
                <details className="volume-periods" open={compareYears}>
                  <summary>{compareYears ? "Значения по годам" : "Значения по периодам"}</summary>
                  <div className="table-scroll">
                    <table className="detail-table">
                      <thead>
                        <tr>
                          <th>Период</th>
                          <th>Прогноз, т</th>
                          {compareYears && <th>К предыдущему году</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {data.periods.map((period, index) => {
                          const previous = data.periods[index - 1]?.forecast_tonnes;
                          const change = previous > 0
                            ? (period.forecast_tonnes - previous) / previous * 100
                            : null;
                          return (
                            <tr key={period.period}>
                              <th scope="row">{isRange ? periodName(period.period) : period.period}</th>
                              <td>{num(period.forecast_tonnes, 1)}</td>
                              {compareYears && (
                                <td>{change == null ? "—" : `${change > 0 ? "+" : ""}${num(change, 1)}%`}</td>
                              )}
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </details>
              </div>
              <p className="forecast-chart-note">
                {data.volume_status === "synthetic"
                  ? "История объёмов синтетическая. "
                  : ""}
                Модель учитывает линейный тренд и месячную сезонность. Последний
                год истории — {data.last_observed_year}. Ряд описывает условного
                грузоотправителя на направлении, без детализации по выбранному
                товару и остановкам.
              </p>
            </>
          )}
          {!routeId && (
            <div className="forecast-chart-empty">
              <span aria-hidden="true">↗</span>
              <p>Выберите направление, чтобы увидеть прогноз в тоннах.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
