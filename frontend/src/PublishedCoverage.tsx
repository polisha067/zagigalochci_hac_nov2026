import { hours, modeNames, money, num } from "./model";
import type { Calculation } from "./networkApi";

const roadStatus: Record<string, string> = {
  router_unavailable: "Грузовой дорожный граф недоступен.",
  missing_coordinates: "Для одного из пунктов отсутствуют координаты.",
  path_not_found: "Грузовой путь не найден в подключённом графе.",
  ferry_required: "Найденный путь включает паром. Нужна проверка переправы.",
};

export function RoadMarketEstimate({ result }: { result: Calculation }) {
  const estimate = result.road_market_estimate;
  if (!estimate) return null;
  return (
    <section className="notice">
      <h2>Сценарная оценка автоплеча</h2>
      <p>{estimate.origin} → {estimate.destination} · {estimate.target_year} год ·{" "}
        <b>{money(estimate.forecast_road_rub)}</b>. НДС в источнике не указан.</p>
      <p>{estimate.trucks} маш. по {money(estimate.forecast_unit_rub)}. Расчёт по весу и объёму
        при равномерной раскладке, до {estimate.tonnes_per_truck} т и {estimate.assumed_m3_per_truck} м³ на машину.</p>
      <p className="table-note">
        База {estimate.base_year} года — {money(estimate.base_unit_rub)} за машину. Услуги сверх автоплеча
        требуют отдельной оценки. Перевозчик подтверждает раскладку, цену партии и наличие машин.
        {" "}<a href={estimate.tariff_source.url}>Прайс направления</a>
        {" · "}<a href={estimate.capacity_source.url}>Параметры фур</a>
      </p>
    </section>
  );
}

export function PublishedCoverage({ result, compact = false }: { result: Calculation; compact?: boolean }) {
  const coverage = result.published_coverage;
  if (!coverage) return null;
  const road = coverage.truck_path;
  const vat = result.request.cost_basis === "including_vat" ? "с НДС" : "без НДС";
  const rows = compact ? result.candidates.slice(0, 3) : result.candidates;
  return (
    <section className="notice">
      <h2>Опубликованные данные</h2>
      {road.status === "found" && road.distance_km != null && road.navigation_hours != null ? (
        <p>Путь по грузовому графу · {num(road.distance_km, 1)} км · {hours(road.navigation_hours)} в движении.
          {" "}<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap</a></p>
      ) : roadStatus[road.status] && <p>{roadStatus[road.status]}</p>}
      {!rows.length && <p>Для выбранных условий опубликованная ставка в каталоге не найдена. Запросите котировку у перевозчика.</p>}
      {coverage.terminals?.map((terminal) => (
        <p key={terminal.id + terminal.operation}>
          {terminal.operation === "pickup" ? "Подвоз" : "Довоз"} · {terminal.name}, ст. {terminal.station}.{" "}
          {terminal.path.status === "found" && terminal.path.distance_km != null
            ? `${num(terminal.path.distance_km, 1)} км. Нужна котировка.`
            : roadStatus[terminal.path.status]}
        </p>
      ))}
      {rows.map((candidate) => {
        const rate = candidate.published_rate;
        if (!rate) return null;
        const tax = rate.vat === "included" ? "с НДС" : "НДС не указан";
        return (
          <div className="source-item" key={candidate.id}>
            <b>{candidate.route_hubs.map((hub) => hub.name).join(" → ")} · {candidate.modes.map((mode) => modeNames[mode]).join(" → ")}</b>
            <p>{rate.equipment} · {money(rate.published_amount_rub)} за единицу ({tax}).{" "}
              <a href={rate.source_url} target="_blank" rel="noreferrer">Источник · сверено {rate.observed_on}</a>
              {rate.valid_from ? ` · опубликовано начало действия ${rate.valid_from}` : " · период действия не указан"}</p>
            {rate.forecast_amount_rub != null && <p>Прогноз по опубликованной базе на {rate.forecast_year} · {money(rate.forecast_amount_rub)} за единицу ({tax})</p>}
            {candidate.known_cost_subtotal_rub != null && <p>Частичная смета · {money(candidate.known_cost_subtotal_rub)} {vat}</p>}
            {rate.distance_km != null && <p>Расстояние из прайса · {num(rate.distance_km, 1)} км</p>}
            {rate.transit_days?.length ? <p>Ориентир из прайса · {rate.transit_days.join("–")} сут.</p> : null}
            {rate.security_rub != null && <p>Отдельно по прайсу · охрана {money(rate.security_rub)} за контейнер</p>}
            {!compact && rate.separate_services?.map((service) => (
              <p className="table-note" key={service.name}>
                {service.name} · от {money(service.from_rub)}{service.unit === "day" ? " в сутки" : ""}. Уточните применимость.
              </p>
            ))}
            {candidate.issues.map((issue) => <p className="table-note" key={issue.code}>{issue.message}</p>)}
          </div>
        );
      })}
      {!compact && coverage.other_tariffs.length > 0 && (
        <details>
          <summary>Справочные прайсы на этом направлении</summary>
          {coverage.other_tariffs.map((row, index) => (
            <p key={index}>{row.origin} → {row.destination}.{" "}
              {row.from_rub_per_kg != null && `От ${money(row.from_rub_per_kg)}/кг. `}
              {row.from_rub_per_m3 != null && `От ${money(row.from_rub_per_m3)}/м³. `}
              {row.amount_rub != null && `${row.equipment} · ${money(row.amount_rub)}. Состав плеч уточняется. `}
              <a href={row.source_url} target="_blank" rel="noreferrer">Условия перевозчика</a></p>
          ))}
        </details>
      )}
      <details>
        <summary>Фактические объёмы перевозок</summary>
        <p>В каталоге нет ряда объёмов по этому направлению. Ниже показана статистика Росстата по России.</p>
        {coverage.volume.observations.map((row) => (
          <p key={`${row.year}:${row.mode}:${row.indicator}`}>
            {row.year} · {row.geography} · {modeNames[row.mode]} · {num(row.value, 1)}
            {" "}{row.unit === "million_tonnes" ? "млн т" : "млрд т·км"}
          </p>
        ))}
        <a href="https://rosstat.gov.ru/storage/mediabank/Rossia_2025.pdf">Росстат, «Россия 2025», стр. 78</a>
      </details>
    </section>
  );
}
