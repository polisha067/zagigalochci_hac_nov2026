import { dashboardResult, type AssistantReply } from "./assistantApi";
import { candidateName, money, num } from "./model";
import { PublishedCoverage, RoadMarketEstimate } from "./PublishedCoverage";

const statusNames = {
  feasible: "Допустим",
  infeasible: "Не подходит",
  insufficient_data: "Недостаточно данных",
};

export function AssistantData({ response }: { response: AssistantReply }) {
  const calculation = dashboardResult(response);
  if (!calculation) return null;
  if (calculation.published_coverage) return <>
    <RoadMarketEstimate result={calculation} />
    <PublishedCoverage result={calculation} compact />
  </>;
  const { candidates, request } = calculation;
  const preliminary = response.kind === "clarification" || response.kind === "calculation" && response.conditional;
  const vat = request.cost_basis === "including_vat" ? "с НДС" : "без НДС";
  const byMode = candidates.filter((candidate, index, rows) => rows.findIndex((row) =>
    row.modes.join() === candidate.modes.join() && row.status === candidate.status) === index);
  const air = byMode.find((candidate) => candidate.forecast_price?.air_median_rub != null)
    ?? byMode.find((candidate) => candidate.modes.includes("air"));
  const preview = byMode.slice(0, 4);
  if (air && !preview.includes(air)) preview[preview.length - 1] = air;
  return <><RoadMarketEstimate result={calculation} />{preview.map((candidate) => (
    <dl className="assistant-data" key={candidate.id}>
      <div><dt>Вариант</dt><dd>{candidateName(candidate)}</dd></div>
      <div><dt>Срок</dt><dd>{candidate.status === "feasible" && candidate.max_delivery_hours !== null
        ? num(candidate.max_delivery_hours, 2) + " ч" : statusNames[candidate.status]}</dd></div>
      {candidate.total_cost_rub == null && candidate.forecast_price?.air_median_rub != null ? (
        <>
          {candidate.multimodal && candidate.known_cost_subtotal_rub != null && (
            <div><dt>Учтённые этапы {vat}</dt><dd>{money(candidate.known_cost_subtotal_rub)}</dd></div>
          )}
          <div><dt>Перелёт и авианакладная {vat}</dt><dd>{money(candidate.forecast_price.air_median_rub)}</dd></div>
        </>
      ) : (
        <div>
          <dt>{preliminary ? "Предварительная цена" : "Стоимость"} {vat}</dt>
          <dd>{candidate.total_cost_rub !== null ? money(candidate.total_cost_rub)
            : candidate.known_cost_subtotal_rub != null ? money(candidate.known_cost_subtotal_rub) + " (известная часть)" : "—"}</dd>
        </div>
      )}
      {candidate.forecast_price?.air_softened_rub != null && (
        <div><dt>Авиатариф с ослаблением роста</dt><dd>{money(candidate.forecast_price.air_softened_rub)}</dd></div>
      )}
    </dl>
  ))}</>;
}
