import { dashboardResult, type AssistantReply } from "./assistantApi";
import { candidateName, money, num } from "./model";

const statusNames = {
  feasible: "Допустим",
  infeasible: "Не подходит",
  insufficient_data: "Недостаточно данных",
};

export function AssistantData({ response }: { response: AssistantReply }) {
  const calculation = dashboardResult(response);
  if (!calculation) return null;
  const { candidates, request } = calculation;
  const preliminary = response.kind === "clarification" || response.kind === "calculation" && response.conditional;
  const vat = request.cost_basis === "including_vat" ? "с НДС" : "без НДС";
  const byMode = candidates.filter((candidate, index, rows) => rows.findIndex((row) =>
    row.modes.join() === candidate.modes.join() && row.status === candidate.status) === index);
  return byMode.slice(0, 4).map((candidate) => (
    <dl className="assistant-data" key={candidate.id}>
      <div><dt>Вариант</dt><dd>{candidateName(candidate)}</dd></div>
      <div><dt>Срок</dt><dd>{candidate.status === "feasible" && candidate.max_delivery_hours !== null
        ? num(candidate.max_delivery_hours, 2) + " ч" : statusNames[candidate.status]}</dd></div>
      <div>
        <dt>{preliminary ? "Предварительная цена" : "Стоимость"} {vat}</dt>
        <dd>{candidate.total_cost_rub !== null ? money(candidate.total_cost_rub)
          : candidate.known_cost_subtotal_rub != null ? money(candidate.known_cost_subtotal_rub) + " (известная часть)" : "—"}</dd>
      </div>
    </dl>
  ));
}
