import { useState, type FormEvent } from "react";
import { post } from "./api";
import { hours, money } from "./model";
import type { Calculation, ConnectionPilot as Pilot } from "./networkApi";

const fields = [
  ["buffer_hours", "Запас до закрытия окна, ч", true],
  ["delay_hours", "Задержка готовности груза, ч", true],
  ["next_slot_hours", "Подтверждённое окно, ч от плановой подачи (необязательно)", false],
  ["free_truck_wait_hours", "Бесплатное ожидание машины, ч", true],
  ["free_storage_hours", "Бесплатное хранение с готовности груза, ч", true],
  ["transfer_rub", "Перегрузка, ₽ за партию", false],
  ["truck_wait_rub_per_hour", "Простой, ₽ за час", false],
  ["storage_rub_per_day", "Хранение, ₽ за начатые сутки", false],
  ["rebooking_rub", "Повторная подача, ₽", false],
] as const;

const components: Record<string, string> = {
  transfer: "перегрузка",
  truck_wait: "простой машины",
  storage: "хранение",
  rebooking: "повторная подача",
};
const labels = {
  planned: "По плану",
  within_buffer: "Задержка в пределах запаса",
  requested: "Заданная задержка",
  pickup_after_6h: "Сценарий: забор через 6 ч после готовности",
  pickup_after_24h: "Сценарий: забор через 24 ч после готовности",
};

const marketRateLabels: Record<string, string> = {
  transfer_rub: "Перегрузка партии",
  truck_wait_rub_per_hour: "Простой, за час",
  storage_rub_per_day: "Хранение, за платные сутки",
};

export function ConnectionPilot({ calculation, candidateId, initial }: {
  calculation: Calculation;
  candidateId: string;
  initial: Pilot;
}) {
  const [result, setResult] = useState(initial);
  const [values, setValues] = useState(initial.conditions);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const tax = calculation.request.cost_basis === "including_vat" ? "с НДС" : "без НДС";

  async function recalculate(conditions: typeof values) {
    setBusy(true);
    setError("");
    try {
      const next = await post<Calculation>("/network/russia/forecasts", {
        ...calculation.request,
        connection_conditions: Object.fromEntries(fields.map(([key]) => [key, conditions[key] || null])),
      });
      const pilot = next.candidates.find((candidate) => candidate.id === candidateId)?.connection_pilot;
      if (!pilot) throw new Error("Этот вариант стыковки больше недоступен. Обновите основной прогноз.");
      setResult(pilot);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось пересчитать стыковку.");
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void recalculate(values);
  }

  function applyMarketScenario() {
    const conditions = { ...values };
    for (const [key, rate] of Object.entries(result.market_budget?.rates ?? {})) {
      if (conditions[key] == null || conditions[key] === "") conditions[key] = String(rate.forecast_amount_rub);
    }
    setValues(conditions);
    void recalculate(conditions);
  }

  return (
    <section className="report-section connection-panel">
      <h3>Стыковка в Екатеринбурге</h3>
      <p>Два плеча по опубликованным ставкам · <b>{money(result.movement_rub)} {tax}</b>.</p>
      <p className="table-note">
        Отсчёт начинается от плановой подачи машины. Начальные 4 часа запаса и 6 часов задержки заданы для сценария.
        Станцию прибытия ТЛМ, доступность перегрузки муки и фактическое окно нужно подтвердить.
        {" "}<a href={result.terminal_reference_url}>Терминальные услуги</a>
      </p>
      <p className="table-note">
        «Логистик Авто» публикует ориентир {money(result.truck_10t_reference_rub)} с НДС для категории 10 т.
        Уточните пригодность машины для 20 м³ и условия забора с терминала.
        {result.truck_10t_forecast_rub != null && <> При подтверждении прогноз за автоплечо составит{" "}
          <b>{money(result.truck_10t_forecast_rub)} {tax}</b>.</>}
        {" "}<a href={result.truck_10t_reference_url}>Прайс перевозчика</a>
      </p>
      {result.market_budget && (
        <div className="connection-preset">
          <b>Рыночные ориентиры на {calculation.forecast?.target_year} год ({tax})</b>
          {Object.entries(result.market_budget.rates).map(([key, rate]) => (
            <p className="table-note" key={key}>
              {marketRateLabels[key]} · <b>{money(rate.forecast_amount_rub)}</b>.
              {" "}{rate.method} База {rate.base_year} года — {money(rate.base_amount_rub)}.
              {" "}<a href={rate.source_url}>{rate.source_name}</a>
            </p>
          ))}
          <div className="connection-actions">
            <button className="outline-button" type="button" disabled={busy} onClick={applyMarketScenario}>Заполнить пустые ставки</button>
          </div>
          <p className="table-note">
            Для расчёта после пропуска окна уточните стоимость повторной подачи и время следующей машины.
          </p>
        </div>
      )}
      <div className="table-scroll connection-table" aria-busy={busy}>
        <table>
          <thead><tr><th>Сценарий</th><th>Первое окно</th><th>К плановому забору</th><th>Два плеча и стыковка {tax}</th></tr></thead>
          <tbody>{result.scenarios.map((row) => (
            <tr key={row.scenario}>
              <th>{labels[row.scenario]} · {hours(row.delay_hours)}</th>
              <td>{row.missed
                ? `Пропущено на ${hours(-row.buffer_remaining_hours)}`
                : `Запас ${hours(row.buffer_remaining_hours)}`}</td>
              <td>{row.extra_hours == null ? "Время новой подачи неизвестно" : `+${hours(row.extra_hours)}`}</td>
              <td>
                {row.total_rub == null ? `Учтено ${money(row.known_rub)}` : money(row.total_rub)}
                {Object.entries(row.charges_rub).filter(([, amount]) => amount == null || amount > 0)
                  .map(([key, amount]) => (
                    <div key={key} className="table-note">
                      {components[key]}
                      {key === "truck_wait" && ` · ${hours(row.billable_truck_hours)}`}
                      {key === "storage" && row.billable_storage_days != null && ` · ${row.billable_storage_days} сут.`}
                      {amount == null ? " · стоимость неизвестна" : ` · ${money(amount)}`}
                    </div>
                  ))}
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="table-note">
        При пропуске первого окна варианты через 6 и 24 часа показывают последствия условной подачи машины после готовности груза.
      </p>
      {error && <p role="alert">{error}</p>}
      <details className="ledger-details">
        <summary>Задать условия стыковки и доплаты</summary>
        <p className="table-note">
          Указывайте суммы {tax} в ценах года расчёта. Пустая ставка означает «неизвестно», 0 — «оплаты нет».
          Для сценария простоя машина ждёт до закрытия окна, хранение оплачивается за начатые сутки после льготного периода.
        </p>
        <form onSubmit={submit}>
          <div className="connection-fields">
            {fields.map(([key, label, required]) => (
              <label key={key}>{label}
                <input type="number" min="0" max={key.endsWith("_hours") ? 720 : 100000000}
                  step="any" required={required} value={values[key] ?? ""}
                  onChange={(event) => setValues({ ...values, [key]: event.target.value })} />
              </label>
            ))}
          </div>
          <button className="outline-button" type="submit" disabled={busy}>{busy ? "Расчёт…" : "Пересчитать стыковку"}</button>
        </form>
      </details>
    </section>
  );
}
