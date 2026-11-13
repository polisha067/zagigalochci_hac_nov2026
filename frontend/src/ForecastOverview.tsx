import { ForecastChart } from "./ForecastChart";
import { useAnnualPrices } from "./useAnnualPrices";
import { useId, useState } from "react";
import { VolumeForecast } from "./VolumeForecast";
import type { VolumeForecastSelection } from "./assistantApi";
import type { Calculation } from "./networkApi";
import {
  candidateName,
  hours,
  money,
  num,
  snapshotRoute,
  type Draft,
  type NetworkSnapshot,
} from "./model";

export function ForecastOverview({
  snapshot,
  draft,
  volumeSelection,
  compact = false,
}: {
  volumeSelection?: VolumeForecastSelection;
  compact?: boolean;
  snapshot?: NetworkSnapshot;
  draft?: Draft;
}) {
  const [view, setView] = useState<"cost" | "volume">(volumeSelection ? "volume" : "cost");
  const [volumeOpened, setVolumeOpened] = useState(Boolean(volumeSelection));
  const panelId = useId();
  const conditions = snapshot?.draft ?? draft;
  const result: Calculation | undefined = snapshot?.result;
  const forecast = result?.forecast;
  const candidate = result?.candidates.find(
    (item) => item.id === result.recommendation,
  );
  const air = result?.air_market?.routes[0];
  const land = result?.land_market?.routes.find((route) => route.movement_rub != null);
  const road = result?.road_market_estimate;
  const airBaseYear = air?.legs[0]?.base_year;
  const landBaseYear = land?.legs[0]?.base_year;
  const fallback = air ? {
    price: air.forecast_rub, label: "Авиаперевозка между аэропортами",
    year: airBaseYear, base: air.legs.every((leg) => leg.base_year === airBaseYear)
      ? air.legs.reduce((sum, leg) => sum + leg.base_rub, 0) : null,
  } : land ? {
    price: land.movement_rub, label: result?.land_market?.version.startsWith("reefer-market")
      ? "Оценка перевозки рефрижераторами" : "Оценка по учтённым транспортным плечам",
    year: landBaseYear, base: land.legs.every((leg) => leg.base_year === landBaseYear)
      ? land.legs.reduce((sum, leg) => sum + leg.base_unit_rub * leg.units, 0) : null,
  } : road ? {
    price: road.forecast_road_rub, label: "Оценка основного автоплеча",
    year: road.base_year, base: road.base_unit_rub * road.trucks,
  } : undefined;
  const complete = candidate?.complete && candidate.status === "feasible";
  const displayedEstimate = complete ? undefined : fallback;
  const price =
    complete
      ? candidate.total_cost_rub
      : displayedEstimate?.price;
  const budget = complete ? candidate.forecast_price : undefined;
  const annual = useAnnualPrices(candidate, forecast);
  const base = budget?.base_rub ?? displayedEstimate?.base;
  const comparable = forecast && price != null && base != null && base > 0;
  const change = comparable ? (price / base - 1) * 100 : null;
  const baseYear = budget?.base_year ?? displayedEstimate?.year ?? forecast?.base_year;
  const hasRange =
    price != null && budget?.lower_rub != null && budget.upper_rub != null;
  const charges =
    candidate?.cost_items.reduce<Record<string, number>>((groups, item) => {
      if (item.amount_rub != null)
        groups[item.kind] = (groups[item.kind] || 0) + item.amount_rub;
      return groups;
    }, {}) ?? {};
  const chargeLabels: Record<string, string> = {
    transport: "Перевозка",
    pickup_handling: "Приём и погрузка",
    delivery_handling: "Выгрузка и выдача",
    transfer: "Перегрузка",
    transfer_handling: "Перегрузка",
    origin_storage: "Хранение перед отправкой",
    terminal_storage: "Хранение на терминале",
    waiting_storage: "Хранение при ожидании",
    destination_storage: "Хранение в пункте назначения",
    accumulation_storage: "Хранение при накоплении",
    mandatory_stop: "Пребывание на остановке",
    storage: "Хранение",
    handling: "Обработка",
  };
  const chargeSum = Object.values(charges).reduce(
    (sum, value) => sum + value,
    0,
  );
  const reconciled = complete && price != null && Math.abs(chargeSum - price) < 0.011;

  return (
    <section className={`forecast-overview${compact ? " forecast-overview-compact" : ""}`} aria-label="Прогноз">
      <div
        className="forecast-view-switch"
        role="group"
        aria-label="Показатель прогноза"
      >
        <button
          type="button"
          aria-pressed={view === "cost"}
          aria-controls={panelId}
          onClick={() => setView("cost")}
        >
          Стоимость
        </button>
        <button
          type="button"
          aria-pressed={view === "volume"}
          aria-controls={panelId}
          onClick={() => {
            setVolumeOpened(true);
            setView("volume");
          }}
        >
          Объём
        </button>
      </div>
      <div id={panelId}>
        {volumeOpened && (
          <div hidden={view !== "volume"}>
            <VolumeForecast
              key={`${conditions?.origin?.name}|${conditions?.destination?.name}|${forecast?.target_year ?? conditions?.forecastYear}`}
              selection={volumeSelection}
              origin={conditions?.origin?.name}
              destination={conditions?.destination?.name}
              targetYear={
                forecast?.target_year ??
                (Number(conditions?.forecastYear) || undefined)
              }
            />
          </div>
        )}
        <div hidden={view !== "cost"}>
          <div className="forecast-overview-heading">
            <span className="forecast-eyebrow">
              {snapshot ? "Результат по заданным условиям" : "Бюджет перевозки"}
            </span>
            <h2>
              {snapshot
                ? snapshotRoute(snapshot)
                : "Спланируйте расходы заранее"}
            </h2>
            <p>
              {forecast
                ? `Оценка в ценах ${forecast.target_year} года`
                : snapshot
                  ? "Расчёт по заданной дате и условиям"
                  : "Выберите год, направление и груз. Здесь появятся стоимость и сравнение с базовым годом."}
            </p>
            {displayedEstimate && <p>{displayedEstimate.label}. Состав цены и условия указаны в вариантах перевозки.</p>}
          </div>
          <div className="forecast-metrics" aria-live="polite">
            <div>
              <span>
                {forecast ? `База · ${baseYear}` : "Базовая стоимость"}
              </span>
              <strong>{forecast && price != null ? money(base) : "—"}</strong>
            </div>
            <div className="forecast-metric-primary">
              <span>
                {forecast
                  ? `Оценка · ${forecast.target_year}`
                  : "Стоимость перевозки"}
                {candidate && price != null && <> · {candidateName(candidate)}</>}
              </span>
              <strong>{price != null ? money(price) : "—"}</strong>
            </div>
            <div>
              <span>Изменение к базе</span>
              <strong>
                {change == null
                  ? "—"
                  : `${change > 0 ? "+" : ""}${num(change, 1)}%`}
              </strong>
            </div>
          </div>
          <div className="forecast-chart-card">
            <h3>Как изменится стоимость</h3>
            {annual.loading ? <p role="status">Восстанавливаем прогноз по всем годам…</p> : comparable ? (
              <ForecastChart
                variant="bar"
                compact={compact}
                unit="₽"
                label={`Базовая стоимость и прогноз на ${forecast.target_year} год, с диапазоном оценки`}
                points={annual.series?.length
                  ? annual.series.map((point) => ({
                      label: String(point.year), value: point.amount_rub,
                      lower: point.year === baseYear ? null : point.lower_rub,
                      upper: point.year === baseYear ? null : point.upper_rub,
                    }))
                  : [
                      { label: String(baseYear), value: base },
                      { label: String(forecast.target_year), value: price,
                        lower: budget?.lower_rub, upper: budget?.upper_rub },
                    ]}
              />
            ) : (
              <div className="forecast-chart-empty">
                <span aria-hidden="true">↗</span>
                <p>
                  {snapshot
                    ? "Для сравнения по годам нужен прогноз с полной оценкой и сопоставимой базовой стоимостью."
                    : "Сначала задайте условия прогноза"}
                </p>
              </div>
            )}
            {comparable && <div className="cost-chart-legend">
              <span><i className="legend-base" />Базовая стоимость</span>
              <span><i className="legend-forecast" />Прогноз по годам</span>
              <span><i className="legend-range" />Диапазон оценки</span>
            </div>}
            <div className="forecast-range">
              <div>
                <span>Диапазон оценки</span>
                <b>
                  {hasRange
                    ? `${money(budget.lower_rub)} – ${money(budget.upper_rub)}`
                    : "Нет рассчитанного диапазона"}
                </b>
              </div>
              <span>
                {budget?.long_term
                  ? "Долгосрочный сценарий"
                  : "Сценарная оценка"}
              </span>
            </div>
            {comparable && <details className="forecast-chart-method" open={!compact}>
              <summary>Как рассчитан прогноз</summary>
              <p className="forecast-chart-note">
                Одна схема доставки и одинаковый объём груза. Для каждого года
                стоимость пересчитана по индексам модели.
                Вертикальная отметка показывает диапазон оценки.
                {annual.series?.length
                  ? " Наведите на столбец или нажмите на него, чтобы увидеть точную сумму."
                  : displayedEstimate ? "" : " В этом сохранённом расчёте есть только крайние годы. Выполните прогноз заново, чтобы получить полный ряд."}
              </p>
            </details>}
            {annual.error && <p role="alert" className="forecast-chart-note">Не удалось восстановить промежуточные годы: {annual.error}</p>}
            {annual.series && (
              <details className="volume-periods cost-annual-values">
                <summary>Все значения по годам</summary>
                <div className="table-scroll">
                  <table className="detail-table">
                    <thead><tr><th>Год</th><th>Стоимость</th><th>Диапазон оценки</th></tr></thead>
                    <tbody>{annual.series.map((point) => <tr key={point.year}>
                      <th>{point.year}{point.year === baseYear ? " · база" : ""}</th>
                      <td>{money(point.amount_rub)}</td>
                      <td>{point.year === baseYear ? "—"
                        : point.lower_rub != null && point.upper_rub != null
                          ? `${money(point.lower_rub)} – ${money(point.upper_rub)}`
                          : "Нет рассчитанного диапазона"}</td>
                    </tr>)}</tbody>
                  </table>
                </div>
              </details>
            )}
          </div>
          {price != null && reconciled && (
            <details className="cost-composition" open={!compact}>
              <summary>Из чего складывается оценка</summary>
              {Object.entries(charges).map(([kind, amount]) => (
                <div className="cost-charge" key={kind}>
                  <div>
                    <span>{chargeLabels[kind] || kind}</span>
                    <b>{money(amount)}</b>
                  </div>
                  <div className="cost-charge-track">
                    <span
                      style={{
                        width: `${price > 0 ? (amount / price) * 100 : 0}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
              <div className="cost-charge-total">
                <span>Итого по начислениям</span>
                <b>{money(chargeSum)}</b>
              </div>
              <p className="forecast-chart-note">
                Сумма компонентов совпадает с полной стоимостью. Изменение к
                базе = (оценка / база − 1) × 100%.
              </p>
            </details>
          )}
          {!compact && <div className="forecast-recommendation">
            <span className="forecast-recommendation-icon" aria-hidden="true">
              ↗
            </span>
            <div>
              <span>
                {candidate ? "Рекомендуемый вариант" : "Выбор схемы доставки"}
              </span>
              <h3>
                {candidate
                  ? candidateName(candidate)
                  : "Сравним доступные варианты"}
              </h3>
              <p>
                {candidate
                  ? `Минимальная полная оценка среди найденных допустимых вариантов · ${hours(candidate.max_delivery_hours)}`
                  : snapshot
                    ? "Подходящий вариант для рекомендации не найден. Посмотрите ограничения ниже."
                    : "Стоимость, сроки и подробные начисления появятся после расчёта."}
              </p>
            </div>
          </div>}
        </div>
      </div>
    </section>
  );
}
