import {
  Fragment,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { Results as LegacyResults } from "./LegacyResults";
import type { Calculation, Candidate, Source } from "./networkApi";
import {
  candidateName,
  cargoLabel,
  date,
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
const candidateStatus = (c: Candidate, limit: number) =>
  c.status === "insufficient_data"
    ? "Недостаточно данных"
    : c.status === "infeasible"
      ? c.max_delivery_hours != null && c.max_delivery_hours > limit
        ? "Превышен срок"
        : "Есть ограничение"
      : c.complete
        ? "Соответствует условиям"
        : "Неполный расчёт";
function Issues({ c }: { c: Candidate }) {
  return (
    <>
      {c.issues.map((issue, i) => (
        <p key={i}>{issue.message || issue.code}</p>
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
function Sources({ r, ids }: { r: Calculation; ids?: string[] }) {
  const sources = ids ? r.sources.filter((s) => ids.includes(s.id)) : r.sources;
  return (
    <section className="network-sources">
      <h3>Источники и допущения</h3>
      {sources.length ? (
        sources.map((s) => (
          <article className="source-item" key={s.id}>
            <b>{sourceTitle(s)}</b>
            <span className="evidence-label">
              {s.status === "synthetic"
                ? "Сценарные данные"
                : s.status === "assumption"
                  ? "Заявленные условия"
                  : s.status === "fact"
                    ? "Опубликованные данные"
                    : "Источник данных"}
            </span>
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
          </article>
        ))
      ) : (
        <p className="muted">Для этого варианта источники не указаны.</p>
      )}
      <p className="table-note">
        Версия расчёта {r.schema_version} · Набор {r.network_id}
      </p>
    </section>
  );
}
function Conditions({ snapshot }: { snapshot: NetworkSnapshot }) {
  const r = snapshot.result,
    request = r.request;
  return (
    <details className="conditions-summary ticket-shadow">
      <summary>
        <b>Условия расчёта</b>
        <span>{snapshotRoute(snapshot)}</span>
        <span>
          {num(totalVolume(r), 6)} т · {r.shipments} отправок
        </span>
        <span>Лимит {hours(request.max_delivery_hours)}</span>
        <span className="chevron" aria-hidden="true">
          ⌄
        </span>
      </summary>
      <div className="conditions-expanded">
        <div className="facts">
          <div>
            <small>График</small>
            <b>{scheduleName(request.schedule)}</b>
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
        {request.schedule.mode === "regular" ? (
          <p className="muted">
            Период, МСК: {date(request.schedule.start_at, true)} —{" "}
            {date(request.schedule.end_at, true)}. Конец периода не включается.
          </p>
        ) : request.schedule.mode === "once" ? (
          <p className="muted">
            Готовность, МСК: {date(request.schedule.ready_at, true)}.
          </p>
        ) : (
          <details>
            <summary>Выбранные даты, МСК</summary>
            <ul>
              {request.schedule.ready_at.map((value) => (
                <li key={value}>{date(value, true)}</li>
              ))}
            </ul>
          </details>
        )}
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
                          visit: "Обязательное посещение",
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
  const batches = [...new Set(c.steps.map((s) => s.batch ?? 0))];
  const [batch, setBatch] = useState(String(batches[0] ?? 0));
  const steps = c.steps.filter(
    (s) => batch === "all" || String(s.batch ?? 0) === batch,
  );
  const hub = (id?: string) =>
    c.route_hubs.find((h) => h.id === id)?.name || id || "—";
  const costGroups = new Map<string, number>();
  for (const item of c.cost_items)
    costGroups.set(
      item.kind,
      (costGroups.get(item.kind) || 0) + item.amount_rub,
    );
  const sum = c.cost_items.reduce((n, item) => n + item.amount_rub, 0);
  const cargo = (id: string) =>
    r.request.cargoes.find((cargo) => cargo.id === id)?.name || id;
  return (
    <section
      id="scheme-report"
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
          <div className="notice warning">
            <b>{candidateStatus(c, r.request.max_delivery_hours)}</b>
            <Issues c={c} />
            <p>
              Итоговая цена не подтверждена. Доступные этапы и компоненты ниже
              не являются полной стоимостью.
            </p>
          </div>
        )}
        <div className="report-total">
          <div>
            <small>
              За весь период ·{" "}
              {r.request.cost_basis === "including_vat" ? "с НДС" : "без НДС"}
            </small>
            <strong>{money(completePrice(c) ? c.total_cost_rub : null)}</strong>
          </div>
          <div>
            <small>За тонну</small>
            <strong>
              {money(completePrice(c) ? c.cost_rub_per_tonne : null)}
              {completePrice(c) && c.cost_rub_per_tonne != null ? "/т" : ""}
            </strong>
          </div>
        </div>
        <div className="report-metrics">
          <p>
            Максимальный срок <b>{hours(c.max_delivery_hours)}</b>
            <span className="muted">
              {" "}
              при лимите {hours(r.request.max_delivery_hours)}
            </span>
          </p>
          <p>
            Отправок в плане: <b>{r.shipments}</b> · Объём:{" "}
            <b>{num(totalVolume(r), 6)} т</b>
          </p>
        </div>
        <p className="table-note">
          {c.evidence_levels.includes("synthetic") ||
          c.evidence_levels.includes("assumption")
            ? "Расчёт содержит сценарные или заявленные условия."
            : "Проверьте происхождение данных в источниках."}{" "}
          Это оценка при заданных условиях, не подтверждение бронирования.
        </p>
        {c.cost_items.length > 0 && (
          <section className="report-section">
            <h3>
              {completePrice(c)
                ? "Из чего складывается стоимость"
                : "Учтённые компоненты — неполная стоимость"}
            </h3>
            <div className="cost-components">
              {[...costGroups].map(([kind, value]) => (
                <div key={kind}>
                  <span>{kindName(kind)}</span>
                  <b>{money(value)}</b>
                </div>
              ))}
              <div className="cost-total">
                <span>
                  {completePrice(c)
                    ? "Итого по ответу сервера"
                    : "Сумма учтённых строк"}
                </span>
                <b>{money(completePrice(c) ? c.total_cost_rub : sum)}</b>
              </div>
            </div>
            {completePrice(c) && Math.abs(sum - c.total_cost_rub!) < 0.011 && (
              <p className="formula">
                {[...costGroups.values()].map((v) => money(v)).join(" + ")} ={" "}
                {money(c.total_cost_rub)}
              </p>
            )}
            <details className="ledger-details">
              <summary>Все начисления по датам ({c.cost_items.length})</summary>
              <div className="table-scroll">
                <table className="detail-table">
                  <thead>
                    <tr>
                      <th>Компонент</th>
                      <th>Партия</th>
                      <th>Дата, МСК</th>
                      <th>Сумма</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.cost_items.map((item, i) => (
                      <tr key={i}>
                        <th>{kindName(item.kind)}</th>
                        <td>{item.batch == null ? "—" : item.batch + 1}</td>
                        <td>{item.at ? date(item.at, true) : "—"}</td>
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
        {c.steps.length > 0 && (
          <section className="report-section">
            <div className="timeline-title">
              <h3>Этапы перевозки</h3>
              {batches.length > 1 && (
                <label>
                  Партия
                  <select
                    value={batch}
                    onChange={(e) => setBatch(e.target.value)}
                  >
                    {batches.map((b) => (
                      <option key={b} value={b}>
                        Партия {b + 1}
                      </option>
                    ))}
                    <option value="all">Все партии</option>
                  </select>
                </label>
              )}
            </div>
            <p className="table-note">
              Группа консолидации: {num(c.group_size)} отправок. Времена указаны
              по Москве.
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
                    {s.departure_at || s.start_at
                      ? date((s.departure_at || s.start_at)!, true)
                      : "—"}{" "}
                    →{" "}
                    {s.arrival_at || s.end_at
                      ? date((s.arrival_at || s.end_at)!, true)
                      : "—"}
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
                    <th>Готовность, МСК</th>
                    <th>Доставлено, МСК</th>
                    <th>Срок</th>
                  </tr>
                </thead>
                <tbody>
                  {c.deliveries.map((item, i) => (
                    <tr key={item.cargo_instance_id + i}>
                      <th>{cargo(item.cargo_id)}</th>
                      <td>{item.shipment + 1}</td>
                      <td>{num(item.weight_tonnes, 6)}</td>
                      <td>{date(item.ready_at, true)}</td>
                      <td>{date(item.delivered_at, true)}</td>
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
        <section className="report-section">
          <Sources r={r} ids={c.source_ids} />
        </section>
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
  saved,
  onEdit,
  onNew,
  onBack,
}: {
  snapshot: NetworkSnapshot;
  saved: boolean;
  onEdit: () => void;
  onNew: () => void;
  onBack: () => void;
}) {
  const r = snapshot.result;
  const [filter, setFilter] = useState("all");
  const [opened, setOpened] = useState<string | null>(null);
  const frame = useRef<HTMLDivElement>(null),
    scroll = useRef<HTMLDivElement>(null),
    selectedCell = useRef<HTMLTableCellElement>(null);
  const [bridge, setBridge] = useState({ x: 0, visible: false });
  const candidates = r.candidates.filter(
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
  const rows = [
    {
      label: "Стоимость за период",
      value: (c: Candidate) => (
        <strong>{money(completePrice(c) ? c.total_cost_rub : null)}</strong>
      ),
    },
    {
      label: "За тонну",
      value: (c: Candidate) =>
        money(completePrice(c) ? c.cost_rub_per_tonne : null),
    },
    {
      label: "Макс. срок доставки",
      value: (c: Candidate) => (
        <>
          {hours(c.max_delivery_hours)}
          {c.max_delivery_hours != null &&
            c.max_delivery_hours > r.request.max_delivery_hours && (
              <small className="over-limit">
                +{hours(c.max_delivery_hours - r.request.max_delivery_hours)}
              </small>
            )}
        </>
      ),
    },
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
          <h1>{saved ? "Сохранённый расчёт" : "Результат расчёта"}</h1>
          <p className="muted">
            {saved ? date(snapshot.createdAt, true) + " · " : ""}Сценарный
            расчёт ·{" "}
            {r.request.cost_basis === "including_vat" ? "с НДС" : "без НДС"}
          </p>
        </div>
        <div className="title-actions">
          {!saved && (
            <button className="outline-button" onClick={onEdit}>
              Изменить условия
            </button>
          )}
          <button className="outline-button" onClick={onNew}>
            + Новый расчёт
          </button>
        </div>
      </div>
      <Conditions snapshot={snapshot} />
      <h2 id="comparison" className="comparison-title">
        Сравнение вариантов
      </h2>
      {r.search.truncated && (
        <div className="notice warning">
          <b>Поиск ограничен</b>
          <p>
            Показаны найденные варианты в пределах поиска. Глобально самый
            дешёвый маршрут не гарантируется.
          </p>
        </div>
      )}
      {r.candidates.length === 0 ? (
        <section className="empty-state perforated">
          <h2>В загруженной сети вариант не найден</h2>
          <p>
            При выбранных условиях и ограничениях поиска подходящих цепочек нет.
            Это не означает, что перевозка между этими пунктами невозможна.
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
          <p className="mobile-scroll-hint">
            Таблицу можно прокручивать вбок →
          </p>
          <div ref={frame} className="comparison-frame">
            <div ref={scroll} className="comparison-scroll">
              <table
                className="comparison-table"
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
                            Вариант {r.candidates.indexOf(c) + 1}
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
                          aria-label={`${opened === c.id ? "Свернуть" : "Подробнее"}: вариант ${r.candidates.indexOf(c) + 1}`}
                          aria-expanded={opened === c.id}
                          aria-controls={
                            opened === c.id ? "scheme-report" : undefined
                          }
                          onClick={() =>
                            setOpened((old) => (old === c.id ? null : c.id))
                          }
                        >
                          {opened === c.id ? "Свернуть" : "Подробнее"}{" "}
                          <span aria-hidden="true">
                            {opened === c.id ? "⌃" : "⌄"}
                          </span>
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
      <div className="result-notices">
        {!r.recommendation && r.candidates.length > 0 && (
          <div className="notice warning">
            <b>Нет допустимого варианта для рекомендации</b>
            <p>Посмотрите причины ограничений ниже или измените условия.</p>
          </div>
        )}
        {r.candidates
          .filter((c) => c.status !== "feasible" || !c.complete)
          .map((c) => (
            <div className="notice warning" key={c.id}>
              <b>
                Вариант {r.candidates.indexOf(c) + 1} · {candidateName(c)}
              </b>
              <Issues c={c} />
            </div>
          ))}
      </div>
      {base && (
        <p className="table-note">
          Разница к авто вычислена относительно самого дешёвого допустимого
          варианта только на авто из этого ответа. Плюс — экономия, минус —
          удорожание.
        </p>
      )}
      <details className="method-notes">
        <summary>Источники, предупреждения и покрытие сети</summary>
        <div className="method-body">
          <h3>Ограничения расчёта</h3>
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
  saved = false,
  onEdit,
  onNew,
  onBack,
}: {
  snapshot: Snapshot;
  saved?: boolean;
  onEdit: () => void;
  onNew: () => void;
  onBack: () => void;
}) {
  return isNetwork(snapshot) ? (
    <NetworkResults
      snapshot={snapshot}
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
  const filtered = entries.filter((s) =>
    `${snapshotRoute(s)} ${cargoLabel(s)}`
      .toLocaleLowerCase("ru")
      .includes(search.trim().toLocaleLowerCase("ru")),
  );
  let previous = "";
  return (
    <div className="history-page">
      <div className="page-title">
        <div>
          <h1>История расчётов</h1>
          <p className="muted">
            Снимки в этом браузере · отдельно для вашего аккаунта
          </p>
        </div>
        <button className="outline-button" onClick={onNew}>
          + Новый расчёт
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
                <small>Рекомендация на момент расчёта</small>
                <b>{recommendation}</b>
                <p>
                  {price == null
                    ? "Проверьте ограничения в отчёте"
                    : `${money(price)} за период`}
                </p>
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
                Открыть расчёт ↗
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
