import type { ReactNode } from "react";
import { hasDashboard, type AssistantCalculation, type AssistantReply, type DashboardCalculation, type ReferenceResult } from "./assistantApi";
import { candidateName, money, num } from "./model";

const statusNames: Record<string, string> = {
  feasible: "Допустим",
  infeasible: "Не подходит",
  insufficient_data: "Недостаточно данных",
};
const vat = (basis: string) => basis === "including_vat" ? "с НДС" : "без НДС";
const rubles = (value: number | null) => value === null ? "—" : money(value);
const hours = (value: number | null) => value === null ? "—" : num(value, 2);

function ReportTable({ columns, rows }: { columns: string[]; rows: ReactNode[][] }) {
  if (!rows.length) return null;
  return (
    <div className="table-scroll">
      <table className="detail-table">
        <thead><tr>{columns.map((title) => <th scope="col" key={title}>{title}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{row.map((value, cell) => <td key={cell}>{value}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CalculationData({ result }: { result: Exclude<AssistantCalculation, DashboardCalculation> }) {
  if (result.engine === "pipeline") {
    return <ReportTable
      columns={["Маршрут", "Масса нетто, т", "Ставка, ₽/т без НДС", "Опубликованная часть без НДС"]}
      rows={result.known_components.map((component) => [
        component.route_id, num(result.request.net_weight_tonnes, 6),
        num(Number(component.rate_rub), 4), money(component.amount_excluding_vat_rub),
      ])}
    />;
  }
  if (result.engine === "chukotavia") {
    return <ReportTable
      columns={["Отправка", "Рейс", "Плановый вылет", "Плановое прибытие", `Тарифная часть ${vat(result.overview.cost_basis)}`, "Сценарный срок, ч"]}
      rows={result.shipments.flatMap((shipment) => shipment.candidates.map((flight) => [
        shipment.number, flight.flight, flight.departure_at, flight.arrival_at,
        rubles(flight.transport_component_estimate_rub), hours(flight.scenario_delivery_hours),
      ]))}
    />;
  }
  return <ReportTable
    columns={["Схема", "Статус", `Сценарная стоимость ${vat(result.request.cost_basis)}`, "Макс. срок, ч"]}
    rows={result.candidates.map((candidate) => [
      candidateName(candidate),
      candidate.id === result.recommendation ? "Рекомендация" : statusNames[candidate.status],
      rubles(candidate.total_cost_rub), hours(candidate.max_delivery_hours),
    ])}
  />;
}

const referenceColumns: Record<string, [string, string][]> = {
  lookup_air_freight_tariff: [["origin", "Отправление"], ["destination", "Назначение"], ["rate_including_vat_rub_per_kg", "₽/кг с НДС"], ["rate_excluding_vat_rub_per_kg", "₽/кг без НДС"], ["valid_from", "Действует с"]],
  lookup_chukotavia_schedule: [["flight", "Рейс"], ["departure_at", "Плановый вылет"], ["arrival_at", "Плановое прибытие"]],
  lookup_connectivity: [["mode", "Транспорт"], ["distance_km", "Расстояние, км"], ["estimated_hours", "Оценка времени, ч"], ["missing_reason", "Примечание"]],
  lookup_fesco_voyages: [["id", "Рейс"], ["departure_date", "Дата отхода"], ["arrival_date", "Дата прибытия"]],
  lookup_river_scenario: [["id", "Вариант"], ["scenario_travel_hours", "Переход, ч"], ["scenario_unit_tonnes", "Лимит, т"], ["scenario_unit_m3", "Лимит, м³"]],
  lookup_pipeline_reference: [["origin_name", "Отправление"], ["destination_name", "Назначение"], ["point_id_name", "Пункт"], ["operator", "Оператор"], ["rate_rub", "Ставка, ₽ без НДС"], ["basis", "Единица ставки"], ["distance_km", "Справочная длина, км"]],
  lookup_rosmorport: [["name", "Порт"], ["basin", "Бассейн"], ["navigation_text", "Навигация"]],
  lookup_asop: [["category_name", "Категория"], ["source_value", "Подпись диаграммы"], ["value_tonnes", "Объём, т"]],
  lookup_transport_strategy: [["scenario", "Сценарий"], ["mode", "Транспорт"], ["year", "Год"], ["value_million_tonnes", "Объём, млн т"]],
  rank_regional_forecast: [["region", "Регион"], ["base_tonnes", "База, т"], ["forecast_tonnes", "Прогноз, т"], ["growth_tonnes", "Рост, т"], ["growth_percent", "Рост, %"]],
};
const valueLabels: Record<string, string> = {
  road: "Авто", rail: "Ж/д", air: "Авиа", river: "Внутренний водный",
  overall_excluding_pipeline: "Все виды без трубопроводного",
  baseline: "Базовый", conservative: "Консервативный",
  per_tonne: "т", per_100_tonnes: "100 т", per_100_tonne_km: "100 т·км",
};

function ReferenceData({ result, tool }: { result: ReferenceResult; tool: string | null }) {
  const columns = (referenceColumns[tool || ""] || []).filter(([field]) => result.rows.some((row) => row[field] != null));
  if (!columns.length) return null;
  return <ReportTable
    columns={columns.map(([, label]) => label)}
    rows={result.rows.map((row) => columns.map(([field]) => {
      const value = row[field];
      if (typeof value === "number") return field === "year" ? String(value) : num(value, 6);
      return typeof value === "string" ? valueLabels[value] || value : "—";
    }))}
  />;
}

export function AssistantData({ response }: { response: AssistantReply }) {
  if (response.kind === "information" && response.result) {
    return <ReferenceData result={response.result} tool={response.tool} />;
  }
  if (response.kind === "calculation" && !hasDashboard(response.calculation)) {
    return <CalculationData result={response.calculation} />;
  }
  return null;
}
