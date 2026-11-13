"""Shared settlement route forecast for the form and the assistant."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from functools import cache
from math import ceil
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from backend.calculation_models import Hours, OnceSchedule, Schedule
from backend.network.air_tariff import YAKUTSK
from backend.network.air_market import air_options, air_rates
from backend.network.connection_pilot import ConnectionConditions, connection_pilot
from backend.network.forecast_indices import load_indices
from backend.network.land_market import calibration, dry_cargo, land_options, price_leg, rail_services
from backend.network.cargo import named_temperature_cargo
from backend.network.market_assumptions import road_budget
from backend.network.provider_tariffs import AIR_SOURCE, FORECAST_BASE_SOURCES
from backend.network.published_land import calculate_published
from backend.network.russia import END, SCENARIO, START, RussiaRequest, RussiaResponse, load_russia
from backend.network.yakutsk_bulk_market import bulk_market_routes
from backend.schedule import build_schedule
from backend.transport_calculator import money


TOOL = "estimate_future_delivery_cost"
BASE_YEAR = datetime.fromisoformat(START).year
MODEL_VERSION = "delivery-cost-v4"
PROJECTED_MODES = frozenset(("road", "rail", "air"))
SCENARIO_CONNECTIONS = ("observation", "connector", "provider_rail", "provider_air")
TECHNICAL_HOURS = 90 * 24
MOSCOW = ZoneInfo("Europe/Moscow")
CHARGES = {"road": "автоперевозка", "rail": "ж/д", "handling": "обработка", "storage": "хранение",
           "river": "речная перевозка", "sea": "морская перевозка", "air": "авиаперевозка", "pipeline": "трубопровод"}


class ForecastRequest(RussiaRequest):
    target_year: Annotated[int, Field(strict=True, gt=BASE_YEAR, le=2100)]
    schedule: Schedule | None = None
    max_delivery_hours: Hours | None = None
    connection_conditions: ConnectionConditions | None = None

    @model_validator(mode="after")
    def dates_in_target_year(self):
        if self.schedule is not None:
            events, _, _ = build_schedule(self.schedule)
            if any(event.astimezone(MOSCOW).year != self.target_year for event in events):
                raise ValueError("Все отправки должны быть в выбранном году по МСК. Разбейте отправки по годам.")
        return self


class ForecastResponse(RussiaResponse):
    forecast: dict
    road_market_estimate: dict | None = None
    land_market: dict | None = None
    air_market: dict | None = None
    published_reference: dict | None = None


def forecast_options():
    indices = load_indices()
    return {"min_year": BASE_YEAR + 1, "max_year": indices.max_year, "base_year": BASE_YEAR,
            "scenario_modes": sorted(PROJECTED_MODES)}


def _extend_evidence(value, end):
    if isinstance(value, dict):
        if (value.get("source_ids") == [SCENARIO] and value.get("status") == "synthetic"
                and value.get("valid_from") == START and value.get("valid_until") == END):
            value["valid_until"] = end
        for child in value.values():
            _extend_evidence(child, end)
    elif isinstance(value, list):
        for child in value:
            _extend_evidence(child, end)


def project_catalog(catalog, simulation_end):
    projected = deepcopy(catalog)
    end = simulation_end.isoformat()
    for cargo in projected["cargo_types"].values():
        _extend_evidence(cargo, end)
    for hub in projected["hubs"]:
        evidence = hub["evidence"]
        _extend_evidence(hub, end)
        if evidence["source_ids"] == [SCENARIO] and hub["windows"] == [{"start": START, "end": END}]:
            hub["windows"][0]["end"] = end
    for transfer in projected["transfers"]:
        if {transfer["from_mode"], transfer["to_mode"]} <= PROJECTED_MODES:
            _extend_evidence(transfer, end)
    for service in projected["services"]:
        if (service["mode"] in PROJECTED_MODES and service["timetable"]["kind"] in ("on_demand", "interval")
                and service.get("connection_status") == "observation_based_scenario"
                and service["windows"] == [{"start": START, "end": END}]):
            _extend_evidence(service, end)
            service["windows"][0]["end"] = end
            quote = service["quote"]["evidence"]
            if FORECAST_BASE_SOURCES.intersection(quote["source_ids"]) or AIR_SOURCE in quote["source_ids"]:
                quote["valid_until"] = end
    return projected


def _search_weights(network, request, index):
    # The heap uses approximate movement charges. Final ranking uses the
    # simulated ledger with actual units, transfer charges and capacity checks.
    tonnes = sum(cargo.weight_tonnes for cargo in request.cargoes)
    volume = sum(cargo.volume_m3 or 0 for cargo in request.cargoes)

    @cache
    def weight(edge_id):
        edge = network.edges[edge_id]
        mode = edge["mode"]
        if edge["kind"] not in ("observation", "provider_rail") or mode not in PROJECTED_MODES:
            return 1000 + edge["travel_hours"]
        service = network.service(edge)
        quote = service["quote"]
        factor = index(mode, int(quote["evidence"]["valid_from"][:4]))
        if factor is None:
            return 1000 + edge["travel_hours"]
        quantity = (max(1, ceil(max(tonnes / service["unit_tonnes"], volume / service["unit_m3"])))
                    if quote["basis"] == "per_unit" else tonnes * edge["distance_km"])
        base = max(quote.get("minimum_rub", 0), quote["rate_rub"] * quantity)
        return base * float(factor.central) / 1000

    return lambda edge: weight(edge["id"])


def _relative_calendar(row, reference, year_only):
    edges = {edge["id"]: edge for edge in row["connections"]}
    row["schedule_status"] = ("scenario" if any(
        edge["kind"] in SCENARIO_CONNECTIONS for edge in row["connections"]
    ) else "snapshot")
    for step in row["steps"]:
        beginning = step.get("departure_at") or step.get("start_at")
        ending = step.get("arrival_at") or step.get("end_at")
        if beginning and ending:
            step["duration_hours"] = round((datetime.fromisoformat(ending) - datetime.fromisoformat(beginning)).total_seconds() / 3600, 6)
        if step["kind"] == "transport":
            edge = edges[step["service_id"]]
            step["schedule_status"] = "scenario" if edge["kind"] in SCENARIO_CONNECTIONS else "snapshot"
    if not year_only:
        return
    for record in row["steps"] + row["deliveries"] + row["cost_items"]:
        for key in ("ready_at", "departure_at", "arrival_at", "start_at", "end_at", "delivered_at", "at"):
            if moment := record.pop(key, None):
                record[key.removesuffix("_at") + "_offset_hours" if key != "at" else "offset_hours"] = round(
                    (datetime.fromisoformat(moment) - reference).total_seconds() / 3600, 6)


def _calculate_charge(item, target_year, index):
    pricing = item["pricing"]
    amount = Decimal(str(item["amount_rub"]))
    modelled = pricing["source_ids"] == [SCENARIO] or FORECAST_BASE_SOURCES.intersection(pricing["source_ids"])
    if modelled:
        pricing["valid_until"] = END
        effective_year = pricing["base_year"]
    else:
        # The simulation already checked that this explicit rate applies at
        # the target event. Indexing it again would change a published price.
        effective_year = target_year
    pricing["effective_price_year"] = effective_year
    estimate = index(pricing["category"], effective_year)
    item.update(base_amount_rub=float(amount), amount_rub=None, lower_rub=None, upper_rub=None,
                index_factors=[], index_proxy=False, long_term=False)
    if estimate is None:
        return
    item.update(amount_rub=float(money(amount * estimate.central)),
                lower_rub=float(money(amount * estimate.lower)) if estimate.lower is not None else None,
                upper_rub=float(money(amount * estimate.upper)) if estimate.upper is not None else None,
                index_factors=[point.as_dict() for point in estimate.points],
                index_proxy=bool(estimate.proxy_for and estimate.points), long_term=estimate.long_term)


def _sum_items(items, key):
    if any(item[key] is None for item in items):
        return None
    return sum((Decimal(str(item[key])) for item in items), Decimal(0))


def _air_charge(item, request, indices, connection):
    pricing = item["pricing"]
    base = datetime.fromisoformat(pricing["valid_from"])
    metadata = connection["reference_metadata"]
    end_of_tariff = datetime.fromisoformat(metadata["tariff_valid_until"])
    in_period = False
    years = Decimal(request.target_year - base.year)
    if request.schedule is not None:
        event = datetime.fromisoformat(item["at"]).astimezone(base.tzinfo)
        in_period = base <= event < end_of_tariff
        years = Decimal(event.year - base.year)
        anniversary = base.replace(year=event.year)
        if event < anniversary:
            anniversary = anniversary.replace(year=event.year - 1)
            years -= 1
        end = anniversary.replace(year=anniversary.year + 1)
        years += Decimal((event.date() - anniversary.date()).days) / (end.date() - anniversary.date()).days
    pricing.update(valid_until=metadata["tariff_valid_until"], effective_price_year=base.year,
                   tariff_version=metadata["tariff_version"], observed_on=metadata["tariff_observed_on"],
                   tariff_projected=not in_period)
    amount = Decimal(str(item["amount_rub"]))
    item.update(base_amount_rub=float(amount), amount_rub=None, lower_rub=None, upper_rub=None,
                index_factors=[], index_proxy=False, long_term=years > 3)
    if in_period:
        item["amount_rub"] = float(amount)
    elif years >= 0 and (scenario := indices.air_scenarios(base.year, years)):
        central, softened, points = scenario
        item.update(amount_rub=float(money(amount * central)), index_factors=[point.as_dict() for point in points])
        if softened is not None:
            item["air_softened_rub"] = float(money(amount * softened))


def _annual_price_series(items, base_year, target_year, index):
    """Reprice the same charge quantities for each year, without interpolation."""
    series = []
    for year in range(base_year, target_year + 1):
        amounts, lower, upper = [], [], []
        for item in items:
            pricing = item["pricing"]
            estimate = index(pricing["category"], pricing["effective_price_year"], year)
            if estimate is None:
                break
            amount = Decimal(str(item["base_amount_rub"]))
            amounts.append(money(amount * estimate.central))
            if estimate.lower is not None:
                lower.append(money(amount * estimate.lower))
            if estimate.upper is not None:
                upper.append(money(amount * estimate.upper))
        if len(amounts) == len(items):
            series.append({
                "year": year, "amount_rub": float(sum(amounts)),
                "lower_rub": float(sum(lower)) if len(lower) == len(items) else None,
                "upper_rub": float(sum(upper)) if len(upper) == len(items) else None,
            })
    return series


class SavedCharge(BaseModel):
    category: Annotated[str, Field(min_length=1, max_length=50)]
    effective_price_year: Annotated[int, Field(strict=True, ge=1900, le=2100)]
    base_amount_rub: Annotated[Decimal, Field(ge=0, le=Decimal("1e18"))]


class SavedPriceSeriesRequest(BaseModel):
    base_year: Annotated[int, Field(strict=True, ge=1900, le=2100)]
    target_year: Annotated[int, Field(strict=True, ge=1900, le=2100)]
    base_rub: Annotated[Decimal, Field(ge=0, le=Decimal("1e20"))]
    total_rub: Annotated[Decimal, Field(ge=0, le=Decimal("1e20"))]
    indices_version: Annotated[str, Field(max_length=100)] | None = None
    model_version: Annotated[str, Field(max_length=100)] | None = None
    charges: Annotated[list[SavedCharge], Field(min_length=1, max_length=20000)]


def saved_price_series(request: SavedPriceSeriesRequest):
    """Enrich an archived ledger without rerunning route selection or changing its totals."""
    indices = load_indices()
    if (request.indices_version and request.indices_version != indices.version or
            request.model_version and request.model_version != MODEL_VERSION):
        raise ValueError("Версия модели сохранённого расчёта отличается. Выполните новый прогноз.")
    if not request.base_year <= request.target_year <= indices.max_year:
        raise ValueError("Некорректный период сохранённого прогноза.")
    if any(charge.effective_price_year > request.base_year for charge in request.charges):
        raise ValueError("Начисления не сопоставимы с базовым годом.")
    items = [{"pricing": {"category": charge.category, "effective_price_year": charge.effective_price_year},
              "base_amount_rub": charge.base_amount_rub} for charge in request.charges]
    series = _annual_price_series(items, request.base_year, request.target_year, indices.estimate)
    if (len(series) != request.target_year - request.base_year + 1 or
            money(series[0]["amount_rub"]) != money(request.base_rub) or
            money(series[-1]["amount_rub"]) != money(request.total_rub)):
        raise ValueError("Годовой ряд не совпадает с сохранёнными суммами. Выполните новый прогноз.")
    return {"annual_series": series}


def _reprice(row, request, index, reference, indices):
    requires_date = request.schedule is None and any(
        edge["mode"] not in PROJECTED_MODES or edge["kind"] not in SCENARIO_CONNECTIONS
        for edge in row["connections"]
    )
    if requires_date:
        row.update(status="insufficient_data", complete=False, total_cost_rub=None, cost_rub_per_tonne=None,
                   max_delivery_hours=None, steps=[], deliveries=[], cost_items=[])
        row["issues"].append({"code": "forecast_date_required", "fields": ["schedule"],
                              "message": "Для сезонного или датированного плеча укажите дату или период отправки."})
    feasible = row["status"] == "feasible"
    row["transport_status"] = row["status"]
    air_connections = {edge["id"]: edge for edge in row["connections"] if edge["kind"] == "provider_air"}
    tariff_metadata = {edge["id"]: edge["reference_metadata"] for edge in row["connections"]
                       if "tariff_version" in edge["reference_metadata"]}
    items = row["cost_items"]
    if air_connections:
        # The simulator uses generic airport operations for timing. The price
        # estimate includes movement charges and the air waybill.
        items = row["cost_items"] = [item for item in items if item["kind"] == "transport"]
    air_items = []
    for item in items:
        if AIR_SOURCE in item["pricing"]["source_ids"]:
            _air_charge(item, request, indices, air_connections[item["object_id"]])
            air_items.append(item)
        else:
            _calculate_charge(item, request.target_year, index)
            if (metadata := tariff_metadata.get(item["object_id"])) and item["kind"] == "transport":
                item["pricing"].update(tariff_version=metadata["tariff_version"],
                                       observed_on=metadata["tariff_observed_on"])
                if metadata.get("tariff_date_precision") == "year":
                    item["pricing"]["tariff_date_precision"] = "year"
                item["pricing"].pop("valid_until", None)
    _relative_calendar(row, reference, request.schedule is None)
    row["forecast_price"] = {"base_rub": None, "base_year": None, "lower_rub": None, "upper_rub": None,
                             "known_subtotal_rub": None, "range_status": "incomplete", "long_term": False}
    row.pop("published_tariff_estimate", None)
    known = [item for item in items if item["amount_rub"] is not None] if not air_connections or air_items else []
    if row["status"] == "infeasible" and any(stop.no_reload for stop in request.stops):
        known = []
    row["known_cost_subtotal_rub"] = float(_sum_items(known, "amount_rub")) if known else None
    row["forecast_price"]["known_subtotal_rub"] = row["known_cost_subtotal_rub"]
    if air_items and (air_total := _sum_items(air_items, "amount_rub")) is not None:
        row["forecast_price"].update(
            air_base_rub=float(_sum_items(air_items, "base_amount_rub")),
            air_median_rub=float(air_total),
            air_softened_rub=float(sum(Decimal(str(item.get("air_softened_rub", item["amount_rub"])))
                                       for item in air_items)) if any("air_softened_rub" in item for item in air_items) else None,
            long_term=any(item["long_term"] for item in air_items),
        )
    missing = {item["pricing"]["category"] for item in items if item["amount_rub"] is None}
    if missing:
        labels = ", ".join(CHARGES.get(category, category) for category in sorted(missing))
        row["issues"].append({"code": "missing_forecast_index", "message": f"Нет применимого индекса: {labels}.", "fields": []})
    if feasible:
        base_year = max(item["pricing"]["effective_price_year"] for item in items)
        normalized = []
        for item in items:
            pricing = item["pricing"]
            estimate = index(pricing["category"], pricing["effective_price_year"], base_year)
            if estimate is None or any(point.kind != "observed" for point in estimate.points):
                break
            normalized.append(money(Decimal(str(item["base_amount_rub"])) * estimate.central))
        if len(normalized) == len(items):
            row["forecast_price"].update(base_rub=float(sum(normalized)), base_year=base_year)
    if not feasible or missing:
        if feasible:
            row.update(status="insufficient_data", complete=False, total_cost_rub=None, cost_rub_per_tonne=None)
        return

    total = _sum_items(items, "amount_rub")
    mass = sum((Decimal(str(cargo["weight_tonnes"])) for cargo in row["deliveries"]), Decimal(0))
    lower, upper = _sum_items(items, "lower_rub"), _sum_items(items, "upper_rub")
    row.update(total_cost_rub=float(total), cost_rub_per_tonne=float(money(total / mass)))
    row["forecast_price"].update(
        lower_rub=float(lower) if lower is not None else None,
        upper_rub=float(upper) if upper is not None else None,
        range_status="available" if lower is not None and upper is not None else "not_calibrated",
        long_term=any(item["long_term"] for item in items),
    )
    if row["forecast_price"]["base_rub"] is not None:
        row["forecast_price"]["annual_series"] = _annual_price_series(
            items, row["forecast_price"]["base_year"], request.target_year, index)


def _alternative(best, other):
    if not other:
        return None
    high = best["forecast_price"]["upper_rub"]
    low = other["forecast_price"]["lower_rub"]
    return {"id": other["id"],
            "difference_rub": float(money(Decimal(str(other["total_cost_rub"])) - Decimal(str(best["total_cost_rub"])))),
            "difference_hours": round(other["max_delivery_hours"] - best["max_delivery_hours"], 6),
            "ranges_overlap": high >= low if high is not None and low is not None else None}


def _visible_candidates(candidates, limit):
    if not candidates:
        return []
    # Preserve direct modes and both types of mixed route alongside the best
    # priced option. Fill the remaining slots in the server's ranking order.
    representatives = {}
    combinations = {}
    for row in candidates:
        key = ("multimodal_air" if "air" in row["modes"] else "multimodal_surface") if row["multimodal"] else row["modes"][0]
        previous = representatives.get(key)
        if previous is None or (
            not row["multimodal"] and len(row["connections"]) == 1 and len(previous["connections"]) != 1
            and row["status"] != "infeasible"
        ):
            representatives[key] = row
        if row["multimodal"]:
            combinations.setdefault(tuple(row["modes"]), row)
    selected = set()
    for row in (candidates[0], *representatives.values(), *combinations.values()):
        if len(selected) == limit:
            break
        selected.add(row["id"])
    for row in candidates:
        if len(selected) == limit:
            break
        selected.add(row["id"])
    return [row for row in candidates if row["id"] in selected]


def _normalized_request(request, network, settlements):
    payload = request.model_dump(mode="json")
    if request.connection_conditions is None:
        payload.pop("connection_conditions")
    points = [(payload, "origin"), *((stop, "settlement") for stop in payload["stops"]), (payload, "destination")]
    for (item, field), settlement in zip(points, settlements):
        if settlement:
            name = network.settlements[settlement]["name"]
            item[field] = name if not settlement.startswith("gar:") and network.resolve(name) == settlement else settlement
    return payload


def _vladivostok_three_mode(network, result, request, indices):
    if (request.data_policy != "allow_assumptions" or request.stops or result["clarifications"]
            or result["shipments"] != 1 or request.max_legs < 3 or request.max_delivery_hours is not None
            or not {"air", "road", "rail"}.issubset(request.allowed_modes)
            or dry_cargo(network, request) is None):
        return None
    origin, destination = result["settlements"]
    if (origin, destination) != (network.resolve("Москва"), network.resolve("Владивосток")):
        return None
    ekb, novo = map(network.resolve, ("Екатеринбург", "Новосибирск"))
    if not ekb or not novo:
        return None
    air = min((row for row in air_rates(network, request, indices).get((origin, ekb), ()) if not row["via"]),
              key=lambda row: row["forecast_rub"], default=None)
    if air is None:
        return None
    snapshot = calibration()
    road = network.edges.get(f"observation:road:{ekb}:{novo}", {})
    trucks = price_leg(network, (ekb, novo), "road", 20, request, indices, snapshot,
                       road.get("distance_km"), "observation", None)
    rail = network.edges.get(f"observation:rail:{novo}:{destination}", {})
    service = rail_services(network).get((novo, destination))
    trains = [leg for size in (20, 40) for leg in
              price_leg(network, (novo, destination), "rail", size, request, indices, snapshot,
                        rail.get("distance_km"), "observation", service, linehaul_only=True)]
    if not trucks or not trains:
        return None
    flight = {key: air[key] for key in ("origin", "destination", "origin_id", "destination_id",
                                       "base_year", "forecast_rub", "vat")}
    flight.update(mode="air", equipment="Авиагруз по платному весу", units=air["shipment_count"],
                  unit_tonnes=None, unit_m3=air["volume_m3_per_shipment"],
                  base_unit_rub=air["base_rub"] / air["shipment_count"],
                  kind="model" if air["shipment_count"] > 1 else "published_base",
                  sources=[air["source"]], distance_km=None, distance_source=None, confidence=None)
    parts = [flight, trucks[0], min(trains, key=lambda leg: leg["forecast_rub"])]
    taxes = {leg["vat"] for leg in parts}
    return {"legs": parts, "transfer_count": 2, "vat": taxes.pop() if len(taxes) == 1 else "mixed",
            "movement_rub": float(sum(Decimal(str(leg["forecast_rub"])) for leg in parts)),
            "scenario_note": "Сумма трёх транспортных плеч. Обработку в аэропорту, подачу на станцию, "
                             "стыковки и наличие мест нужно подтвердить отдельно."}


def calculate_forecast(request, network=None, indices=None):
    indices = indices or load_indices()
    if request.target_year > indices.max_year:
        raise ValueError(f"Оценка доступна до {indices.max_year} года по текущему снимку индексов.")
    # A representative phase of the repeating weekly calendar, never returned
    # as a cargo readiness date or an actual departure.
    if request.schedule:
        events, _, _ = build_schedule(request.schedule)
        ready = min(events)
        network = network or load_russia(tariff_date=ready.astimezone(MOSCOW).date(),
                                         air_tariff_date=ready.astimezone(YAKUTSK).date())
        schedule = request.schedule
    else:
        network = network or load_russia(tariff_date=date(request.target_year, 12, 31))
        start = date(request.target_year, 1, 1)
        if "rail" in request.allowed_modes:
            effective = date.fromisoformat(network.tlm_tariff["valid_from"])
            if effective.year == request.target_year:
                start = effective
        if "air" in request.allowed_modes:
            effective = date.fromisoformat(network.air_tariff["valid_from"])
            if effective.year == request.target_year:
                start = max(start, effective)
        schedule = OnceSchedule(mode="once", ready_at=datetime(
            start.year, start.month, start.day, 12, tzinfo=timezone.utc))
    horizon = request.max_delivery_hours or TECHNICAL_HOURS
    events, _, _ = build_schedule(schedule)
    end = max(events) + timedelta(hours=horizon + 24)
    simulation = RussiaRequest.model_validate({
        **request.model_dump(exclude={"target_year", "schedule", "max_delivery_hours", "connection_conditions"}),
        "schedule": schedule, "max_delivery_hours": horizon, "max_candidates": 16,
    })
    @cache
    def index(category, base_year, target_year=request.target_year):
        return indices.estimate(category, base_year, target_year)

    strict_land = request.data_policy == "verified_only" and {"road", "rail"}.intersection(request.allowed_modes)
    if strict_land:
        simulation = simulation.model_copy(update={"max_candidates": request.max_candidates})
    result = network.calculate(simulation, project_catalog=lambda catalog: project_catalog(catalog, end),
                               price_weight=_search_weights(network, request, index),
                               include_dated=request.schedule is not None,
                               published_forecast_year=request.target_year, indices=indices)
    result["request"] = _normalized_request(request, network, result["settlements"])
    temperature_controlled = False
    for cargo in request.cargoes:
        props = cargo.properties
        needs_temperature = props.temperature_control_required if props else named_temperature_cargo(cargo.name)
        temperature_controlled |= bool(needs_temperature or cargo.temperature_c or
                                       props and props.required_temperature_c)
        if needs_temperature and not (cargo.temperature_c or props and props.required_temperature_c):
            if not any(row.get("code") == "missing_temperature_range" and row.get("cargo_id") == cargo.id
                       for row in result["clarifications"]):
                result["clarifications"].append({"code": "missing_temperature_range", "cargo_id": cargo.id,
                                                 "fields": ["temperature_c"],
                                                 "message": "Укажите допустимый температурный диапазон груза"})
            result.update(input_status="needs_clarification", status="needs_clarification",
                          candidates=[], recommendation=None)
    result["land_market"] = land_options(network, result, request, indices)
    bulk_routes = bulk_market_routes(network, result, request, indices)
    if three_mode := _vladivostok_three_mode(network, result, request, indices):
        bulk_routes.insert(0, three_mode)
    if bulk_routes:
        market = result["land_market"] or {
            "version": "bulk-market-2026-11", "target_year": request.target_year, "routes": [], "legs": [],
        }
        market["routes"] = (bulk_routes + market["routes"])[:request.max_candidates]
        result["land_market"] = market
    result["air_market"] = air_options(network, result, request, indices)
    if temperature_controlled:
        # General price lists carry no equipment/temperature admission metadata.
        result["published_coverage"] = None
        result["published_reference"] = None
        result["candidates"] = [row for row in result["candidates"] if row["complete"]
                                and row["status"] == "feasible" and not row.get("published_tariff_estimate")]
        result["recommendation"] = None
    if not temperature_controlled and not strict_land and result.get("published_coverage") is None and network.registry and (
        {"road", "rail"}.intersection(request.allowed_modes) and not result["clarifications"]
    ):
        published = calculate_published(network, {"settlements": result["settlements"], "clarifications": []},
                                        simulation.model_copy(update={"max_candidates": request.max_candidates}),
                                        events, request.target_year, indices)
        result["published_reference"] = {"coverage": published["published_coverage"],
                                         "candidates": published["candidates"]}
        source_ids = {source["id"] for source in result["sources"]}
        result["sources"].extend(source for source in published["sources"] if source["id"] not in source_ids)
    air_market = result["air_market"] or {}
    has_air_market_price = any(air_market.get("segments", ())) or bool(air_market.get("routes"))
    has_land_market_price = bool(bulk_routes) or any((result["land_market"] or {}).get("legs", ()))
    if result["status"] == "no_route_in_dataset" and (
        has_air_market_price or has_land_market_price or any(air_market.get("weight_limits", ()))
    ):
        result["status"] = "not_confirmed"
    market = None if temperature_controlled else road_budget(network, result, request, indices)
    result["road_market_estimate"] = market
    if market:
        capacity_source = market["capacity_source"]
        sources = [market["tariff_source"], capacity_source | {
            "id": "logistic-avto-truck-capacity-benchmark", "status": "market_reference", "roles": ["equipment"]
        }]
        ids = {source["id"] for source in result["sources"]}
        result["sources"].extend(source for source in sources if source["id"] not in ids)
    if strict_land or result.get("published_coverage") is not None:
        result["candidates"] = result["candidates"][:request.max_candidates]
        result["forecast"] = {
            "target_year": request.target_year, "base_year": BASE_YEAR, "indices_version": indices.version,
            "model_version": MODEL_VERSION, "date_precision": "date" if request.schedule else "year",
            "schedule_status": "unconfirmed", "alternative": None,
            "coverage_status": "partial" if result["candidates"] or has_air_market_price or has_land_market_price else "insufficient_data",
            "assumptions": ["Для прогноза используются применимые опубликованные ставки. Срок и доступность рейса требуют подтверждения."],
        }
        return ForecastResponse.model_validate(result)
    for row in result["candidates"]:
        _reprice(row, request, index, events[0], indices)
    continuous = any(stop.no_reload for stop in request.stops)
    candidates = sorted(result["candidates"], key=lambda row: (
        row["status"] != "feasible", row["total_cost_rub"] if row["total_cost_rub"] is not None else float("inf"),
        row["forecast_price"].get("air_median_rub") is None,
        -sum(edge["reference_metadata"].get("tariff_source_id") is not None for edge in row["connections"])
        if continuous and row["known_cost_subtotal_rub"] is not None else 0,
        row["known_cost_subtotal_rub"] if continuous and row["known_cost_subtotal_rub"] is not None else float("inf"),
        row["max_delivery_hours"] if row["max_delivery_hours"] is not None else float("inf"), row["id"]))
    visible = _visible_candidates(candidates, request.max_candidates)
    route = [network.settlements[city]["name"] if city else None for city in result["settlements"]]
    for row in visible:
        if pilot := connection_pilot(row, route, request, indices):
            row["connection_pilot"] = pilot
    eligible = [row for row in visible if row["status"] == "feasible"]
    best = eligible[0] if eligible else None
    alternative = _alternative(best, eligible[1] if len(eligible) > 1 else None)
    result["search"].update(evaluated_candidates=len(candidates), output_limit=request.max_candidates,
                            price_heuristic="forecast_movement", truncated=result["search"]["truncated"]
                            or len(candidates) > request.max_candidates)
    result.update(candidates=visible, recommendation=best["id"] if best else None)
    if candidates and result["input_status"] == "ready":
        result["status"] = "calculated" if best else "not_confirmed"
    assumptions = [
        f"База {BASE_YEAR} года использует опубликованные ставки перевозчиков на подходящих направлениях. "
        "Для авто и ж/д без опубликованной базы используются сценарные ставки. "
        "Частота и доступные мощности моделируются.",
        "Для обработки и хранения при отсутствии собственного ряда используется сводный индекс грузовых тарифов как сценарное приближение.",
        "НДС по модели базы сохраняется. Все начисления приведены к рублям выбранного года.",
    ]
    if any(connection["reference_metadata"].get("scope") == "station_to_station"
           for row in visible for connection in row["connections"]):
        assumptions.append("Контейнерная ставка ТЛМ включает терминальные операции от станции до станции. "
                           "Подвоз к станции, доставка от неё и отдельно указанная охрана в оценку не входят.")
    if request.schedule is None:
        assumptions.append("Задан только год. Для ж/д смоделирована фаза суточного интервала; даты рейсов не выводятся. Для сезонных и датированных услуг нужен период или дата.")
    if request.max_delivery_hours is None:
        assumptions.append("Срок доставки не задан; технический горизонт симуляции — 90 суток.")
    has_air_forecast = any("air_median_rub" in row["forecast_price"] for row in visible)
    if has_air_forecast:
        assumptions.append("Авиа: прогноз по опубликованному тарифу «Якутии» и авианакладной. "
                           "Терминальные и агентские сборы требуют отдельной ставки. Расписание и вместимость сценарные.")
    result["warnings"] = assumptions + [warning for warning in result["warnings"]
                                        if warning.startswith(("Нет цепочки", "Поиск ограничен"))]
    result["forecast"] = dict(target_year=request.target_year, base_year=BASE_YEAR,
                              date_precision="date" if request.schedule else "year",
                              schedule_status="scenario" if request.schedule is None or best and best["schedule_status"] == "scenario" else "snapshot",
                              coverage_status="estimated" if best else ("partial" if has_air_forecast
                                  or has_air_market_price or has_land_market_price or continuous and any(
                                  row["known_cost_subtotal_rub"] is not None for row in visible) else "insufficient_data"),
                              indices_version=indices.version, model_version=MODEL_VERSION,
                              alternative=alternative, assumptions=assumptions)
    used_source_ids = {point["source_id"] for row in result["candidates"] for item in row["cost_items"] for point in item["index_factors"]}
    if used_source_ids:
        used_source_ids.update(indices.sources)
    for source_id in sorted(used_source_ids):
        result["sources"].append(indices.sources[source_id])
    for row in result["candidates"]:
        if any(item["index_factors"] for item in row["cost_items"]):
            row["source_ids"] = sorted(set(row["source_ids"]) | used_source_ids)
    return ForecastResponse.model_validate(result)
