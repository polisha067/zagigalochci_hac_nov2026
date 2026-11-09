"""Render engine results without asking the language model to restate numbers"""

from backend.network.chukotavia import ChukotaviaResponse
from backend.network.models import NetworkResponse
from backend.network.pipeline import PipelineResponse

from .references import AIR_PATH, MODE_LABELS, PIPELINE_PATH, RIVER_PATH, number, pipeline_points, snapshot, source_ref


VAT = {"including_vat": "с НДС", "excluding_vat": "без НДС"}


def calculation_sources(result, engine):
    path = {
        "pilot": "backend/examples/catalog.json",
        "network": RIVER_PATH if getattr(result, "network_id", "").startswith("river-") else "backend/NETWORK.md",
        "russia": "backend/RUSSIA_NETWORK.md",
        "chukotavia": AIR_PATH,
        "pipeline": PIPELINE_PATH,
    }[engine]
    sources = result.sources
    if engine == "russia":
        used = {"source-01", "russia-freight-assumptions-v1"}
        for row in result.candidates:
            used.update(row["source_ids"])
            used.update(connection["reference_metadata"].get("source_id") for connection in row["connections"])
        sources = [source for source in sources if source["id"] in used]
    if engine == "pipeline" and result.known_components:
        ids = {row["source_id"] for row in result.known_components}
        sources = [source for source in sources if source["id"] in ids]
    return [source_ref(source, path) for source in sources]


def engine_summary(result):
    if isinstance(result, PipelineResponse):
        data = snapshot(PIPELINE_PATH)
        points = pipeline_points(data)
        route = next(row for row in data["routes"] if row["id"] == result.request["route_id"])
        direction = f"{points[route['origin']]['name']} → {points[route['destination']]['name']}"
        mass = number(result.request["net_weight_tonnes"])
        lines = [f"Товарная нефть, {direction}, {mass} т нетто."]
        for row in result.known_components:
            lines.append(f"Опубликованная часть стоимости — {number(row['amount_excluding_vat_rub'])} ₽ без НДС "
                         f"по ставке {number(row['rate_rub'])} ₽/т.")
        if not result.known_components:
            lines.append("По запрошенной дате опубликованный компонент не рассчитан.")
        return "\n".join([*lines, *result.warnings])

    if isinstance(result, ChukotaviaResponse):
        overview = result.overview
        lines = [f"ЧукотАВИА, {result.request['origin']} → {result.request['destination']}. "
                 f"Отправок — {overview['shipment_count']}, расчётный вес на отправку — "
                 f"{number(round(float(overview['chargeable_weight_kg_per_shipment']), 2))} кг."]
        shown = 0
        for shipment in result.shipments:
            candidates = shipment["candidates"]
            if not candidates:
                continue
            candidate = candidates[0]
            shown += 1
            line = f"Отправка {shipment['number']}, первый плановый вариант — {candidate['flight']}, {candidate['departure_at']}."
            cost = candidate["transport_component_estimate_rub"]
            if cost is not None:
                line += f" Тарифная составляющая — {number(cost)} ₽ {VAT[overview['cost_basis']]}."
            if candidate["scenario_delivery_hours"] is not None:
                line += f" Срок по заданному сценарию — {number(round(candidate['scenario_delivery_hours'], 2))} ч."
            lines.append(line)
            if shown == 3:
                break
        if not shown:
            lines.append("По условиям запроса плановые варианты в снимке отсутствуют.")
        assumptions = overview["assumptions"]
        if assumptions:
            lines.append("Допущения пользователя в часах — "
                         f"сдача за {number(assumptions['cutoff_hours'])}, обработка перед вылетом "
                         f"{number(assumptions['origin_processing_hours'])}, после прилёта "
                         f"{number(assumptions['destination_processing_hours'])}.")
        return "\n".join([*lines, *result.limitations])

    lines = [f"Сценарный расчёт. Отправок — {result.shipments}, вариантов — {len(result.candidates)}."]
    chosen = next((row for row in result.candidates if row["id"] == result.recommendation), None)
    if chosen:
        modes = " → ".join(MODE_LABELS[mode] for mode in chosen["modes"])
        lines.append(f"Рекомендация среди рассчитанных вариантов — {modes}, "
                     f"{number(chosen['total_cost_rub'])} ₽ {VAT[result.request['cost_basis']]}, "
                     f"максимальный срок {number(chosen['max_delivery_hours'])} ч.")
    else:
        lines.append("Среди рассчитанных вариантов подходящая схема не определена.")
    partial = next((row["published_tariff_estimate"] for row in result.candidates
                    if "published_tariff_estimate" in row), None)
    if partial:
        lines.append(f"Опубликованный компонент трубопровода в одном из вариантов — "
                     f"{number(partial['amount_excluding_vat_rub'])} ₽ без НДС.")
    if isinstance(result, NetworkResponse):
        lines.extend(result.assumptions)
    return "\n".join([*lines, *result.warnings])
