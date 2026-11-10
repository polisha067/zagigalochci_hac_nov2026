"""Render engine results without asking the language model to restate numbers"""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from backend.network.chukotavia import ChukotaviaResponse
from backend.network.delivery_forecast import ForecastResponse
from backend.network.models import NetworkResponse
from backend.network.pipeline import PipelineResponse
from backend.network.russia import RussiaResponse
from backend.network.river_tariffs import river_catalog

from .references import AIR_PATH, MODE_LABELS, PIPELINE_PATH, RIVER_PATH, number, pipeline_points, snapshot, source_ref


VAT = {"including_vat": "с НДС", "excluding_vat": "без НДС"}


def _tariff_scope(row):
    for connection in row.get("connections", ()):
        metadata = connection["reference_metadata"]
        if metadata.get("scope") == "station_to_station":
            return (f"Для ж/д использован тариф ТЛМ на контейнер {metadata['container_size_ft']} футов "
                    "от станции до станции. Подвоз, доставка от станции и охрана оплачиваются отдельно, если требуются.")
    return None


def _expired_river_summary(result):
    if not isinstance(result, NetworkResponse):
        return None
    if not result.candidates or not all(
        any(issue["code"] == "expired_evidence" for issue in row["issues"])
        for row in result.candidates
    ):
        return None

    data = river_catalog()
    if result.network_id != data["id"]:
        return None
    hubs = {hub["id"]: hub["city"] for hub in data["hubs"]}
    origin = hubs.get(result.request.get("origin_hub_id"))
    destination = hubs.get(result.request.get("destination_hub_id"))
    direction = f" для маршрута {origin} → {destination}" if origin and destination else ""
    services = {identifier for row in result.candidates for identifier in row["service_ids"]}
    evidence = [service["evidence"] for service in data["services"] if service["id"] in services]
    if not evidence:
        return None
    start = min(datetime.fromisoformat(item["valid_from"]) for item in evidence)
    end = max(datetime.fromisoformat(item["valid_until"]) for item in evidence) - timedelta(microseconds=1)
    if start.year == end.year:
        period = f"только навигацию {start.year} года"
    else:
        period = f"период с {start:%d.%m.%Y} по {end:%d.%m.%Y}"
    return (f"Речной снимок{direction} покрывает {period}. "
            "Для выбранных дат в снимке нет действующих условий перевозки, поэтому цену и срок рассчитать нельзя. "
            "Для сравнения контейнеров по этому снимку укажите дату в пределах этой навигации.")


def calculation_sources(result, engine):
    path = {
        "pilot": "backend/examples/catalog.json",
        "network": RIVER_PATH if getattr(result, "network_id", "").startswith("river-") else "docs/calculations/network/NETWORK.md",
        "russia": "docs/calculations/RUSSIA_NETWORK.md",
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
    return [source_ref(source, source.get("path") or path) for source in sources]


def forecast_summary(result):
    forecast = result.forecast
    rows = result.candidates
    chosen = next((row for row in rows if row["id"] == result.recommendation), None)
    header = f"{result.request['origin']} → {result.request['destination']}, {forecast['target_year']} год"
    if chosen is None:
        return "\n\n".join([
            header,
            "Для этого маршрута и условий не удалось рассчитать полную стоимость ни одного варианта.",
            "В результатах расчёта указано, где не хватило данных или какие условия помешали доставке.",
        ])

    def price(value, row):
        step = 1000 if row["forecast_price"]["long_term"] else 100
        amount = Decimal(str(value))
        rounded = (amount / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step if amount >= step else amount
        return f"{number(rounded)} ₽"

    def name(row):
        labels = {"road": "машина", "rail": "поезд", "air": "самолёт", "river": "судно по реке", "sea": "судно по морю"}
        return " → ".join(labels.get(mode, MODE_LABELS[mode]) for mode in row["modes"])

    def duration(row):
        if row["max_delivery_hours"] is None:
            return "срок доставки неизвестен"
        days, hours = divmod(round(row["max_delivery_hours"]), 24)
        parts = ([f"{days} дн."] if days else []) + ([f"{hours} ч"] if hours or not days else [])
        return f"срок доставки около {' '.join(parts)}"

    budget = chosen["forecast_price"]
    lines = [
        header,
        f"Дешевле всего из рассчитанных вариантов — {name(chosen)}. "
        f"Около {price(chosen['total_cost_rub'], chosen)} {VAT[result.request['cost_basis']]}, {duration(chosen)}.",
    ]
    if scope := _tariff_scope(chosen):
        lines.append(scope)
    if budget["lower_rub"] is not None:
        lines.append(f"Ориентир для бюджета — от {price(budget['lower_rub'], chosen)} до {price(budget['upper_rub'], chosen)}.")
    else:
        lines.append("Для выбранного года можно дать только примерную сумму без диапазона цен.")
    if alternative := forecast["alternative"]:
        other = next(row for row in rows if row["id"] == alternative["id"])
        lines.append(f"Другой вариант — {name(other)}, около {price(other['total_cost_rub'], other)}, {duration(other)}.")
        if alternative["ranges_overlap"]:
            lines.append("Диапазоны цен пересекаются. Фактическая цена может поменять порядок вариантов.")
    lines.append("Цена рассчитана по ставкам проекта с учётом того, как менялись тарифы по данным Росстата. "
                 "Перед отправкой уточните цену и срок у перевозчика.")
    return "\n\n".join(lines)


def engine_summary(result):
    if isinstance(result, ForecastResponse):
        return forecast_summary(result)
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

    lines = []
    chosen = next((row for row in result.candidates if row["id"] == result.recommendation), None)
    if chosen:
        modes = " → ".join(MODE_LABELS[mode] for mode in chosen["modes"])
        lines.append(f"Рекомендованный вариант — {modes}. "
                     f"Примерная стоимость — {number(chosen['total_cost_rub'])} ₽ {VAT[result.request['cost_basis']]}. "
                     f"Самая долгая доставка займёт около {number(chosen['max_delivery_hours'])} ч.")
        if isinstance(result, RussiaResponse):
            if scope := _tariff_scope(chosen):
                lines.append(scope)
            lines.append("Цену, срок и наличие мест уточните у перевозчика. "
                         "Другие варианты и подробности доступны в результатах расчёта.")
            return "\n\n".join(lines)
    else:
        if coverage := _expired_river_summary(result):
            return coverage
        if not result.candidates and isinstance(result, RussiaResponse):
            return "По заданным условиям не удалось найти маршрут. Проверьте даты и выбранные виды транспорта."
        lines.append("Среди рассчитанных вариантов подходящих для ваших условий не нашлось.")
        partial = next((row for row in result.candidates if row.get("known_cost_subtotal_rub")), None)
        if partial:
            lines.append(f"По опубликованным ставкам рассчитана часть стоимости — "
                         f"{number(partial['known_cost_subtotal_rub'])} ₽ {VAT[result.request['cost_basis']]}.")
        reasons = dict.fromkeys(issue["message"] for row in result.candidates for issue in row["issues"])
        lines.extend(f"Причина: {message.rstrip('.')}." for message in list(reasons)[:3])
    partial = next((row["published_tariff_estimate"] for row in result.candidates
                    if "published_tariff_estimate" in row), None)
    if partial:
        lines.append(f"В одном из вариантов известна цена только за перевозку по трубопроводу — "
                     f"{number(partial['amount_excluding_vat_rub'])} ₽ без НДС.")
    if isinstance(result, NetworkResponse):
        lines.extend(result.assumptions)
        lines.extend(result.warnings)
    return "\n\n".join(lines)
