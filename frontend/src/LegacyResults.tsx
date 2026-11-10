import {
  Fragment,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import type { Calculation, Scheme } from "./api";
import {
  date,
  hours,
  money,
  num,
  routeName,
  scheduleName,
  schemeName,
  schemeStatus,
  type Snapshot,
} from "./legacyModel";
import { TransportIcon } from "./ui";
const componentNames: Record<string, string> = {
  transport: "Перевозка",
  intermediate_stops: "Промежуточные остановки",
  cargo_handling: "Обработка груза",
  fixed_contract: "Фиксированная плата по договору",
  first_mile: "Подвоз до станции",
  last_mile: "Доставка от станции",
  rail: "Железная дорога",
  terminals: "Терминалы",
  accumulation_storage: "Хранение при накоплении",
  terminal_storage: "Хранение на терминале",
};
function Savings({ scheme }: { scheme: Scheme }) {
  return (
    <>
      {money(scheme.savings_vs_base_rub)}
      {scheme.savings_vs_base_percent != null && (
        <> · {num(scheme.savings_vs_base_percent)} %</>
      )}
    </>
  );
}
function Trips({ s }: { s: Scheme }) {
  if (s.scheme === "road_rail_road")
    return <>{s.departures == null ? "—" : `${num(s.departures)} парт.`}</>;
  return (
    <>
      {s.physical_trips == null ? "—" : `${num(s.physical_trips)} рейсов`}
      {s.paid_trips != null && s.paid_trips !== s.physical_trips && (
        <small>Оплачивается: {num(s.paid_trips)}</small>
      )}
    </>
  );
}
export function Sources({ r, ids }: { r: Calculation; ids?: string[] }) {
  const sources = ids ? r.sources.filter((s) => ids.includes(s.id)) : r.sources;
  return (
    <div className="sources-grid">
      <section>
        <h3>Источники данных</h3>
        {sources.length ? (
          sources.map((s) => (
            <div className="source-item" key={s.id}>
              <b>
                {s.status === "synthetic" ? "Демонстрационный набор" : s.id}
              </b>
              {s.date && <span className="muted"> · {date(s.date)}</span>}
              <p>{s.description}</p>
              <small>{s.id}</small>
            </div>
          ))
        ) : (
          <p className="muted">Источники для этой схемы не указаны.</p>
        )}
        <p className="muted">
          Набор: {r.dataset_id} · Версия расчёта: {r.schema_version}
        </p>
      </section>
      <section>
        <h3>Допущения модели</h3>
        <ul>
          {r.assumptions.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
function PlanTable({ r }: { r: Calculation }) {
  return (
    <div className="table-scroll">
      <table className="detail-table">
        <caption>Плановый объём по неделям</caption>
        <thead>
          <tr>
            <th>Неделя с</th>
            <th>Отправок</th>
            <th>Объём, т</th>
          </tr>
        </thead>
        <tbody>
          {r.volume_by_week.map((w) => (
            <tr key={w.week_start}>
              <th>{date(w.week_start)}</th>
              <td>{num(w.shipments)}</td>
              <td>{num(w.tonnes, 6)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th>Итого</th>
            <td>{num(r.shipments)}</td>
            <td>{num(r.total_volume_tonnes, 6)}</td>
          </tr>
        </tfoot>
      </table>
      <p className="table-note">
        Недели планового объёма — по московскому времени. Это план поставок, не
        прогноз по истории.
      </p>
    </div>
  );
}
function Conditions({ snapshot }: { snapshot: Snapshot }) {
  const r = snapshot.result;
  return (
    <details className="conditions-summary ticket-shadow">
      <summary>
        <b>Условия расчёта</b>
        <span>{routeName(r)}</span>
        <span>
          {num(r.total_volume_tonnes, 6)} т · {r.shipments} отправок
        </span>
        <span>Лимит {hours(r.request.max_delivery_hours)}</span>
      </summary>
      <div className="conditions-expanded">
        <div className="facts">
          <div>
            <small>График</small>
            <b>{scheduleName(r)}</b>
          </div>
          <div>
            <small>Начало, МСК</small>
            <b>{date(r.planning_period.start_at, true)}</b>
          </div>
          <div>
            <small>Конец периода, МСК</small>
            <b>{date(r.planning_period.end_at, true)}</b>
            {r.planning_period.end_exclusive && (
              <small>Правая граница не включается</small>
            )}
          </div>
          <div>
            <small>Проверка вместимости</small>
            <b>
              {r.request.capacity_basis === "mass_only"
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
                <th>Вес, т</th>
                <th>Погрузка → выгрузка</th>
                <th>Делимый</th>
              </tr>
            </thead>
            <tbody>
              {r.request.cargoes.map((c) => (
                <tr key={c.id}>
                  <th>{snapshot.cargoNames[c.cargo_type] || c.cargo_type}</th>
                  <td>{num(c.weight_tonnes, 6)}</td>
                  <td>
                    {r.request.route[c.load_at]?.city} →{" "}
                    {
                      r.request.route[c.unload_at ?? r.request.route.length - 1]
                        ?.city
                    }
                  </td>
                  <td>{c.splittable ? "Да" : "Нет"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted">
          Обработка в пунктах:{" "}
          {r.request.route
            .map((p) => `${p.city} — ${hours(p.service_hours)}`)
            .join("; ")}
          .
        </p>
      </div>
    </details>
  );
}
function Report({
  s,
  snapshot,
  bridgeX,
  bridgeVisible,
}: {
  s: Scheme;
  snapshot: Snapshot;
  bridgeX: number;
  bridgeVisible: boolean;
}) {
  const r = snapshot.result;
  const components = Object.entries(s.breakdown_rub);
  const base = r.schemes.find((v) => v.scheme === "spot_road");
  const rate =
    s.paid_trips != null &&
    s.paid_trips > 0 &&
    s.breakdown_rub.transport != null
      ? s.breakdown_rub.transport / s.paid_trips
      : null;
  const transportOnly = components.every(
    ([key, value]) => key === "transport" || value === 0,
  );
  return (
    <section
      id="scheme-report"
      className="scheme-report ticket-shadow"
      aria-label={"Подробный расчёт: " + schemeName(s.scheme)}
      style={
        {
          "--bridge-x": `${bridgeX}px`,
          "--bridge-display": bridgeVisible ? "block" : "none",
        } as CSSProperties
      }
    >
      <svg className="report-bridge" viewBox="0 0 72 24" aria-hidden="true">
        <path d="M0 24C25 24 23 0 36 0S47 24 72 24Z" />
      </svg>
      <div className="report-card perforated">
        <div className="report-title">
          <h2>Подробный расчёт</h2>
          <h3>{schemeName(s.scheme)}</h3>
        </div>
        {s.status !== "feasible" && (
          <div className="notice warning">
            <b>{schemeStatus(s, r.request.max_delivery_hours)}</b>
            <p>{s.reason}</p>
            {s.missing_fields.length > 0 && (
              <p>Недостающие данные: {s.missing_fields.join(", ")}</p>
            )}
          </div>
        )}
        <div className="report-total">
          <div>
            <small>
              {transportOnly && rate != null
                ? "Стоимость перевозки за период"
                : "Полная стоимость за период"}
            </small>
            <strong>
              {transportOnly && rate != null
                ? `${num(s.paid_trips)} × ${money(rate)} = ${money(s.total_cost_rub)}`
                : money(s.total_cost_rub)}
            </strong>
          </div>
          <div>
            <small>За тонну</small>
            <strong>
              {money(s.cost_rub_per_tonne)}
              {s.cost_rub_per_tonne != null && "/т"}
            </strong>
          </div>
        </div>
        <div className="report-metrics">
          <p>
            Доставка за <b>{hours(s.max_delivery_hours)}</b>
            <span className="muted">
              {" "}
              при лимите {hours(r.request.max_delivery_hours)}
            </span>
          </p>
          <p>
            {s.savings_vs_base_rub != null && s.savings_vs_base_rub < 0
              ? "Удорожание к базе: "
              : "Экономия к базе: "}
            <b>
              <Savings scheme={s} />
            </b>
          </p>
        </div>
        {components.length > 0 && (
          <section className="report-section">
            <h3>Из чего складывается стоимость</h3>
            <div className="cost-components">
              {components.map(([key, value]) => (
                <div key={key}>
                  <span>{componentNames[key] || key}</span>
                  <b>{money(value)}</b>
                </div>
              ))}
              <div className="cost-total">
                <span>Итого</span>
                <b>{money(s.total_cost_rub)}</b>
              </div>
            </div>
            <p className="formula">
              {components
                .filter(([, v]) => v !== 0)
                .map(([, v]) => money(v))
                .join(" + ") || money(0)}{" "}
              = {money(s.total_cost_rub)}
            </p>
            {rate != null && (
              <p className="table-note">
                {num(s.paid_trips)} оплачиваемых рейсов × {money(rate)} ={" "}
                {money(s.breakdown_rub.transport)}. Стоимость одного рейса
                вычислена из транспортной части ответа сервера.
              </p>
            )}
          </section>
        )}
        {s.details.billing_weeks && (
          <section className="report-section">
            <div className="table-scroll">
              <table className="detail-table">
                <caption>Разбивка оплаты по неделям</caption>
                <thead>
                  <tr>
                    <th>Неделя с</th>
                    <th>Фактические рейсы</th>
                    <th>Оплачиваемые рейсы</th>
                    <th>Перевозка, ₽</th>
                  </tr>
                </thead>
                <tbody>
                  {s.details.billing_weeks.map((w) => (
                    <tr key={w.week_start}>
                      <th>{date(w.week_start)}</th>
                      <td>{num(w.actual_trips)}</td>
                      <td>{num(w.paid_trips)}</td>
                      <td>{rate == null ? "—" : money(w.paid_trips * rate)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <th>Итого</th>
                    <td>{num(s.physical_trips)}</td>
                    <td>{num(s.paid_trips)}</td>
                    <td>{money(s.breakdown_rub.transport)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
            <p className="table-note">
              Стоимость перевозки за неделю = оплачиваемые рейсы × расчётная
              стоимость рейса. Дополнительные расходы приведены отдельно выше;
              они не распределены по неделям. Недели оплаты взяты из ответа
              сервера и могут отличаться от недель готовности груза.
            </p>
          </section>
        )}
        {s.details.batches && (
          <section className="report-section">
            <div className="table-scroll">
              <table className="detail-table">
                <caption>Укрупнённые ж/д партии</caption>
                <thead>
                  <tr>
                    <th>Партия</th>
                    <th>Отправок / тонн</th>
                    <th>Ж/д единиц</th>
                    <th>Готовность, МСК</th>
                    <th>Отправление ж/д, МСК</th>
                    <th>Доставка, МСК</th>
                    <th>Макс. срок</th>
                  </tr>
                </thead>
                <tbody>
                  {s.details.batches.map((b, i) => (
                    <tr key={i}>
                      <th>{i + 1}</th>
                      <td>
                        {num(b.shipments)} / {num(b.weight_tonnes, 6)}
                      </td>
                      <td>{num(b.units)}</td>
                      <td>{date(b.ready_at, true)}</td>
                      <td>{date(b.rail_departure_at, true)}</td>
                      <td>{date(b.delivered_at, true)}</td>
                      <td>{hours(b.max_delivery_hours)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="table-note">
              Авторейсов подвоза и развоза суммарно: {num(s.physical_trips)}.
              Это отдельный показатель, он не равен числу ж/д партий.
            </p>
            <details>
              <summary>Машины подвоза и развоза по партиям</summary>
              <ul>
                {s.details.batches.map((b, i) => (
                  <li key={i}>
                    Партия {i + 1}: подвоз — {num(b.first_mile_vehicles)},
                    развоз — {num(b.last_mile_vehicles)}.
                  </li>
                ))}
              </ul>
            </details>
          </section>
        )}
        {s.details.cargo_delivery_hours && (
          <section className="report-section">
            <h3>Срок по каждому грузу</h3>
            <div className="cost-components">
              {r.request.cargoes.map((c) => (
                <div key={c.id}>
                  <span>
                    {snapshot.cargoNames[c.cargo_type] || c.cargo_type} ·{" "}
                    {num(c.weight_tonnes, 6)} т
                  </span>
                  <b>{hours(s.details.cargo_delivery_hours?.[c.id])}</b>
                </div>
              ))}
            </div>
            <p className="table-note">
              От готовности груза до завершения обработки в пункте выгрузки.
              Машин на отправку: {num(s.details.vehicles_per_departure)}.
            </p>
          </section>
        )}
        <section className="report-section">
          <PlanTable r={r} />
        </section>
        {base &&
          s.scheme !== "spot_road" &&
          base.total_cost_rub != null &&
          s.total_cost_rub != null &&
          s.savings_vs_base_rub != null && (
            <section className="report-section">
              <h3>
                {s.savings_vs_base_rub < 0
                  ? "Как получилось удорожание"
                  : "Как получилась экономия"}
              </h3>
              <p className="formula">
                {money(base.total_cost_rub)} − {money(s.total_cost_rub)} ={" "}
                {money(s.savings_vs_base_rub)}
              </p>
              <p className="muted">
                База — разовые автозаказы при тех же условиях.{" "}
                {s.savings_vs_base_rub < 0
                  ? "Отрицательная экономия означает удорожание."
                  : ""}
              </p>
            </section>
          )}
        <section className="report-section">
          <Sources r={r} ids={s.source_ids} />
        </section>
        <button
          className="text-button"
          type="button"
          onClick={() =>
            document
              .getElementById("comparison")
              ?.scrollIntoView({ behavior: "smooth", block: "start" })
          }
        >
          ↑ К сравнению схем
        </button>
      </div>
    </section>
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
  const r = snapshot.result;
  const [openScheme, setOpenScheme] = useState<string | null>(null);
  const [bridgeX, setBridgeX] = useState(0);
  const [bridgeVisible, setBridgeVisible] = useState(false);
  const frame = useRef<HTMLDivElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const selectedCell = useRef<HTMLTableCellElement>(null);
  const selected = r.schemes.find((s) => s.scheme === openScheme);
  useLayoutEffect(() => {
    const update = () => {
      if (!frame.current || !selectedCell.current) return;
      const outer = frame.current.getBoundingClientRect();
      const cell = selectedCell.current.getBoundingClientRect();
      const x = cell.left + cell.width / 2 - outer.left;
      setBridgeX(x);
      setBridgeVisible(x >= 36 && x <= outer.width - 36);
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
  }, [openScheme]);
  const metrics = [
    {
      label: "Стоимость за период",
      cell: (s: Scheme) => <strong>{money(s.total_cost_rub)}</strong>,
    },
    { label: "За тонну", cell: (s: Scheme) => money(s.cost_rub_per_tonne) },
    {
      label: "Срок доставки",
      cell: (s: Scheme) => (
        <>
          {hours(s.max_delivery_hours)}
          {s.max_delivery_hours != null &&
            s.max_delivery_hours > r.request.max_delivery_hours && (
              <small className="over-limit">
                +{hours(s.max_delivery_hours - r.request.max_delivery_hours)}
              </small>
            )}
        </>
      ),
    },
    {
      label: "Экономия к базе",
      cell: (s: Scheme) =>
        s.scheme === "spot_road" ? "—" : <Savings scheme={s} />,
    },
    { label: "Рейсы / партии", cell: (s: Scheme) => <Trips s={s} /> },
  ];
  return (
    <div className="results">
      {saved && (
        <button className="text-button back-link" onClick={onBack}>
          ← Вернуться к истории
        </button>
      )}
      <div className="page-title">
        <div>
          <h1>{saved ? "Сохранённый расчёт" : "Результат расчёта"}</h1>
          <p className="muted">
            {saved && `${date(snapshot.createdAt, true)} · `}
            {r.data_status === "synthetic"
              ? "Демо · синтетические тарифы"
              : "Расчёт по данным сервера"}
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
        Сравнение схем
      </h2>
      <p className="mobile-scroll-hint">Таблицу можно прокручивать вбок →</p>
      <div ref={frame} className="comparison-frame">
        <div ref={scroll} className="comparison-scroll">
          <table
            className="comparison-table"
            style={{ minWidth: `${190 + r.schemes.length * 260}px` }}
          >
            <caption className="sr-only">
              Сравнение стоимости, сроков и количества рейсов
            </caption>
            <colgroup>
              <col className="metric-col" />
              {r.schemes.map((s) => (
                <col key={s.scheme} />
              ))}
            </colgroup>
            <thead>
              <tr>
                <th scope="col">
                  <span className="sr-only">Показатель</span>
                </th>
                {r.schemes.map((s) => (
                  <th
                    key={s.scheme}
                    scope="col"
                    className={
                      s.scheme === r.recommendation
                        ? "recommended scheme-head"
                        : "scheme-head"
                    }
                  >
                    <div>
                      <span className="scheme-badge">
                        {s.scheme === r.recommendation
                          ? "Рекомендуем"
                          : s.status !== "feasible"
                            ? schemeStatus(s, r.request.max_delivery_hours)
                            : s.scheme === "spot_road"
                              ? "База"
                              : "Соответствует условиям"}
                      </span>
                      <TransportIcon
                        rail={s.scheme === "road_rail_road"}
                        double={s.scheme === "contract_road"}
                      />
                    </div>
                    <b>{schemeName(s.scheme)}</b>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m.label}>
                  <th scope="row">{m.label}</th>
                  {r.schemes.map((s) => (
                    <td
                      key={s.scheme}
                      className={
                        s.scheme === r.recommendation ? "recommended" : ""
                      }
                    >
                      {m.cell(s)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td />
                {r.schemes.map((s) => (
                  <td
                    key={s.scheme}
                    ref={s.scheme === openScheme ? selectedCell : undefined}
                    className={`${s.scheme === r.recommendation ? "recommended" : ""} ${s.scheme === openScheme ? "expanded-scheme" : ""}`}
                  >
                    <button
                      className="text-button"
                      aria-expanded={s.scheme === openScheme}
                      aria-controls={
                        s.scheme === openScheme ? "scheme-report" : undefined
                      }
                      aria-label={`${s.scheme === openScheme ? "Свернуть" : "Подробнее"}: ${schemeName(s.scheme)}`}
                      onClick={() =>
                        setOpenScheme((old) =>
                          old === s.scheme ? null : s.scheme,
                        )
                      }
                    >
                      {s.scheme === openScheme ? "Свернуть" : "Подробнее"}
                    </button>
                    {s.scheme === openScheme && (
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
          <Report
            s={selected}
            snapshot={snapshot}
            bridgeX={bridgeX}
            bridgeVisible={bridgeVisible}
          />
        )}
      </div>
      <div className="result-notices">
        {!r.recommendation && (
          <div className="notice warning">
            <b>Нет подходящей рекомендации</b>
            <p>
              Проверьте причины ограничений схем или измените условия перевозки.
            </p>
          </div>
        )}
        {r.schemes
          .filter((s) => s.status !== "feasible")
          .map((s) => (
            <div className="notice warning" key={s.scheme}>
              <b>{schemeName(s.scheme)}</b>
              <p>{s.reason}</p>
              {s.missing_fields.length > 0 && (
                <small>Не хватает данных: {s.missing_fields.join(", ")}</small>
              )}
            </div>
          ))}
        <p className="muted">{r.recommendation_scope}</p>
      </div>
      <details className="method-notes">
        <summary>Источники, предупреждения и допущения</summary>
        <div className="method-body">
          {r.warnings.length > 0 && (
            <section>
              <h3>Что учитывать при выборе</h3>
              <ul>
                {r.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </section>
          )}
          <Sources r={r} />
          <details>
            <summary>Загрузка участков маршрута</summary>
            <div className="table-scroll">
              <table className="detail-table">
                <thead>
                  <tr>
                    <th>Участок</th>
                    <th>Масса, т</th>
                    <th>Объём, м³</th>
                  </tr>
                </thead>
                <tbody>
                  {r.load_profile.map((p) => (
                    <tr key={p.leg}>
                      <th>
                        {p.origin} → {p.destination}
                      </th>
                      <td>{num(p.weight_tonnes, 6)}</td>
                      <td>{num(p.volume_m3, 6)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      </details>
      {r.warnings.length > 0 && (
        <p className="result-disclaimer">{r.warnings[0]}</p>
      )}
    </div>
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
    `${routeName(s.result)} ${s.result.request.cargoes.map((c) => s.cargoNames[c.cargo_type] || c.cargo_type).join(" ")}`
      .toLocaleLowerCase("ru")
      .includes(search.trim().toLocaleLowerCase("ru")),
  );
  let previousDate = "";
  return (
    <div className="history-page">
      <div className="page-title">
        <div>
          <h1>История расчётов</h1>
          <p className="muted">
            Сохранено в этом браузере · только для вашего аккаунта
          </p>
        </div>
        <button className="outline-button" onClick={onNew}>
          + Новый расчёт
        </button>
      </div>
      <label className="history-search">
        <span className="sr-only">Поиск по маршруту или грузу</span>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          aria-hidden="true"
        >
          <circle cx="10" cy="10" r="7" />
          <path d="m15 15 6 6" />
        </svg>
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
              : "Задайте условия перевозки. Завершённый расчёт автоматически появится в истории."}
          </p>
          {!entries.length && (
            <button className="primary" onClick={onNew}>
              Начать расчёт →
            </button>
          )}
        </div>
      )}
      {filtered.map((snapshot) => {
        const r = snapshot.result;
        const day = date(snapshot.createdAt);
        const showDay = day !== previousDate;
        previousDate = day;
        const recommended = r.schemes.find(
          (s) => s.scheme === r.recommendation,
        );
        return (
          <Fragment key={snapshot.id}>
            {showDay && <h2 className="history-date">{day}</h2>}
            <article className="history-ticket perforated ticket-shadow">
              <div>
                <h3>{routeName(r)}</h3>
                <p>
                  {r.request.cargoes
                    .map(
                      (c) =>
                        `${snapshot.cargoNames[c.cargo_type] || c.cargo_type} · ${num(c.weight_tonnes, 6)} т`,
                    )
                    .join("; ")}
                </p>
                <p className="muted">
                  {r.shipments} отправок · {num(r.total_volume_tonnes, 6)} т за
                  период
                </p>
              </div>
              <div className="history-recommendation">
                <small>Рекомендация на момент расчёта</small>
                <b>
                  {recommended
                    ? schemeName(recommended.scheme)
                    : "Нет подходящей схемы"}
                </b>
                <p>
                  {recommended
                    ? `${money(recommended.total_cost_rub)} за период`
                    : "Посмотрите ограничения в отчёте"}
                </p>
                <small>
                  {r.data_status === "synthetic"
                    ? "Демо · синтетические тарифы"
                    : "Данные сервера"}
                </small>
              </div>
              <time dateTime={snapshot.createdAt}>
                {new Intl.DateTimeFormat("ru-RU", {
                  hour: "2-digit",
                  minute: "2-digit",
                  timeZone: "Europe/Moscow",
                }).format(new Date(snapshot.createdAt))}
              </time>
              <button
                className="history-open text-button"
                onClick={() => onOpen(snapshot)}
              >
                Открыть расчёт ↗
              </button>
            </article>
          </Fragment>
        );
      })}
      <p className="history-footnote">
        История не синхронизируется между устройствами. Очистка данных сайта в
        браузере удалит сохранённые расчёты.
      </p>
    </div>
  );
}
