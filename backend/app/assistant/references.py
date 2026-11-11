"""Read the versioned project snapshots without turning observations into services"""

from datetime import date
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.a1_corridors import MAIN_DEADLINE_DAYS, compare_corridor, pilot_reference
from backend.a1_forecast import Granularity, MAX_FORECAST_YEAR, MIN_FORECAST_YEAR, forecast_route, load_routes
from backend.calculation_models import InputModel, Text
from backend.network.asop import load_snapshot
from backend.network.cargo import normalized_name
from backend.network.chukotavia import normalized
from backend.network.provider_tariffs import observations as provider_observations

from .regional_forecast import ForecastQuery, forecast_reply, rank_regional_forecast
from .schemas import DocumentSource, InformationTurn, ReferenceResult


ROOT = Path(__file__).resolve().parents[3]
AIR_PATH = "backend/data_sources/chukotavia/dataset.json"
ASOP_PATH = "backend/data_sources/asop/observations.json"
CONNECTIVITY_PATH = "backend/data_sources/russia/connectivity.json"
FESCO_PATH = "backend/data_sources/russia/fesco-voyages.json"
PROVIDER_TARIFF_PATH = "backend/data_sources/delivery_forecast/provider_observations.json"
PIPELINE_PATH = "backend/data_sources/pipeline/tariffs.json"
PORTS_PATH = "backend/data_sources/rosmorport/references.json"
RIVER_PATH = "backend/data_sources/river/network.json"
STRATEGY_PATH = "backend/data_sources/strategy/context.json"

MODE_LABELS = {
    "road": "авто", "rail": "ж/д", "air": "авиа", "river": "река", "sea": "море",
    "pipeline": "трубопровод", "inland_water": "внутренний водный",
    "overall_excluding_pipeline": "все виды без трубопроводного",
}


@lru_cache(maxsize=None)
def snapshot(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def source_ref(source, path, *, section=None, title=None):
    if section is None and source.get("publication_year"):
        section = source.get("description", "")[:100] or str(source["publication_year"])
    return DocumentSource(
        title=title or source.get("title") or source.get("name") or source.get("id") or "Снимок проекта",
        url=source.get("url") or source.get("source_url"),
        path=path,
        section=section,
        date=source.get("observed_on") or source.get("reviewed_on") or source.get("date"),
    )


def number(value):
    whole, _, fraction = f"{Decimal(str(value)):,f}".partition(".")
    fraction = fraction.rstrip("0")
    return whole.replace(",", " ") + (f",{fraction}" if fraction else "")


class LookupLimit(InputModel):
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 5


class CityPair(InputModel):
    origin: Text
    destination: Text


A1Year = Annotated[StrictInt, Field(ge=MIN_FORECAST_YEAR, le=MAX_FORECAST_YEAR)]


class CorridorComparisonQuery(CityPair):
    year: A1Year


class CorridorForecastQuery(CityPair):
    start_year: A1Year
    end_year: Optional[A1Year] = None
    granularity: Granularity = "year"

    @model_validator(mode="after")
    def ordered_years(self):
        if self.end_year is not None and self.end_year < self.start_year:
            raise ValueError("Конечный год должен быть не раньше начального")
        return self


class AirScheduleQuery(CityPair):
    flight_date: date


class ConnectivityQuery(CityPair):
    mode: Optional[Literal["road", "rail", "air"]] = None


class FescoQuery(LookupLimit):
    origin: Optional[Text] = None
    destination: Optional[Text] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Конец периода должен быть не раньше начала")
        return self


class FescoReeferQuery(CityPair):
    size_ft: Literal[20, 40] | None = None
    shipment_date: date | None = None


class RiverQuery(InputModel):
    origin: Optional[Text] = None
    destination: Optional[Text] = None
    departure_date: Optional[date] = None


class PipelineReferenceQuery(LookupLimit):
    section: Literal["routes", "operations", "pumping_by_operator", "dispatch", "physical_sections"] = "routes"
    origin: Optional[Text] = None
    destination: Optional[Text] = None
    point: Optional[Text] = None
    operator: Optional[Text] = None

    @model_validator(mode="after")
    def relevant_filters(self):
        if (self.origin or self.destination) and self.section not in ("routes", "physical_sections"):
            raise ValueError("Фильтр по направлению применим к маршрутам и физическим участкам")
        if self.point and self.section != "operations":
            raise ValueError("Фильтр по пункту применим к операциям")
        if self.operator and self.section != "pumping_by_operator":
            raise ValueError("Фильтр по оператору применим к ставкам перекачки")
        return self


class PortQuery(LookupLimit):
    query: Text
    topic: Literal["overview", "characteristics", "navigation", "services", "fees", "location"] = "overview"


class AsopQuery(LookupLimit):
    year: Annotated[StrictInt, Field(ge=1900, le=2100)]
    period_kind: Literal["year", "Q1"] = "year"
    metric: Optional[Literal["port_inbound", "port_outbound", "basin", "direction", "dry_cargo", "liquid_cargo"]] = None
    category: Optional[Text] = None
    include_flagged: StrictBool = False


class StrategyQuery(InputModel):
    year: Annotated[StrictInt, Field(ge=2000, le=2100)]
    mode: Optional[Literal["road", "rail", "river", "overall_excluding_pipeline"]] = None
    scenario: Optional[Literal["baseline", "conservative"]] = None


STRATEGY_MODE_NAMES = {
    "road": "автомобильный транспорт",
    "rail": "железнодорожный транспорт",
    "river": "внутренний водный транспорт",
    "overall_excluding_pipeline": "все виды транспорта, кроме трубопроводного",
}
STRATEGY_SCENARIOS = {
    "baseline": ("Базовый", "благоприятные условия для развития экономики"),
    "conservative": ("Консервативный", "более осторожная оценка развития экономики и спроса на перевозки"),
}


def _information(dataset_id, title, rows, lines, assumptions, sources, **details):
    return InformationTurn(
        reply="\n".join([title, *lines, *assumptions]),
        sources=sources,
        result=ReferenceResult(
            dataset_id=dataset_id, status="available" if rows else "unavailable",
            rows=rows, assumptions=assumptions, details=details,
        ),
    )


A1_DATASET = "route-benchmark-v2"
A1_MANIFEST = "backend/data_sources/a1_forecast/route-benchmark-v2/manifest.json"
A1_RAIL_REFERENCE = "backend/data_sources/a1_forecast/kemerovo-reference.json"
A1_VOLUME_NOTE = "Месячные объёмы синтетические. Это сценарий для одного условного грузоотправителя."
A1_SCHEMES = {
    "spot_road": "Разовые автозаказы",
    "contract_road": "Авторейсы по договору",
    "road_rail_road": "Авто → ж/д → авто",
}


def _corridor_input(query):
    routes, manifest = load_routes()
    requested = normalized_name(query.origin), normalized_name(query.destination)
    route = next((r for r in routes if requested == (normalized_name(r["origin"]), normalized_name(r["destination"]))), None)
    sources = [source_ref({}, A1_MANIFEST, title="Сценарный ряд объёмов на 12 коридорах"),
               source_ref({"url": manifest["source_url"]}, CONNECTIVITY_PATH, title="Точно: расстояния и время поездок")]
    return route, sources


def corridor_forecast(query: CorridorForecastQuery):
    route, sources = _corridor_input(query)
    if route is None:
        return _information(A1_DATASET, "Для этого направления в проекте нет ряда объёмов.", [], [], [], sources)
    result = forecast_route(route["route_id"], query.start_year, query.end_year or query.start_year, query.granularity)
    title = (f"{route['origin']} → {route['destination']}, {result['start_year']}–{result['end_year']}. "
             f"Сценарный объём — {number(result['forecast_tonnes'])} т.")
    return _information(A1_DATASET, title, result["periods"], [], [A1_VOLUME_NOTE], sources,
                        route_id=route["route_id"], forecast_tonnes=result["forecast_tonnes"], volume_status="synthetic")


def corridor_comparison(query: CorridorComparisonQuery):
    route, sources = _corridor_input(query)
    if route is None:
        return _information(A1_DATASET, "Для этого направления нет расчёта двух гипотез. Доступны 12 опорных коридоров.",
                            [], [], [], sources)
    pilot = pilot_reference()
    rail = pilot["rail_reference"]
    sources.append(source_ref({"url": rail["url"], "date": rail["tariff_date"]}, A1_RAIL_REFERENCE,
                              title="Рейл Континент: опубликованная ставка по Кемерово как ориентир"))
    result = compare_corridor(route, pilot["connectivity"], rail, query.year)
    rows, lines = [], []
    for scheme, data in result["annual"].items():
        verdict = "База сравнения" if scheme == "spot_road" else (
            "Гипотеза подтвердилась" if data["hypothesis_supported"] else "Гипотеза не подтвердилась")
        cost = f"{number(data['cost_rub'])} ₽" if data["cost_rub"] is not None else "цена не рассчитана"
        feasibility = "в срок" if data["feasible"] else "весь объём в срок не доставлен"
        rows.append({"scheme": A1_SCHEMES[scheme], "verdict": verdict, **data})
        lines.append(f"{A1_SCHEMES[scheme]} — {cost}, {feasibility}. {verdict}.")
    title = (f"{route['origin']} → {route['destination']}, {query.year}: {number(result['forecast_tonnes'])} т "
             f"за год, лимит {MAIN_DEADLINE_DAYS} дней на доставку партии.")
    return _information(A1_DATASET, title, rows, lines, [
        A1_VOLUME_NOTE,
        "Тарифы и операции сценарные. Опубликованная ж/д ставка по Кемерово используется как ориентир для масштабирования.",
        "Гипотеза засчитывается, когда схема и разовое авто доставляют весь объём в срок, а схема обходится дешевле разовых заказов.",
    ], sources, route_id=route["route_id"], origin=route["origin"], destination=route["destination"],
        year=query.year, deadline_days=MAIN_DEADLINE_DAYS, volume_status="synthetic",
        forecast_tonnes=result["forecast_tonnes"], sensitivity=result["sensitivity"], connectivity=route["connectivity_reference"])


def air_schedule(query, data):
    day = query.flight_date.isoformat()
    key = normalized(query.origin), normalized(query.destination)
    rows = [dict(row, booking_confirmed=False) for row in data.flights.get(key, []) if row["date"] == day]
    sources = [source_ref(data.sources[key], AIR_PATH) for key in dict.fromkeys(row["source_id"] for row in rows)]
    if not sources:
        sources = [source_ref({}, AIR_PATH, title="Снимок расписания ЧукотАВИА")]
    lines = [
        f"{row['flight']} — {row['departure_local']} → {row['arrival_local']} (местное время)"
        + (f", через {', '.join(row['via'])}" if row["via"] else "") + "."
        for row in rows
    ]
    return _information(
        "chukotavia-2026", f"ЧукотАВИА, {query.origin} → {query.destination}, {query.flight_date:%d.%m.%Y}. "
        f"В снимке найдено рейсов — {len(rows)}.", rows, lines,
        ["Плановое расписание снимка 2026 года. Приём груза, свободный тоннаж и бронирование требуют подтверждения перевозчика."],
        sources, booking_confirmed=False, observed_on=data.data["observed_on"],
    )


def connectivity(query):
    data = snapshot(CONNECTIVITY_PATH)

    def resolve(value):
        matches = [
            city["id"] for city in data["settlements"]
            if city["id"] == value or normalized_name(city["name"]) == normalized_name(value)
        ]
        return matches[0] if len(matches) == 1 else None

    origin, destination = resolve(query.origin), resolve(query.destination)
    rows = [
        dict(zip(data["columns"], row))
        for row in data["connections"]
        if row[0] == origin and row[1] == destination and query.mode in (None, row[2])
    ]
    lines = [
        f"{MODE_LABELS[row['mode']]} — "
        + (f"{number(row['distance_km'])} км, {number(row['estimated_hours'])} ч."
           if row["distance_km"] is not None and row["estimated_hours"] is not None
           else f"{row['missing_reason'] or 'нет полной оценки расстояния и времени'}.")
        for row in rows
    ]
    title = f"Наблюдения связности {query.origin} → {query.destination}."
    if not rows:
        title += " В снимке нет записей для выбранного направления и вида транспорта."
    return _information(
        data["id"], title, rows, lines,
        ["Это наблюдения о расстоянии и времени поездок. Грузовые ставки, расписание и доступная вместимость в них не подтверждены."],
        [source_ref(data["source"], CONNECTIVITY_PATH, title="Если быть точным — связность городов")],
    )


def _matches(value, choices):
    return value is None or any(choice and normalized_name(choice) == normalized_name(value) for choice in choices)


def fesco(query):
    data = snapshot(FESCO_PATH)
    hubs = {hub["id"]: hub for hub in data["hubs"]}
    records = []
    for voyage in data["voyages"]:
        origin, destination = hubs[voyage["from_hub"]], hubs[voyage["to_hub"]]
        departure_day = date.fromisoformat(voyage["departure_at"][:10])
        if not _matches(query.origin, (origin["id"], origin["name"], origin["settlement_name"])):
            continue
        if not _matches(query.destination, (destination["id"], destination["name"], destination["settlement_name"])):
            continue
        if (query.date_from and departure_day < query.date_from) or (query.date_to and departure_day > query.date_to):
            continue
        records.append({
            "id": voyage["id"], "origin": origin, "destination": destination,
            "departure_date": departure_day.isoformat(), "arrival_date": voyage["arrival_at"][:10],
            "reference_metadata": voyage["reference_metadata"], "booking_confirmed": False,
        })
    records.sort(key=lambda row: row["departure_date"])
    rows = records[:query.limit]
    ids = {row["reference_metadata"]["source_id"] for row in rows}
    lines = [
        f"{row['reference_metadata']['voyage']}, {row['origin']['settlement_name']} → "
        f"{row['destination']['settlement_name']} — {row['departure_date']} → {row['arrival_date']}."
        for row in rows
    ]
    return _information(
        data["id"], f"Рейсы FESCO по датам из PDF — найдено {len(records)}, показано {len(rows)}.", rows, lines,
        ["Опубликованы календарные даты. Время суток, фрахт и рабочие параметры модели сценарные. Бронирование не подтверждено."],
        [source_ref(source, FESCO_PATH) for source in data["sources"] if not ids or source["id"] in ids],
        total_matches=len(records),
    )


def fesco_reefer_tariff(query):
    collected, records, documents, _ = provider_observations()
    record = records["fesco-fpkl-container-cy-cy"]
    endpoints = record["endpoints"]
    direction = [query.origin, query.destination]
    normalized_direction = list(map(normalized_name, direction))
    forward = list(map(normalized_name, endpoints))
    source = source_ref({"url": documents[record["source"]], "date": collected}, PROVIDER_TARIFF_PATH,
                        title="FESCO — контейнерные тарифы ФПКЛ", section=record["source_locator"])
    if normalized_direction not in (forward, forward[::-1]):
        return _information(record["id"], "Этот тарифный снимок относится к линии Владивосток ↔ Петропавловск-Камчатский.",
                            [], [], [], [source])
    if query.shipment_date and not date.fromisoformat(record["valid_from"]) <= query.shipment_date <= date.fromisoformat(collected):
        return _information(record["id"], "Для выбранной даты применимость ставки в снимке не подтверждена.",
                            [], [], [f"Ставка опубликована с {record['valid_from']}, снимок собран {collected}."], [source])
    column = 1 if normalized_direction == forward else 2
    rows = []
    for size in (20, 40) if query.size_ft is None else (query.size_ft,):
        tariff = next(row[column] for row in record["rows"] if row[0] == f"shipper_{size}_reefer")
        if tariff is not None:
            rows.append({"container_ft": size, "rate_rub": tariff, "valid_from": record["valid_from"], "vat": "с НДС"})
    return _information(
        record["id"], f"Фрахт FESCO для рефконтейнера грузоотправителя, {query.origin} → {query.destination}.", rows,
        [f"{row['container_ft']} футов — {number(row['rate_rub'])} ₽ за контейнер с НДС." for row in rows],
        [f"Ставка опубликована с {record['valid_from']}, снимок собран {collected}.",
         record["notes"], "Наличие мест, допустимый температурный диапазон и полная цена требуют подтверждения перевозчика."],
        [source], booking_confirmed=False, cost_scope="published_component", observed_on=collected,
    )


def river(query):
    data = snapshot(RIVER_PATH)
    hubs = {hub["id"]: hub["city"] for hub in data["hubs"]}
    rows = []
    for service in data["services"]:
        origin, destination = hubs[service["from_hub"]], hubs[service["to_hub"]]
        if not _matches(query.origin, (origin, service["from_hub"])) or not _matches(query.destination, (destination, service["to_hub"])):
            continue
        departures = service["timetable"]["departures"]
        if query.departure_date:
            departures = [value for value in departures if value[:10] == query.departure_date.isoformat()]
        rows.append({
            "id": service["id"], "origin": origin, "destination": destination,
            "movement_quote": service["quote"], "additional_charges": service["additional_charges"],
            "storage_quotes": service["storage_quotes"], "scenario_departures": departures,
            "scenario_windows": service["windows"], "scenario_travel_hours": service["travel_hours"],
            "scenario_unit_tonnes": service["unit_tonnes"], "scenario_unit_m3": service["unit_m3"],
            "scenario_units_per_departure": service["available_units"], "booking_confirmed": False,
        })
    lines = []
    for row in rows:
        line = (f"{row['origin']} → {row['destination']}, {row['id']} — "
                f"{number(row['movement_quote']['rate_rub'])} ₽ с НДС за контейнер за движение, "
                f"сценарный переход {number(row['scenario_travel_hours'])} ч, "
                f"сценарный лимит контейнера {number(row['scenario_unit_tonnes'])} т и "
                f"{number(row['scenario_unit_m3'])} м³.")
        if query.departure_date:
            dates = ", ".join(row["scenario_departures"]) or "нет"
            line += f" Сценарные отправления на {query.departure_date:%d.%m.%Y} — {dates}."
        lines.append(line)
    return _information(
        data["id"], f"Речной сценарий Красноярск ↔ Дудинка. Найдено вариантов — {len(rows)}.", rows, lines,
        ["Движение, обработка и хранение представлены отдельными компонентами. Навигация, расписание и вместимость заданы сценарием, бронирование не подтверждено."],
        [source_ref(source, RIVER_PATH) for source in data["sources"]],
        published_rates=data["sources"][0]["published_rates_including_vat"],
    )


def pipeline_points(data):
    return {point["id"]: point for point in data["points"]}


def point_matches(value, point):
    return _matches(value, (point["id"], point["name"], point["settlement_name"]))


def pipeline_reference(query):
    data = snapshot(PIPELINE_PATH)
    points = pipeline_points(data)
    records = []
    items = data[query.section]
    if isinstance(items, dict):
        items = [items]
    for item in items:
        row = dict(item)
        for field in ("origin", "destination", "point_id"):
            value = getattr(query, "point" if field == "point_id" else field)
            if field in row:
                point = points[row[field]]
                if not point_matches(value, point):
                    break
                row[f"{field}_name"] = point["name"]
            elif not _matches(value, (row.get(f"{field}_name"),)):
                break
        else:
            if query.operator and not _matches(query.operator, (row.get("operator"),)):
                continue
            records.append(row)
    units = {"per_tonne": "т", "per_100_tonnes": "100 т", "per_100_tonne_km": "100 т·км"}
    rows = records[:query.limit]
    lines = []
    for row in rows:
        label = row.get("point_id_name") or row.get("operator") or "Диспетчеризация"
        if "origin_name" in row and "destination_name" in row:
            label = f"{row['origin_name']} → {row['destination_name']}"
        if "operation" in row:
            label += " (" + {"loading": "налив", "unloading": "слив"}.get(row["operation"], row["operation"]) + ")"
        if "rate_rub" in row:
            lines.append(f"{label} — {number(row['rate_rub'])} ₽ за {units[row['basis']]} без НДС.")
        else:
            lines.append(f"{label} — {number(row['distance_km'])} км справочной длины.")
    source_ids = {row.get("source_id") for row in rows}
    sources = [source_ref(source, PIPELINE_PATH) for source in data["sources"] if source["id"] in source_ids]
    for url in dict.fromkeys(row["source_url"] for row in rows if row.get("source_url")):
        sources.append(source_ref({"url": url}, PIPELINE_PATH, title="Опубликованные ставки перекачки"))
    if not sources:
        sources.append(source_ref({}, PIPELINE_PATH, title="Снимок трубопроводных ставок и участков"))
    return _information(
        data["id"], f"Трубопроводный справочник по товарной нефти — найдено {len(records)}, показано {len(rows)}.",
        rows, lines, [data["validity_note"], "Ставки покрывают отдельные компоненты. Справочные длины не задают тарифное расстояние. Приём и мощность не подтверждены."],
        sources, total_matches=len(records), section=query.section, rules=data["rules"],
    )


PORT_SECTIONS = {
    "navigation": "Период навигации",
    "services": "Перечень услуг, оказываемых в морском порту",
    "fees": "Портовые сборы",
    "location": "Местонахождение морского порта",
}


def ports(query):
    data = snapshot(PORTS_PATH)
    key = normalized_name(query.query)
    records = [port for port in data["ports"] if any(
        key in normalized_name(port[field] or "") for field in ("name", "id", "basin")
    )]
    rows = records[:query.limit]
    lines = []
    for port in rows:
        values = []
        if query.topic in ("overview", "navigation") and port["navigation_text"]:
            values.append(port["navigation_text"])
        if query.topic in ("overview", "characteristics"):
            values.extend(f"{item['label']} — {item['value_raw']}" for item in port["technical_characteristics"][:5])
        if query.topic in PORT_SECTIONS and query.topic != "navigation":
            values.extend(item["text"][:280] for item in port["sections"] if item["title"] == PORT_SECTIONS[query.topic])
        lines.append(f"{port['name']}, {port['basin']} бассейн — " + (" · ".join(values) if values else "по выбранной теме сведений нет"))
    return _information(
        data["dataset_id"], f"Справочник Росморпорта — найдено {len(records)}, показано {len(rows)}.", rows, lines,
        ["Сведения относятся к порту в целом. Декларируемая годовая мощность не подтверждает свободную вместимость или параметры конкретного терминала."],
        [source_ref(port, PORTS_PATH) for port in rows] or [source_ref({}, PORTS_PATH, title="Снимок справочника Росморпорта")],
        total_matches=len(records), topic=query.topic,
    )


@lru_cache(maxsize=1)
def _asop_snapshot():
    return load_snapshot()


def asop(query):
    data = _asop_snapshot()
    records = [row for row in data["observations"] if row["year"] == query.year
               and row["period_kind"] == query.period_kind and query.metric in (None, row["metric"])
               and (query.include_flagged or not row["quality_flags"])
               and (query.category is None or any(
                   normalized_name(query.category) in normalized_name(row[field])
                   for field in ("category_name", "category_id")
               ))]
    rows = records[:query.limit]
    lines = [
        f"{row['category_name']} ({row['metric']}) — {row['source_value'].replace('.', ',')} "
        + ("млн т." if row["source_unit"] == "million_tonnes" else "(единица не установлена).")
        + (f" Метки качества — {', '.join(row['quality_flags'])}." if row["quality_flags"] else "")
        for row in rows
    ]
    ids = {row["source_file_id"] for row in rows}
    sources = [source_ref({"url": source["download_url"] or source["landing_url"], **source}, ASOP_PATH,
                          title=f"АСОП — {source['filename']}", section="Страница 1")
               for source in data["source_files"] if source["id"] in ids]
    return _information(
        data["dataset_id"], f"АСОП, {query.year}, {'I квартал' if query.period_kind == 'Q1' else 'год'} — "
        f"найдено {len(records)}, показано {len(rows)}.", rows, lines,
        ["Агрегированные округлённые подписи диаграмм. Они не определяют конкретный грузопоток, тариф или будущий рост."],
        sources or [source_ref({}, ASOP_PATH, title="Снимок статистики АСОП")], total_matches=len(records),
    )


def strategy(query):
    data = snapshot(STRATEGY_PATH)
    rows = [row for row in data["scenario_values"] if row["year"] == query.year
            and query.mode in (None, row["mode"]) and query.scenario in (None, row["scenario"])]
    title = f"Годовой объём грузоперевозок по России в {query.year} году"
    if query.mode:
        title += f" ({STRATEGY_MODE_NAMES[query.mode]})"
    lines = []
    for row in rows:
        scenario, _ = STRATEGY_SCENARIOS[row["scenario"]]
        mode = "" if query.mode else f", {STRATEGY_MODE_NAMES[row['mode']]}"
        lines.append(f"{scenario} сценарий{mode} — {number(row['value_million_tonnes'])} млн т.")

    scenarios = {row["scenario"]: row["value_million_tonnes"] for row in rows}
    if len(rows) == 2 and len(scenarios) == 2 and rows[0]["mode"] == rows[1]["mode"]:
        baseline = scenarios["baseline"]
        difference = abs(baseline - scenarios["conservative"])
        if difference and baseline:
            percent = round(difference * 100 / baseline, 1)
            lines.append(f"Разница между сценариями — {number(difference)} млн т, "
                         f"или {number(percent)}% от объёма в базовом сценарии.")

    assumptions = []
    if not rows:
        lines.append("В снимке стратегии нет значений для этого запроса.")
    elif all(row["status"] == "historical_baseline_in_strategy" for row in rows):
        assumptions.append(f"{query.year} год — исходный уровень, с которым авторы стратегии сравнивали прогнозы.")
    else:
        for key, (name, description) in STRATEGY_SCENARIOS.items():
            if key in scenarios:
                assumptions.append(f"{name} сценарий — {description}.")
        assumptions.append("Это оценки на год из Транспортной стратегии 2021 года. У каждого сценария свои допущения о спросе на перевозки.")
    page_numbers = ", ".join(map(str, sorted({row["page"] for row in rows})))
    return _information(
        data["dataset_id"], title + ".", rows, lines, assumptions,
        [source_ref({"source_url": data["source_url"], "date": data["document_date"]}, STRATEGY_PATH,
                    title=data["document_title"],
                    section=f"Страницы PDF {page_numbers}" if page_numbers else None)],
    )


def regional_forecast(query: ForecastQuery, dataset):
    forecast = rank_regional_forecast(query, dataset)
    result = forecast.model_dump(mode="json")
    rows = result.pop("rows")
    status = result.pop("status")
    if status == "available":
        sources = [DocumentSource(title=dataset.title, url=dataset.source_url)]
        assumptions = [forecast.method]
    else:
        sources = [source_ref({}, "docs/data/README.md", title="Перечень снимков проекта")]
        assumptions = ["Транспортная стратегия содержит общероссийские макросценарии."]
    return InformationTurn(
        reply="\n".join([forecast_reply(forecast), *(assumptions if status == "unavailable" else [])]),
        result=ReferenceResult(dataset_id="regional-forecast", status=status, rows=rows,
                               assumptions=assumptions, details=result),
        sources=sources,
    )
