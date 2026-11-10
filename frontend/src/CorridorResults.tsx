import { SourceLinks } from "./Assistant";
import type { CorridorReport, CorridorScheme } from "./assistantApi";
import { hours, money, num } from "./model";

const metrics = [
  { label: "Стоимость за период", value: (scheme: CorridorScheme) => <strong>{money(scheme.cost_rub)}</strong> },
  { label: "За тонну", value: (scheme: CorridorScheme) => money(scheme.rub_per_tonne) },
  { label: "Макс. срок доставки", value: (scheme: CorridorScheme) => hours(scheme.worst_delivery_hours) },
  { label: "Экономия к базе", value: (scheme: CorridorScheme) => money(scheme.cost_difference_vs_spot_rub) },
  { label: "Итог проверки", value: (scheme: CorridorScheme) => scheme.verdict },
];

export function CorridorResults({ report, onNew }: { report: CorridorReport; onNew: () => void }) {
  const recommended = report.schemes
    .filter((scheme) => scheme.feasible && scheme.cost_rub !== null)
    .sort((a, b) => (a.cost_rub ?? Infinity) - (b.cost_rub ?? Infinity))[0];

  return (
    <div className="results">
      <div className="page-title">
        <div>
          <h1>Результат расчёта</h1>
          <p className="muted">Сценарная проверка гипотез · {report.year} год</p>
        </div>
        <div className="title-actions">
          <button className="outline-button" onClick={onNew}>+ Новый расчёт</button>
        </div>
      </div>
      <details className="conditions-summary ticket-shadow">
        <summary>
          <b>Условия расчёта</b>
          <span>{report.origin} → {report.destination}</span>
          <span>{num(report.forecastTonnes)} т за {report.year} год</span>
          <span>Лимит {num(report.deadlineDays)} дн.</span>
          <span className="chevron" aria-hidden="true">⌄</span>
        </summary>
        <div className="conditions-expanded">
          <p>Объём распределён по сценарному графику. Срок отсчитывается от готовности каждой партии.</p>
        </div>
      </details>
      <h2 className="comparison-title">Сравнение схем</h2>
      <p className="mobile-scroll-hint">Таблицу можно прокручивать вбок →</p>
      <div className="comparison-frame">
        <div className="comparison-scroll">
          <table className="comparison-table" style={{ minWidth: `${190 + report.schemes.length * 260}px` }}>
            <caption className="sr-only">Сравнение стоимости, сроков и результатов проверки гипотез</caption>
            <colgroup>
              <col className="metric-col" />
              {report.schemes.map((scheme) => <col key={scheme.scheme} />)}
            </colgroup>
            <thead>
              <tr>
                <th scope="col"><span className="sr-only">Показатель</span></th>
                {report.schemes.map((scheme) => (
                  <th key={scheme.scheme} scope="col" className={`scheme-head ${scheme === recommended ? "recommended" : ""}`}>
                    <div><span className="scheme-badge">
                      {scheme === recommended ? "Дешевле всех в срок" : scheme.feasible ? "В срок" : "Есть ограничение"}
                    </span></div>
                    <b>{scheme.scheme}</b>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.label}>
                  <th scope="row">{metric.label}</th>
                  {report.schemes.map((scheme) => (
                    <td key={scheme.scheme} className={scheme === recommended ? "recommended" : ""}>{metric.value(scheme)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <details className="method-notes">
        <summary>Источники и допущения</summary>
        <div className="method-body">
          {report.assumptions.map((assumption) => <p key={assumption}>{assumption}</p>)}
          <SourceLinks sources={report.sources} />
        </div>
      </details>
    </div>
  );
}
