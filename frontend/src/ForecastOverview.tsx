import { useId, useState } from "react";
import { VolumeForecast } from "./VolumeForecast";
import type { Calculation } from "./networkApi";
import { candidateName, forecastMoney, hours, money, num, snapshotRoute, type Draft, type NetworkSnapshot } from "./model";

export function ForecastOverview({ snapshot, draft }: { snapshot?: NetworkSnapshot; draft?: Draft }) {
  const [view, setView] = useState<"cost" | "volume">("cost");
  const [volumeOpened, setVolumeOpened] = useState(false);
  const panelId = useId();
  const conditions = snapshot?.draft ?? draft;
  const result: Calculation | undefined = snapshot?.result;
  const forecast = result?.forecast;
  const candidate = result?.candidates.find((item) => item.id === result.recommendation);
  const price = candidate?.complete && candidate.status === "feasible"
    ? candidate.total_cost_rub : null;
  const budget = candidate?.forecast_price;
  const base = budget?.base_rub;
  const comparable = forecast && price != null && base != null && base > 0;
  const change = comparable ? (price / base - 1) * 100 : null;
  const baseYear = budget?.base_year ?? forecast?.base_year;
  const hasRange = price != null && budget?.lower_rub != null && budget.upper_rub != null;
  const baseY = comparable ? 220 - (base / Math.max(base, price)) * 150 : 170;
  const targetY = comparable ? 220 - (price / Math.max(base, price)) * 150 : 100;

  return (
    <section className="forecast-overview" aria-label="Прогноз">
      <div className="forecast-view-switch" role="group" aria-label="Показатель прогноза">
        <button type="button" aria-pressed={view === "cost"} aria-controls={panelId}
          onClick={() => setView("cost")}>Стоимость</button>
        <button type="button" aria-pressed={view === "volume"} aria-controls={panelId}
          onClick={() => { setVolumeOpened(true); setView("volume"); }}>Объём</button>
      </div>
      <div id={panelId}>
      {volumeOpened && <div hidden={view !== "volume"}>
        <VolumeForecast
          key={`${conditions?.origin?.name}|${conditions?.destination?.name}|${forecast?.target_year ?? conditions?.forecastYear}`}
          origin={conditions?.origin?.name} destination={conditions?.destination?.name}
          targetYear={forecast?.target_year ?? (Number(conditions?.forecastYear) || undefined)}
        />
      </div>}
      <div hidden={view !== "cost"}>
      <div className="forecast-overview-heading">
        <span className="forecast-eyebrow">{snapshot ? "Результат по заданным условиям" : "Бюджет перевозки"}</span>
        <h2>{snapshot ? snapshotRoute(snapshot) : "Спланируйте расходы заранее"}</h2>
        <p>{forecast ? `Оценка в ценах ${forecast.target_year} года` : snapshot ? "Расчёт по заданной дате и условиям" : "Выберите год, направление и груз. Здесь появятся стоимость и сравнение с базовым годом."}</p>
      </div>
      <div className="forecast-metrics" aria-live="polite">
        <div>
          <span>{forecast ? `База · ${baseYear}` : "Базовая стоимость"}</span>
          <strong>{forecast && price != null ? money(base) : "—"}</strong>
        </div>
        <div className="forecast-metric-primary">
          <span>{forecast ? `Оценка · ${forecast.target_year}` : "Стоимость перевозки"}</span>
          <strong>{price != null ? forecastMoney(price, budget?.long_term) : "—"}</strong>
        </div>
        <div>
          <span>Изменение к базе</span>
          <strong>{change == null ? "—" : `${change > 0 ? "+" : ""}${num(change, 1)}%`}</strong>
        </div>
      </div>
      <div className="forecast-chart-card">
        <h3>Как изменится стоимость</h3>
        {comparable ? (
          <svg className="forecast-chart" viewBox="0 0 700 285" role="img"
            aria-label={`Базовая стоимость ${money(base)} в ${baseYear} году. Оценка ${money(price)} на ${forecast.target_year} год.`}>
            {[70, 120, 170, 220].map((y) => <line key={y} x1="65" x2="635" y1={y} y2={y} className="forecast-grid-line" />)}
            <path d={`M110 ${baseY} L590 ${targetY} L590 220 L110 220 Z`} fill="#006cf512" />
            <path d={`M110 ${baseY} L590 ${targetY}`} stroke="#006cf5" strokeWidth="4" fill="none" />
            <circle cx="110" cy={baseY} r="7" fill="#7389a8" />
            <circle cx="590" cy={targetY} r="8" fill="#006cf5" />
            <text x="110" y={baseY - 20} textAnchor="middle">{money(base)}</text>
            <text x="590" y={targetY - 20} textAnchor="middle">{forecastMoney(price, budget?.long_term)}</text>
            <text x="110" y="260" textAnchor="middle">{baseYear}</text>
            <text x="590" y="260" textAnchor="middle">{forecast.target_year}</text>
          </svg>
        ) : (
          <div className="forecast-chart-empty">
            <span aria-hidden="true">↗</span>
            <p>{snapshot ? "Для сравнения по годам нужен прогноз с полной оценкой и сопоставимой базовой стоимостью." : "Сначала задайте условия прогноза"}</p>
          </div>
        )}
        <div className="forecast-range">
          <div><span>Диапазон оценки</span><b>{hasRange ? `${forecastMoney(budget.lower_rub, budget.long_term)} – ${forecastMoney(budget.upper_rub, budget.long_term)}` : "Нет рассчитанного диапазона"}</b></div>
          <span>{budget?.long_term ? "Долгосрочный сценарий" : "Сценарная оценка"}</span>
        </div>
        {comparable && <p className="forecast-chart-note">Сравнение базы и выбранного года. Промежуточные годы на графике не рассчитываются.</p>}
      </div>
      <div className="forecast-recommendation">
        <span className="forecast-recommendation-icon" aria-hidden="true">↗</span>
        <div>
          <span>{candidate ? "Рекомендуемый вариант" : "Выбор схемы доставки"}</span>
          <h3>{candidate ? candidateName(candidate) : "Сравним доступные варианты"}</h3>
          <p>{candidate
            ? `Минимальная полная оценка среди найденных допустимых вариантов · ${hours(candidate.max_delivery_hours)}`
            : snapshot ? "Подходящий вариант для рекомендации не найден. Посмотрите ограничения ниже."
              : "Стоимость, сроки и подробные начисления появятся после расчёта."}</p>
        </div>
      </div>
      </div>
      </div>
    </section>
  );
}
