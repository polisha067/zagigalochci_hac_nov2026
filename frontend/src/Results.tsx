import { Select } from "./Select";
import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { RouteMap, type MapRoute } from "./RouteMap";
import { ConnectionPilot } from "./ConnectionPilot";
import { ForecastOverview } from "./ForecastOverview";
import { AirMarketOptions, LandMarketOptions, PublishedCoverage, RoadMarketEstimate } from "./PublishedCoverage";
import { Results as LegacyResults } from "./LegacyResults";
import type { Calculation, Candidate, Source } from "./networkApi";
import {
  airForecastFlightHours,
  candidateName,
  cargoLabel,
  date,
  forecastMoney,
  hours,
  isNetwork,
  modeNames,
  money,
  num,
  packagingNames,
  scheduleName,
  snapshotRoute,
  totalVolume,
  type NetworkSnapshot,
  type Snapshot,
} from "./model";
const kindNames: Record<string, string> = {
  transport: "Перевозка",
  pickup_handling: "Приём и погрузка",
  delivery_handling: "Выгрузка и выдача",
  transfer: "Перегрузка",
  transfer_handling: "Перегрузка",
  storage: "Хранение",
  origin_storage: "Хранение перед отправкой",
  terminal_storage: "Хранение на терминале",
  waiting_storage: "Хранение при ожидании",
  mandatory_stop: "Пребывание на остановке",
  destination_storage: "Хранение в пункте назначения",
  handling: "Обработка",
  accumulation_storage: "Хранение при накоплении",
};
const kindName = (kind: string) =>
  kindNames[kind] || (kind.includes("storage") ? "Хранение" : kind);
const completePrice = (c: Candidate) =>
  c.status === "feasible" && c.complete && c.total_cost_rub != null;
const candidateStatus = (c: Candidate, limit: number | null) =>
  !completePrice(c) && c.forecast_price?.air_median_rub != null
    ? c.multimodal ? "Часть стоимости рассчитана" : "Авиатариф рассчитан"
    : c.status === "insufficient_data"
    ? "Недостаточно данных"
    : c.status === "infeasible"
      ? (limit != null && c.max_delivery_hours != null && c.max_delivery_hours > limit) ||
        c.issues.some(({ code }) => code.endsWith("_deadline"))
        ? "Превышен срок"
        : c.issues.some(({ code }) => code === "stage_capacity")
          ? "Партия превышает вместимость"
          : "Есть ограничение"
      : c.complete
        ? "Соответствует условиям"
        : "Неполный расчёт";
const candidatePrice = (c: Candidate) => {
  const value = completePrice(c) ? c.total_cost_rub : null;
  return c.forecast_price ? forecastMoney(value, c.forecast_price.long_term) : money(value);
};
const budgetRange = (c: Candidate) => {
  const budget = c.forecast_price;
  if (!budget || budget.lower_rub == null || budget.upper_rub == null)
    return budget?.range_status === "not_calibrated" ? "Диапазон не откалиброван" : "—";
  return `${forecastMoney(budget.lower_rub, budget.long_term)} – ${forecastMoney(budget.upper_rub, budget.long_term)}`;
};
const displayedTime = (moment?: string, offset?: number) =>
  moment ? date(moment, true) : offset == null ? "—" : `+${hours(offset)}`;
function Issues({ c }: { c: Candidate }) {
  return (
    <>
      {c.issues.map((issue, i) => (
        <p key={i}>{(issue.message || issue.code).replaceAll("max_piece_tonnes", "максимальная масса одного грузового места")}</p>
      ))}
    </>
  );
}
function sourceTitle(s: Source) {
  return (
    s.name ||
    s.description ||
    (s.roles?.includes("connectivity_observation")
      ? "Наблюдения транспортной связности «Точно»"
      : s.id)
  );
}
function ChargeSource({ result, id }: { result: Calculation; id: string }) {
  const source = result.sources.find((entry) => entry.id === id);
  if (!source) return null;
  return source.url && /^https?:\/\//.test(source.url)
    ? <a href={source.url} target="_blank" rel="noreferrer">{sourceTitle(source)}</a>
    : <span>{sourceTitle(source)}</span>;
}
function Sources({ r, ids }: { r: Calculation; ids?: string[] }) {
  const sources = ids ? r.sources.filter((s) => ids.includes(s.id)) : r.sources;
  return (
    <details className="network-sources disclosure-card">
      <summary>Источники и допущения</summary>
      <div className="disclosure-body">
      {sources.length ? (
        sources.map((s) => (
          <details className="source-item" key={s.id}>
            <summary><b>{sourceTitle(s)}</b>
            <span className="evidence-label">
              {s.status === "synthetic"
                ? "Сценарные данные"
                : s.status === "assumption"
                  ? "Заявленные условия"
                  : s.status === "fact"
                    ? "Опубликованные данные"
                    : "Источник данных"}
            </span></summary>
            <div className="source-body">
            {s.description && s.description !== sourceTitle(s) && (
              <p>{s.description}</p>
            )}
            {s.url && /^https?:\/\//.test(s.url) && (
              <a href={s.url} target="_blank" rel="noreferrer">
                Открыть источник ↗
              </a>
            )}
            {s.notes?.length ? (
              <ul>
                {s.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            ) : null}
            {s.parameters && (
              <details>
                <summary>Параметры сценария из ответа сервера</summary>
                <dl>
                  {Object.entries(s.parameters).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>
                        {value !== null && typeof value === "object"
                          ? Object.entries(value as Record<string, unknown>)
                              .map(([k, v]) => `${k}: ${String(v)}`)
                              .join("; ")
                          : String(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
            </div>
          </details>
        ))
      ) : (
        <p className="muted">Для этого варианта источники не указаны.</p>
      )}
      <p className="table-note">
        Версия расчёта {r.schema_version} · Набор {r.network_id}
      </p>
      </div>
    </details>
  );
}
function Conditions({ snapshot }: { snapshot: NetworkSnapshot }) {
  const r = snapshot.result,
    request = r.request;
  const schedule = request.schedule;
  return (
    <details className="conditions-summary ticket-shadow">
      <summary>
        <b>Условия расчёта</b>
        <span>{snapshotRoute(snapshot)}</span>
        <span>
          {num(totalVolume(r), 6)} т · {r.shipments} отправок
        </span>
        <span>{request.max_delivery_hours == null ? "Без лимита срока" : `Лимит ${hours(request.max_delivery_hours)}`}</span>
      </summary>
      <div className="conditions-expanded">
        <div className="facts">
          <div>
            <small>График</small>
            <b>{schedule ? scheduleName(schedule) : `Разовая отправка в ${r.forecast?.target_year} году`}</b>
          </div>
          <div>
            <small>Виды транспорта</small>
            <b>{request.allowed_modes.map((m) => modeNames[m]).join(", ")}</b>
          </div>
          <div>
            <small>Стоимость</small>
            <b>
              {request.cost_basis === "including_vat" ? "С НДС" : "Без НДС"}
            </b>
          </div>
          <div>
            <small>Вместимость</small>
            <b>
              {request.capacity_basis === "mass_only"
                ? "По массе"
                : "По массе и объёму"}
            </b>
          </div>
        </div>
        <div className="table-scroll">
          <table className="detail-table">
            <caption>Состав каждой отправки</caption>
            <thead>
              <tr>
                <th>Груз</th>
                <th>Масса, т</th>
                <th>Объём, м³</th>
                <th>Упаковка</th>
                <th>Делимый</th>
                <th>Погрузка → выгрузка</th>
              </tr>
            </thead>
            <tbody>
              {request.cargoes.map((c) => {
                const d = snapshot.draft.cargo.find((item) => item.id === c.id);
                const points = [
                  { id: "first", name: snapshot.draft.origin?.name },
                  ...snapshot.draft.stops.map((s) => ({
                    id: s.id,
                    name: s.city?.name,
                  })),
                  { id: "last", name: snapshot.draft.destination?.name },
                ];
                return (
                  <tr key={c.id}>
                    <th>{c.name}</th>
                    <td>{num(c.weight_tonnes, 6)}</td>
                    <td>{num(c.volume_m3, 6)}</td>
                    <td>
                      {c.packaging
                        ? packagingNames[c.packaging] || c.packaging
                        : "Не указана"}
                    </td>
                    <td>
                      {c.splittable == null
                        ? "Не указано"
                        : c.splittable
                          ? "Да"
                          : "Нет"}
                    </td>
                    <td>
                      {points.find((p) => p.id === d?.from)?.name || "—"} →{" "}
                      {points.find((p) => p.id === d?.to)?.name || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {schedule?.mode === "regular" ? (
          <p className="muted">
            Период, МСК: {date(schedule.start_at, true)} —{" "}
            {date(schedule.end_at, true)}. Конец периода не включается.
          </p>
        ) : schedule?.mode === "once" ? (
          <p className="muted">
            Готовность, МСК: {date(schedule.ready_at, true)}.
          </p>
        ) : schedule?.mode === "explicit" ? (
          <details>
            <summary>Выбранные даты, МСК</summary>
            <ul>
              {schedule.ready_at.map((value) => (
                <li key={value}>{date(value, true)}</li>
              ))}
            </ul>
          </details>
        ) : null}
        {request.stops.length > 0 && (
          <div className="table-scroll">
            <table className="detail-table">
              <caption>Действия на остановках</caption>
              <thead>
                <tr>
                  <th>Пункт</th>
                  <th>Действие</th>
                  <th>Доп. пребывание</th>
                </tr>
              </thead>
              <tbody>
                {request.stops.map((s, i) => (
                  <tr key={i}>
                    <th>
                      {snapshot.draft.stops[i]?.city?.name || s.settlement}
                    </th>
                    <td>
                      {
                        {
                          visit: s.no_reload ? "Без перегрузки" : "Обязательное посещение",
                          pickup: "Погрузка",
                          delivery: "Выгрузка",
                          pickup_delivery: "Погрузка и выгрузка",
                        }[s.action]
                      }
                    </td>
                    <td>{hours(s.dwell_hours)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="table-note">
          {request.data_policy === "verified_only"
            ? "Разрешены только подтверждённые данные."
            : "Разрешены явные сценарные допущения."}
        </p>
      </div>
    </details>
  );
}
function CandidateReport({
  snapshot,
  c,
  bridgeX,
  visible,
  base,
}: {
  snapshot: NetworkSnapshot;
  c: Candidate;
  bridgeX: number;
  visible: boolean;
  base?: Candidate;
}) {
  const r = snapshot.result;
  const yearOnly = r.forecast?.date_precision === "year";
  const batches = [...new Set(c.steps.map((s) => s.batch ?? 0))];
  const [batch, setBatch] = useState(String(batches[0] ?? 0));
  const steps = c.steps.filter(
    (s) => batch === "all" || String(s.batch ?? 0) === batch,
  );
  const hub = (id?: string) =>
    c.route_hubs.find((h) => h.id === id)?.name || id || "—";
  const partialAir = !completePrice(c) && c.forecast_price?.air_median_rub != null;
  const chargeName = (item: Candidate["cost_items"][number]) => {
    const mode = item.pricing?.category as keyof typeof modeNames | undefined;
    return c.multimodal && item.kind === "transport" && mode && modeNames[mode]
      ? `Перевозка · ${modeNames[mode]}`
      : kindName(item.kind);
  };
  const costGroups = new Map<string, number>();
  for (const item of c.cost_items) {
    if (item.amount_rub == null) continue;
    const label = chargeName(item);
    costGroups.set(
      label,
      (costGroups.get(label) || 0) + item.amount_rub,
    );
  }
  const sum = c.cost_items.reduce((n, item) => n + (item.amount_rub ?? 0), 0);
  const cargo = (id: string) =>
    r.request.cargoes.find((cargo) => cargo.id === id)?.name || id;
  return (
    <section
      id="scheme-report"
      tabIndex={-1}
      className="scheme-report ticket-shadow"
      aria-label={`Подробный расчёт: ${candidateName(c)}`}
      style={
        {
          "--bridge-x": `${bridgeX}px`,
          "--bridge-display": visible ? "block" : "none",
        } as CSSProperties
      }
    >
      <svg className="report-bridge" viewBox="0 0 72 24" aria-hidden="true">
        <path d="M0 24C25 24 23 0 36 0S47 24 72 24Z" />
      </svg>
      <div className="report-card perforated">
        <div className="report-title">
          <h2>Подробный расчёт</h2>
          <h3>{candidateName(c)}</h3>
        </div>
        {!completePrice(c) && (
          <p className="report-status-line">
            {candidateStatus(c, r.request.max_delivery_hours)} · Полная стоимость не подтверждена.
            Подробности — в допущениях варианта.
          </p>
        )}
        <div className="report-total">
          {r.forecast && completePrice(c) && c.forecast_price?.base_rub != null && (
            <div>
              <small>База · {c.forecast_price.base_year}</small>
              <strong>{money(c.forecast_price.base_rub)}</strong>
            </div>
          )}
          <div>
            <small>
              {partialAir
                ? c.multimodal ? "Учтённые этапы" : "Перелёт и авианакладная"
                : r.forecast ? `Оценка на ${r.forecast.target_year} год` : "За весь период"} ·{" "}
              {r.request.cost_basis === "including_vat" ? "с НДС" : "без НДС"}
            </small>
            <strong>{partialAir
              ? money(c.multimodal ? c.known_cost_subtotal_rub : c.forecast_price?.air_median_rub)
              : candidatePrice(c)}</strong>
          </div>
          {!partialAir && <div>
            <small>За тонну</small>
            <strong>
              {money(completePrice(c) ? c.cost_rub_per_tonne : null)}
              {completePrice(c) && c.cost_rub_per_tonne != null ? "/т" : ""}
            </strong>
          </div>}
        </div>
        <div className="report-metrics">
          {partialAir ? (
            airForecastFlightHours(c) != null && <p>Перелёт по модели <b>{hours(airForecastFlightHours(c))}</b></p>
          ) : <p>
            Максимальный срок <b>{hours(c.max_delivery_hours)}</b>
            <span className="muted">
              {" "}
              {r.request.max_delivery_hours != null ? `при лимите ${hours(r.request.max_delivery_hours)}` : "без заданного лимита"}
            </span>
          </p>}
          <p>
            Отправок в плане: <b>{r.shipments}</b> · Объём:{" "}
            <b>{num(totalVolume(r), 6)} т</b>
          </p>
        </div>
        {c.forecast_price?.air_median_rub != null ? (
          <p className="table-note">
            База тарифа «Якутии» · <b>{money(c.forecast_price.air_base_rub)}</b>.
            {c.multimodal && <> Перелёт и авианакладная · <b>{money(c.forecast_price.air_median_rub)}</b>.</>}
            {" "}Терминальные и агентские сборы оплачиваются отдельно.
            {c.forecast_price.air_softened_rub != null && (
              <> Авиатариф с ослаблением роста · <b>{money(c.forecast_price.air_softened_rub)}</b>.</>
            )}
            {c.forecast_price.long_term && " Долгосрочный ориентир для бюджета."}
          </p>
        ) : c.forecast_price && (
          <p className="table-note">
            {c.forecast_price.base_rub != null && <>База в ценах {c.forecast_price.base_year} года · <b>{money(c.forecast_price.base_rub)}</b> · </>}
            Диапазон бюджета · <b>{budgetRange(c)}</b>
            {c.forecast_price.long_term && " · Долгосрочный сценарий"}
          </p>
        )}
        <p className="table-note">
          {c.evidence_levels.includes("synthetic") ||
          c.evidence_levels.includes("assumption")
            ? "Расчёт содержит сценарные или заявленные условия."
            : "Проверьте происхождение данных в источниках."}{" "}
          Это оценка при заданных условиях, не подтверждение бронирования.
        </p>
        {c.connection_pilot && (
          <ConnectionPilot key={`${r.forecast?.target_year}-${r.network_fingerprint}-${c.id}`}
            calculation={r} candidateId={c.id} initial={c.connection_pilot} />
        )}
        {c.cost_items.length > 0 && (
          <section className="report-section">
            <h3>
              {completePrice(c)
                ? "Из чего складывается стоимость"
                : c.forecast_price?.air_median_rub != null && !c.multimodal
                  ? "Перелёт и авианакладная"
                  : "Учтённые компоненты — неполная стоимость"}
            </h3>
            <div className="cost-components">
              {[...costGroups].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <b>{money(value)}</b>
                </div>
              ))}
              <div className="cost-total">
                <span>
                  {completePrice(c)
                    ? "Итого по ответу сервера"
                    : c.forecast_price?.air_median_rub != null && !c.multimodal ? "Прогноз авиатарифа" : "Сумма учтённых строк"}
                </span>
                <b>{money(completePrice(c) ? c.total_cost_rub : costGroups.size ? sum : null)}</b>
              </div>
            </div>
            {completePrice(c) && Math.abs(sum - c.total_cost_rub!) < 0.011 && (
              <p className="formula">
                {[...costGroups.values()].map((v) => money(v)).join(" + ")} ={" "}
                {money(c.total_cost_rub)}
              </p>
            )}
            <details className="ledger-details">
              <summary>{r.forecast ? "Начисления и индексы" : "Все начисления по датам"} ({c.cost_items.length})</summary>
              <div className="table-scroll">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>Компонент</th>
                      <th>Партия</th>
                      <th>{yearOnly ? "От готовности" : "Дата, МСК"}</th>
                      {r.forecast && <><th>База</th><th>Индексы по годам</th></>}
                      <th>{r.forecast ? "Оценка" : "Сумма"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.cost_items.map((item, i) => (
                      <tr key={i}>
                        <th>{chargeName(item)}</th>
                        <td>{item.batch == null ? "—" : item.batch + 1}</td>
                        <td>{displayedTime(item.at, item.offset_hours)}</td>
                        {r.forecast && <>
                          <td>
                            {money(item.base_amount_rub)}{item.pricing && ` · ${item.pricing.base_year}`}
                            {item.pricing?.source_ids.map((id) => (
                              <div key={id}><ChargeSource result={r} id={id} /></div>
                            ))}
                            {item.pricing?.tariff_version && (
                              <div className="table-note">
                                {item.pricing.tariff_projected && <>Прогноз по опубликованной базе<br /></>}
                                {item.pricing.tariff_date_precision === "year"
                                  ? <>Прайс на {item.pricing.base_year} год<br /></>
                                  : item.pricing.valid_from && <>Тариф действует с {date(item.pricing.valid_from.slice(0, 10))}<br /></>}
                                {item.pricing.valid_until && <>До {date(item.pricing.valid_until.slice(0, 10))} (не включая)<br /></>}
                                {item.pricing.observed_on && <>Источник проверен {date(item.pricing.observed_on)}</>}
                              </div>
                            )}
                          </td>
                          <td>
                            {item.index_factors?.length ? item.index_factors.map((factor) => (
                              <div key={factor.year}>
                                {factor.year} · ×{num(factor.factor, 4)} · <ChargeSource result={r} id={factor.source_id} />
                              </div>
                            )) : item.amount_rub == null ? "Нет индекса" : "Действующая ставка"}
                            {item.index_proxy && <small>Сводный индекс, приближение</small>}
                          </td>
                        </>}
                        <td>{money(item.amount_rub)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </section>
        )}
        {c.published_tariff_estimate && (
          <div className="notice warning">
            <b>Опубликованные тарифные компоненты</b>
            <p>
              {money(c.published_tariff_estimate.amount_excluding_vat_rub)} без
              НДС. Это не полная цена перевозки.
            </p>
            <p>
              Не хватает:{" "}
              {c.published_tariff_estimate.missing_components.join(", ") ||
                "см. ограничения варианта"}
              .
            </p>
          </div>
        )}
        {!c.published_tariff_estimate && !completePrice(c) && (!r.forecast ||
          c.issues.some(({ code }) => code === "unconfirmed_stop_continuity")) &&
          c.known_cost_subtotal_rub != null && (
            <p className="table-note">
              Опубликованная часть стоимости · {money(c.known_cost_subtotal_rub)}
            </p>
          )}
        {c.connections.some((connection) => connection.reference_metadata?.scope === "station_to_station") && (
          <p className="table-note">
            Тариф ТЛМ от станции до станции. Подвоз, доставка от станции и охрана оплачиваются отдельно, если требуются.
          </p>
        )}
        {c.steps.length > 0 && (
          <section className="report-section">
            <div className="timeline-title">
              <h3>Этапы перевозки</h3>
              {batches.length > 1 && (
                <label>
                  Партия
                  <Select
                    value={batch}
                    onChange={(e) => setBatch(e.target.value)}
                  >
                    {batches.map((b) => (
                      <option key={b} value={b}>
                        Партия {b + 1}
                      </option>
                    ))}
                    <option value="all">Все партии</option>
                  </Select>
                </label>
              )}
            </div>
            <p className="table-note">
              Группа консолидации: {num(c.group_size)} отправок.{" "}
              {yearOnly ? "Время отсчитывается от готовности груза." : "Времена указаны по Москве."}
            </p>
            <ol className="route-timeline">
              {steps.map((s, i) => (
                <li key={i}>
                  <div className="timeline-label">
                    <b>
                      {s.kind === "transport" && s.mode
                        ? modeNames[s.mode]
                        : kindName(s.kind)}
                    </b>
                    <small>Партия {(s.batch ?? 0) + 1}</small>
                  </div>
                  <p>
                    {s.kind === "transport"
                      ? `${hub(s.from_hub)} → ${hub(s.to_hub)}`
                      : hub(s.hub_id)}
                  </p>
                  <p className="muted">
                    {displayedTime(s.departure_at || s.start_at, s.departure_offset_hours ?? s.start_offset_hours)}
                    {" → "}
                    {displayedTime(s.arrival_at || s.end_at, s.arrival_offset_hours ?? s.end_offset_hours)}
                    {s.schedule_status === "scenario" && " · сценарный срок"}
                  </p>
                  {s.kind === "transport" && (
                    <p className="table-note">
                      Масса на этапе: {num(s.weight_tonnes, 6)} т · Объём:{" "}
                      {num(s.volume_m3, 6)} м³ · Транспортных единиц:{" "}
                      {num(s.units)} · Ожидание: {hours(s.wait_hours)}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}
        {c.deliveries.length > 0 && (
          <section className="report-section">
            <div className="table-scroll">
              <table className="detail-table">
                <caption>Доставка каждой позиции</caption>
                <thead>
                  <tr>
                    <th>Груз</th>
                    <th>Отправка</th>
                    <th>Масса, т</th>
                    <th>{yearOnly ? "Готовность" : "Готовность, МСК"}</th>
                    <th>{yearOnly ? "Доставка от начала" : "Доставлено, МСК"}</th>
                    <th>Срок</th>
                  </tr>
                </thead>
                <tbody>
                  {c.deliveries.map((item, i) => (
                    <tr key={item.cargo_instance_id + i}>
                      <th>{cargo(item.cargo_id)}</th>
                      <td>{item.shipment + 1}</td>
                      <td>{num(item.weight_tonnes, 6)}</td>
                      <td>{displayedTime(item.ready_at, item.ready_offset_hours)}</td>
                      <td>{displayedTime(item.delivered_at, item.delivered_offset_hours)}</td>
                      <td>{hours(item.delivery_hours)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
        {c.stop_actions.length > 0 && (
          <section className="report-section">
            <h3>Остановки и изменение состава</h3>
            {c.stop_actions.map((s, i) => (
              <div className="source-item" key={i}>
                <b>{hub(s.hub_id)}</b>
                <p>
                  Погрузка: {s.pickup_cargo_ids.map(cargo).join(", ") || "—"} ·
                  Выгрузка: {s.delivery_cargo_ids.map(cargo).join(", ") || "—"}
                </p>
                <p className="table-note">
                  Дополнительное пребывание: {hours(s.dwell_hours)}
                  {s.no_reload && " · Без перегрузки"}
                </p>
              </div>
            ))}
          </section>
        )}
        {base && base.id !== c.id && completePrice(c) && (
          <section className="report-section">
            <h3>Разница относительно допустимого авто</h3>
            <p className="formula">
              {money(base.total_cost_rub)} − {money(c.total_cost_rub)} ={" "}
              {money(base.total_cost_rub! - c.total_cost_rub!)}
            </p>
            <p className="table-note">
              База — самый дешёвый допустимый вариант только на авто среди
              найденных в этом ответе. Отрицательное значение означает
              удорожание.
            </p>
          </section>
        )}
        <div className="report-disclosures">
          {(c.issues.length > 0 || c.published_tariff_estimate) && <details className="disclosure-card">
            <summary>Допущения и ограничения варианта</summary>
            <div className="disclosure-body"><Issues c={c} />
              {c.published_tariff_estimate && (
                <div className="assumption-item">
                  <b>Опубликованные тарифные компоненты</b>
                  <p>
                    {money(c.published_tariff_estimate.amount_excluding_vat_rub)} без
                    НДС. Это не полная цена перевозки.
                  </p>
                  <p>
                    Не хватает:{" "}
                    {c.published_tariff_estimate.missing_components.join(", ") ||
                      "см. ограничения варианта"}
                    .
                  </p>
                </div>
              )}
            </div>
          </details>}
          <Sources r={r} ids={c.source_ids} />
        </div>
        <button
          className="text-button"
          type="button"
          onClick={() =>
            document
              .getElementById("comparison")
              ?.scrollIntoView({ block: "start", behavior: "smooth" })
          }
        >
          ↑ К сравнению вариантов
        </button>
      </div>
    </section>
  );
}
function NetworkResults({
  snapshot,
  initialCandidate,
  saved,
  onEdit,
  onNew,
  onBack,
}: {
  snapshot: NetworkSnapshot;
  initialCandidate?: string;
  saved: boolean;
  onEdit: () => void;
  onNew: () => void;
  onBack: () => void;
}) {
  const r = snapshot.result;
  const [filter, setFilter] = useState("all");
  const [opened, setOpened] = useState<string | null>(initialCandidate ?? null);
  const [reportNavigation, setReportNavigation] = useState(0);
  function openFromMap(id: string) {
    if (id.startsWith("market-")) {
      setOpened(id);
      requestAnimationFrame(() => document.getElementById(id)?.scrollIntoView({ block: "center" }));
      return;
    }
    if (!candidates.some((candidate) => candidate.id === id)) setFilter("all");
    setOpened(id);
    setReportNavigation((count) => count + 1);
  }
  useEffect(() => {
    if (!reportNavigation) return;
    const report = document.getElementById("scheme-report");
    if (!report) return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let frameId: number;
    frameId = requestAnimationFrame((startedAt) => {
      const start = window.scrollY;
      const target = Math.min(
        start + report.getBoundingClientRect().top - 20,
        document.documentElement.scrollHeight - window.innerHeight,
      );
      const distance = target - start;
      const duration = Math.min(800, Math.max(450, Math.abs(distance) * 0.55));
      function step(now: number) {
        const progress = reduceMotion ? 1 : Math.min((now - startedAt) / duration, 1);
        const eased = (1 - Math.cos(progress * Math.PI)) / 2;
        window.scrollTo({ top: start + distance * eased, behavior: "instant" });
        if (progress < 1) frameId = requestAnimationFrame(step);
        else report?.focus({ preventScroll: true });
      }
      step(startedAt);
    });
    // A user's scroll always takes priority over an in-progress route transition.
    const cancel = () => cancelAnimationFrame(frameId);
    const cancelOnKey = (event: KeyboardEvent) => {
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) cancel();
    };
    window.addEventListener("wheel", cancel, { passive: true });
    window.addEventListener("touchstart", cancel, { passive: true });
    window.addEventListener("keydown", cancelOnKey);
    return () => {
      cancel();
      window.removeEventListener("wheel", cancel);
      window.removeEventListener("touchstart", cancel);
      window.removeEventListener("keydown", cancelOnKey);
    };
  }, [reportNavigation]);
  const frame = useRef<HTMLDivElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    selectedCell = useRef<HTMLTableCellElement>(null);
  const [bridge, setBridge] = useState({ x: 0, visible: false });
  const hideUnpricedAir = r.settlements[0] === "45000000000"
    && r.settlements.at(-1) === "05701000001";
  const ranked = useMemo(() => r.candidates.filter((candidate) =>
    !hideUnpricedAir || !candidate.modes.includes("air") || candidate.total_cost_rub != null,
  ).sort(
    (a, b) => Number(b.id === r.recommendation) - Number(a.id === r.recommendation),
  ), [r.candidates, r.recommendation, hideUnpricedAir]);
  const mapRoutes = useMemo<MapRoute[]>(() => {
    function hub(name: string, id?: string | null): MapRoute["route_hubs"][number] {
      const requested = [r.request.origin, ...r.request.stops.map((stop) => stop.settlement), r.request.destination];
      const index = requested.findIndex((point) => point === name);
      const settlement = id ?? r.settlements[index] ?? null;
      return { id: settlement ?? name, settlement_id: settlement, name, identity_status: "settlement" };
    }
    function estimate(id: string, modes: MapRoute["modes"], route_hubs: MapRoute["route_hubs"],
                      estimateLabel: string, estimatePrice: number | null): MapRoute {
      return { id, modes, route_hubs, estimateLabel, estimatePrice, steps: [], issues: [], complete: false,
        multimodal: new Set(modes).size > 1, known_cost_subtotal_rub: null, total_cost_rub: null, max_delivery_hours: null };
    }
    const air = (r.air_market?.routes ?? []).map((route, i) => {
      const first = route.legs[0];
      const points = [hub(first.origin, first.origin_id), ...route.legs.flatMap((leg) => [
        ...(leg.via_points ?? []).map((point) => hub(point.name, point.id)),
        hub(leg.destination, leg.destination_id),
      ])];
      return estimate(`market-air-${i}`, ["air"], points, `Авиа · ${points.map((point) => point.name).join(" → ")}`, route.forecast_rub);
    });
    const land = (r.land_market?.routes ?? []).flatMap((route, i) => {
      if (r.road_market_estimate && r.land_market?.routes.length === 1 && route.legs.length === 1 &&
          route.legs[0].mode === "road" && route.movement_rub === r.road_market_estimate.forecast_road_rub &&
          !r.land_market.unpriced_rail?.length) return [];
      const first = route.legs[0];
      const points = [hub(first.origin, first.origin_id), ...route.legs.map((leg) => hub(leg.destination, leg.destination_id))];
      const modes = route.legs.map((leg) => leg.mode);
      const label = r.land_market?.version.startsWith("reefer-market")
        ? `Рефрижераторы × ${first.units}` : modes.map((mode) => modeNames[mode]).join(" → ");
      return [estimate(`market-land-${i}`, modes, points,
        `${label} · ${points.map((point) => point.name).join(" → ")}`, route.movement_rub)];
    });
    const road = r.road_market_estimate;
    return [...ranked.map((route) => ({ ...route, segmentModes: route.connections.map((leg) => leg.mode) })),
      ...air, ...land, ...(road && !land.some((route) => route.modes.every((mode) => mode === "road"))
      ? [estimate("market-road", ["road"], [hub(road.origin, r.settlements[0]), hub(road.destination, r.settlements.at(-1))],
        "Авто · основное плечо", road.forecast_road_rub)] : [])];
  }, [r, ranked]);
  const candidates = ranked.filter(
    (c) =>
      filter === "all" ||
      (filter === "multimodal" && c.multimodal) ||
      c.modes.includes(filter as keyof typeof modeNames),
  );
  const selected = candidates.find((c) => c.id === opened);
  const base = r.candidates
    .filter((c) => completePrice(c) && c.modes.every((m) => m === "road"))
    .sort((a, b) => a.total_cost_rub! - b.total_cost_rub!)[0];
  useLayoutEffect(() => {
    const update = () => {
      if (!frame.current || !selectedCell.current) return;
      const outer = frame.current.getBoundingClientRect(),
        cell = selectedCell.current.getBoundingClientRect();
      const x = cell.left + cell.width / 2 - outer.left;
      setBridge({ x, visible: x >= 36 && x <= outer.width - 36 });
    };
    update();
    const observer = new ResizeObserver(update);
    if (frame.current) observer.observe(frame.current);
    const el = scroll.current;
    el?.addEventListener("scroll", update, { passive: true });
    return () => {
      observer.disconnect();
      el?.removeEventListener("scroll", update);
    };
  }, [opened, filter]);
  const hasAirTariff = candidates.some((c) => c.forecast_price?.air_median_rub != null);
  const airTariffsOnly = hasAirTariff && candidates.every((c) => !completePrice(c) && c.forecast_price?.air_median_rub != null);
  const rows = [
    ...(!airTariffsOnly ? [{
      label: r.forecast ? `Полная оценка на ${r.forecast.target_year} год` : "Стоимость за период",
      value: (c: Candidate) => (
        <strong>{!completePrice(c) && c.forecast_price?.air_median_rub != null ? "Требуются ставки сборов" : candidatePrice(c)}</strong>
      ),
    }] : []),
    ...(candidates.some((c) => c.issues.some(({ code }) => code === "unconfirmed_stop_continuity")) ? [{
      label: "По ставкам отдельных плеч",
      value: (c: Candidate) => c.issues.some(({ code }) => code === "unconfirmed_stop_continuity")
        ? money(c.known_cost_subtotal_rub) : "—",
    }] : []),
    ...(candidates.some((c) => c.multimodal && c.forecast_price?.air_median_rub != null) ? [{
      label: "Оценка учтённых этапов",
      value: (c: Candidate) => c.multimodal && c.forecast_price?.air_median_rub != null
        ? money(c.known_cost_subtotal_rub) : "—",
    }] : []),
    ...(hasAirTariff ? [{
      label: r.forecast ? `Перелёт и авианакладная · ${r.forecast.target_year}` : "Перелёт и авианакладная",
      value: (c: Candidate) => <strong>{money(c.forecast_price?.air_median_rub)}</strong>,
    }] : []),
    ...(r.forecast ? [
      ...(!airTariffsOnly ? [{ label: "Диапазон бюджета", value: budgetRange }] : []),
      { label: "Базовая стоимость", value: (c: Candidate) => {
        const budget = c.forecast_price;
        if (budget?.air_base_rub != null) return `${money(budget.air_base_rub)} · база авиатарифа`;
        return budget?.base_rub == null ? "—" : `${money(budget.base_rub)} · ${budget.base_year}`;
      } },
    ] : []),
    ...(candidates.some((c) => c.forecast_price?.air_softened_rub != null) ? [
      { label: "Авиатариф с ослаблением роста", value: (c: Candidate) => money(c.forecast_price?.air_softened_rub) },
    ] : []),
    ...(candidates.some((c) => airForecastFlightHours(c) != null) ? [
      { label: "Перелёт по модели", value: (c: Candidate) => hours(airForecastFlightHours(c)) },
    ] : []),
    ...(!airTariffsOnly ? [{
      label: "За тонну",
      value: (c: Candidate) =>
        money(completePrice(c) ? c.cost_rub_per_tonne : null),
    }, {
      label: "Макс. срок доставки",
      value: (c: Candidate) => (
        <>
          {hours(c.max_delivery_hours)}
          {c.max_delivery_hours != null &&
            r.request.max_delivery_hours != null &&
            c.max_delivery_hours > r.request.max_delivery_hours && (
              <small className="over-limit">
                +{hours(c.max_delivery_hours - r.request.max_delivery_hours)}
              </small>
            )}
        </>
      ),
    }] : []),
    ...(base
      ? [
          {
            label: "Разница к авто",
            value: (c: Candidate) =>
              c.id === base.id
                ? "—"
                : money(
                    completePrice(c)
                      ? base.total_cost_rub! - c.total_cost_rub!
                      : null,
                  ),
          },
        ]
      : []),
    {
      label: "Этапов маршрута",
      value: (c: Candidate) => num(c.connections.length),
    },
    {
      label: "Группа консолидации",
      value: (c: Candidate) => `${num(c.group_size)} отправок`,
    },
  ];
  return (
    <div className="results network-results">
      {saved && (
        <button className="text-button back-link" onClick={onBack}>
          ← Вернуться к истории
        </button>
      )}
      <div className="page-title">
        <div>
          <h1>{r.forecast ? "Прогноз грузоперевозок" : saved ? "Сохранённый расчёт" : "Результат расчёта"}</h1>
          <p className="muted">
            {saved ? date(snapshot.createdAt, true) + " · " : ""}
            {r.forecast ? `Прогноз стоимости · ${r.forecast.target_year} год` : "Расчёт перевозки"} ·{" "}
            {r.request.cost_basis === "including_vat" ? "с НДС" : "без НДС"}
          </p>
        </div>
        <div className="title-actions">
          {(
            <button className="outline-button" onClick={onEdit}>
              Изменить условия
            </button>
          )}
          <button className="outline-button" onClick={onNew}>
            + Новый прогноз
          </button>
        </div>
      </div>
      <Conditions snapshot={snapshot} />
      <div className={`forecast-result-grid${r.forecast ? "" : " without-forecast"}`}>
        {r.forecast && <ForecastOverview snapshot={snapshot} compact />}
        <section className="route-summary" aria-label="Варианты доставки">
          <div className="route-summary-heading">
            <div>
              <span className="forecast-eyebrow">Шаг 2 из 2 · Результат</span>
              <h2>Варианты доставки</h2>
            </div>
            <span className="route-count">{mapRoutes.length}</span>
          </div>
          <p className="route-summary-note">Полная стоимость и срок для заданных условий. Нажмите на вариант, чтобы открыть расчёт.</p>
          {!r.recommendation && ranked.length > 0 && <p className="route-search-note">Рекомендации нет: проверьте ограничения вариантов.</p>}
          <div className="route-summary-list">
            {ranked.map((c, index) => <button
              type="button"
              key={c.id}
              className={`route-summary-card${c.id === r.recommendation ? " is-recommended" : ""}`}
              onClick={() => openFromMap(c.id)}
              aria-label={`Открыть расчёт: вариант ${index + 1}, ${candidateName(c)}`}
            >
              <span className="route-summary-top">
                <span>Вариант {index + 1}</span>
                <span>{c.id === r.recommendation ? "Рекомендуем" : candidateStatus(c, r.request.max_delivery_hours)}</span>
              </span>
              <strong className="route-summary-name">{candidateName(c)}</strong>
              <span className="route-summary-bottom">
                <strong>{completePrice(c) ? money(c.total_cost_rub) : candidatePrice(c)}</strong>
                <span>{hours(c.max_delivery_hours)}</span>
                <span aria-hidden="true">↗</span>
              </span>
            </button>)}
            {mapRoutes.filter((route) => route.estimateLabel).map((route) => <button
              type="button" key={route.id} className="route-summary-card" onClick={() => openFromMap(route.id)}
            >
              <span className="route-summary-top">Оценка перевозки</span>
              <strong className="route-summary-name">{route.estimateLabel}</strong>
              <span className="route-summary-bottom">
                <strong>{route.estimatePrice == null ? "Смета по плечам" : money(route.estimatePrice)}</strong>
                <span>Срок по согласованию</span>
                <span aria-hidden="true">↗</span>
              </span>
            </button>)}
          </div>
          {!mapRoutes.length && <p className="notice">По заданным условиям вариантов не найдено. Попробуйте изменить условия перевозки.</p>}

        </section>
      </div>
      {mapRoutes.length > 0 && (
        <RouteMap candidates={mapRoutes} recommendation={r.recommendation}
          opened={opened} onOpen={openFromMap} />
      )}
      {opened === "market-road" && <RoadMarketEstimate result={r} anchor="market-road" />}
      {opened?.startsWith("market-land-") && <LandMarketOptions result={r} />}
      {opened?.startsWith("market-air-") && <AirMarketOptions result={r} />}
      {r.published_coverage ? <PublishedCoverage result={r} /> : r.candidates.length === 0 ? (mapRoutes.length ? null :
        <section className="empty-state perforated">
          <h2>{r.air_market?.weight_limits?.some(Boolean) ? "Для этой партии нужна котировка" :
            r.land_market || r.air_market?.routes.length ? "Для оценки срока нужна информация перевозчика" : "В загруженной сети вариант не найден"}</h2>
          <p>
            {r.air_market?.routes.length || r.air_market?.weight_limits?.some(Boolean)
              ? "Проверьте габариты отдельных мест и согласуйте условия отправки с перевозчиком."
              : "Для подтверждения маршрута и срока нужны сведения перевозчика."}
          </p>
          <button className="outline-button" onClick={onEdit}>
            Изменить условия
          </button>
        </section>
      ) : (
        <>
          <div
            className="candidate-filters"
            aria-label="Фильтр вариантов по транспорту"
          >
            {[
              ["all", "Все варианты"],
              ...Object.entries(modeNames).filter(([m]) =>
                r.candidates.some((c) =>
                  c.modes.includes(m as keyof typeof modeNames),
                ),
              ),
              ...(r.candidates.some((c) => c.multimodal)
                ? [["multimodal", "Мультимодальные"]]
                : []),
            ].map(([id, name]) => (
              <button
                type="button"
                key={id}
                aria-pressed={filter === id}
                onClick={() => {
                  setFilter(id);
                  setOpened(null);
                }}
              >
                {name}
              </button>
            ))}
          </div>
          <p className="table-note">
            Показано {candidates.length} из {r.candidates.length}. Смешанный
            маршрут может входить в несколько фильтров.
          </p>
          {candidates.some((c) => !completePrice(c) && c.forecast_price?.air_median_rub != null) && (
            <p className="table-note">
              Для полной оценки авиаварианта нужны ставки терминальных и агентских сборов.
              Рекомендация учитывает варианты с полной оценкой.
            </p>
          )}
          <p className="mobile-scroll-hint">
            Таблицу можно прокручивать вбок →
          </p>
          <div ref={frame} className="comparison-frame">
            <div ref={scroll} className="comparison-scroll">
              <table
                id="comparison" className="comparison-table"
                style={{ minWidth: `${190 + candidates.length * 260}px` }}
              >
                <caption className="sr-only">
                  Сравнение найденных вариантов перевозки
                </caption>
                <colgroup>
                  <col className="metric-col" />
                  {candidates.map((c) => (
                    <col key={c.id} />
                  ))}
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">
                      <span className="sr-only">Показатель</span>
                    </th>
                    {candidates.map((c, i) => (
                      <th
                        key={c.id}
                        scope="col"
                        className={`scheme-head ${c.id === r.recommendation ? "recommended" : ""}`}
                      >
                        <div>
                          <span className="scheme-badge">
                            {c.id === r.recommendation
                              ? "Рекомендуем"
                              : candidateStatus(
                                  c,
                                  r.request.max_delivery_hours,
                                )}
                          </span>
                          <span className="candidate-number">
                            Вариант {ranked.indexOf(c) + 1}
                          </span>
                        </div>
                        <b>{candidateName(c)}</b>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.label}>
                      <th scope="row">{row.label}</th>
                      {candidates.map((c) => (
                        <td
                          key={c.id}
                          className={
                            c.id === r.recommendation ? "recommended" : ""
                          }
                        >
                          {row.value(c)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td />
                    {candidates.map((c) => (
                      <td
                        key={c.id}
                        ref={opened === c.id ? selectedCell : undefined}
                        className={`${c.id === r.recommendation ? "recommended" : ""} ${opened === c.id ? "expanded-scheme" : ""}`}
                      >
                        <button
                          className="text-button"
                          type="button"
                          aria-label={`${opened === c.id ? "Свернуть" : "Подробнее"}: вариант ${ranked.indexOf(c) + 1}`}
                          aria-expanded={opened === c.id}
                          aria-controls={
                            opened === c.id ? "scheme-report" : undefined
                          }
                          onClick={() => {
                            if (opened === c.id) setOpened(null);
                            else openFromMap(c.id);
                          }}
                        >
                          {opened === c.id ? "Свернуть" : "Подробнее"}
                        </button>
                        {opened === c.id && (
                          <svg
                            className="column-bridge"
                            viewBox="0 0 72 24"
                            aria-hidden="true"
                          >
                            <path d="M0 0C25 0 23 24 36 24S47 0 72 0Z" />
                          </svg>
                        )}
                      </td>
                    ))}
                  </tr>
                </tfoot>
              </table>
            </div>
            {selected && (
              <CandidateReport
                key={selected.id}
                snapshot={snapshot}
                c={selected}
                bridgeX={bridge.x}
                visible={bridge.visible}
                base={base}
              />
            )}
          </div>
        </>
      )}
      {base && (
        <p className="table-note">
          Разница к авто вычислена относительно самого дешёвого допустимого
          варианта только на авто из этого ответа. Плюс — экономия, минус —
          удорожание.
        </p>
      )}
      <details className="method-notes">
        <summary>Допущения и ограничения расчёта</summary>
        <div className="method-body">
          {r.search.truncated && (
            <div className="assumption-item">
              <b>Поиск ограничен</b>
              <p>
                Показаны найденные варианты в пределах поиска. Глобально самый
                дешёвый маршрут не гарантируется.
              </p>
            </div>
          )}
          <div className="result-notices">
            {!r.recommendation && r.candidates.length > 0 && (
              <div className="assumption-item">
                <b>Нет допустимого варианта для рекомендации</b>
                <p>Посмотрите причины ограничений ниже или измените условия.</p>
              </div>
            )}
            {ranked
              .filter((c) => c.status !== "feasible" || !c.complete)
              .map((c) => (
                <div className="assumption-item" key={c.id}>
                  <b>
                    Вариант {ranked.indexOf(c) + 1} · {candidateName(c)}
                  </b>
                  <Issues c={c} />
                </div>
              ))}
          </div>
          <h3>Покрытие и методика</h3>
          <ul>
            {r.warnings.map((w, i) => (
              <li key={"w" + i}>{w}</li>
            ))}
            {r.coverage.limitations.map((w, i) => (
              <li key={"c" + i}>{w}</li>
            ))}
          </ul>
          <p className="table-note">
            {r.coverage.geography}. Населённых пунктов в снимке:{" "}
            {r.coverage.settlements}. Связность не подтверждает наличие грузовой
            услуги.
          </p>
          <Sources r={r} />
        </div>
      </details>
      <p className="result-disclaimer">
        Цены и ресурсы могут включать явно указанные сценарные допущения. Расчёт
        не является предложением перевозчика или бронированием.
      </p>
    </div>
  );
}
export function Results({
  snapshot,
  initialCandidate,
  saved = false,
  onEdit,
  onNew,
  onBack,
}: {
  snapshot: Snapshot;
  initialCandidate?: string;
  saved?: boolean;
  onEdit: () => void;
  onNew: () => void;
  onBack: () => void;
}) {
  return isNetwork(snapshot) ? (
    <NetworkResults
      snapshot={snapshot}
      initialCandidate={initialCandidate}
      saved={saved}
      onEdit={onEdit}
      onNew={onNew}
      onBack={onBack}
    />
  ) : (
    <LegacyResults
      snapshot={snapshot}
      saved={saved}
      onEdit={onEdit}
      onNew={onNew}
      onBack={onBack}
    />
  );
}
export function History({
  entries,
  onOpen,
  onNew,
}: {
  entries: Snapshot[];
  onOpen: (s: Snapshot) => void;
  onNew: () => void;
}) {
  const [search, setSearch] = useState("");
  const [year, setYear] = useState("");
  const years = [...new Set(entries.flatMap((s) =>
    isNetwork(s) && s.result.forecast ? [s.result.forecast.target_year] : [],
  ))].sort((a, b) => b - a);
  const filtered = entries.filter((s) =>
    `${snapshotRoute(s)} ${cargoLabel(s)}`.toLocaleLowerCase("ru")
      .includes(search.trim().toLocaleLowerCase("ru")) &&
    (!year || (isNetwork(s) && String(s.result.forecast?.target_year) === year)),
  );
  let previous = "";
  return (
    <div className="history-page">
      <div className="page-title">
        <div>
          <h1>История прогнозов</h1>
          <p className="muted">
            Снимки в этом браузере · отдельно для вашего аккаунта
          </p>
        </div>
        <button className="outline-button" onClick={onNew}>
          + Новый прогноз
        </button>
      </div>
      <label className="history-search">
        <span className="sr-only">Поиск по маршруту или грузу</span>
        <input
          type="search"
          placeholder="Найти по маршруту или грузу"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      {years.length > 0 && <label className="history-year-filter">Год прогноза
        <Select value={year} onChange={(e) => setYear(e.target.value)}>
          <option value="">Все годы</option>
          {years.map((value) => <option key={value} value={value}>{value}</option>)}
        </Select>
      </label>}
      {!filtered.length && (
        <div className="empty-state perforated">
          <h2>
            {entries.length ? "Ничего не найдено" : "Здесь будут ваши расчёты"}
          </h2>
          <p>
            {entries.length
              ? "Попробуйте другое название города или груза."
              : "Задайте условия перевозки. Завершённый расчёт появится в истории автоматически."}
          </p>
          {!entries.length && (
            <button className="primary" onClick={onNew}>
              Начать расчёт →
            </button>
          )}
        </div>
      )}
      {filtered.map((s) => {
        const day = date(s.createdAt),
          newDay = previous !== day;
        previous = day;
        let recommendation = "Нет допустимой рекомендации";
        let price: number | null = null;
        const forecastCandidate = isNetwork(s) && s.result.forecast
          ? s.result.candidates.find((c) => c.id === s.result.recommendation)
          : undefined;
        const forecastBase = forecastCandidate?.forecast_price;
        if (isNetwork(s)) {
          const c = s.result.candidates.find(
            (c) => c.id === s.result.recommendation,
          );
          if (c && completePrice(c)) {
            recommendation = candidateName(c);
            price = c.total_cost_rub;
          }
        } else {
          const c = s.result.schemes.find(
            (c) => c.scheme === s.result.recommendation,
          );
          if (c) {
            recommendation =
              {
                spot_road: "Разовые автозаказы",
                contract_road: "Авторейсы по договору",
                road_rail_road: "Авто → ж/д → авто",
              }[c.scheme] || c.scheme;
            price = c.total_cost_rub;
          }
        }
        return (
          <Fragment key={s.id}>
            {newDay && <h2 className="history-date">{day}</h2>}
            <article className="history-ticket perforated ticket-shadow">
              <div>
                <h3>{snapshotRoute(s)}</h3>
                <p>{cargoLabel(s)}</p>
                <p className="muted">
                  {num(totalVolume(s.result), 6)} т · {s.result.shipments}{" "}
                  отправок
                </p>
              </div>
              <div className="history-recommendation">
                <small>{isNetwork(s) && s.result.forecast
                  ? `Прогноз на ${s.result.forecast.target_year} год`
                  : "Рекомендация на момент расчёта"}</small>
                <b>{recommendation}</b>
                <p>
                  {price == null
                    ? "Проверьте ограничения в отчёте"
                    : isNetwork(s) && s.result.forecast ? money(price) : `${money(price)} за период`}
                </p>
                {price != null && forecastBase?.base_rub != null && forecastBase.base_rub > 0 && (
                  <small>База {forecastBase.base_year} · {money(forecastBase.base_rub)} ·{" "}
                    {price >= forecastBase.base_rub ? "+" : ""}
                    {num((price / forecastBase.base_rub - 1) * 100, 1)}%
                  </small>
                )}
                <small>
                  {isNetwork(s)
                    ? "Сценарная сеть · версия " + s.result.schema_version
                    : "Прежний расчёт · версия " + s.result.schema_version}
                </small>
              </div>
              <time dateTime={s.createdAt}>
                {new Intl.DateTimeFormat("ru-RU", {
                  timeZone: "Europe/Moscow",
                  hour: "2-digit",
                  minute: "2-digit",
                }).format(new Date(s.createdAt))}
              </time>
              <button
                className="history-open text-button"
                onClick={() => onOpen(s)}
              >
                {isNetwork(s) && s.result.forecast ? "Открыть прогноз ↗" : "Открыть расчёт ↗"}
              </button>
            </article>
          </Fragment>
        );
      })}
      <p className="history-footnote">
        Снимки не пересчитываются при открытии. История не синхронизируется
        между устройствами; очистка данных сайта удалит её.
      </p>
    </div>
  );
}
