"""Расчёт 0.2: расписание, загрузка по участкам, авто и мультимодальная цепочка."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from itertools import combinations
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.calculation_models import CalculationRequest
from backend.schedule import billing_weeks, build_schedule, week_key


def dec(value):
    return Decimal(str(value))


def money(value):
    return dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ceil_ratio(numerator, denominator):
    return int((dec(numerator) / dec(denominator)).to_integral_value(rounding=ROUND_CEILING))


class SchemeProblem(Exception):
    def __init__(self, status, reason, fields=()):
        self.status, self.reason, self.fields = status, reason, list(fields)


def needed(terms, key, *, zero=False, integer=False):
    value = terms.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 1e12 or (key.endswith("hours") and value > 87600) or (integer and value > 10000) or (not zero and value == 0) or (integer and int(value) != value):
        raise SchemeProblem("insufficient_data", "Нет корректного параметра тарифа", [key])
    return dec(value)


def load_catalog(path=None):
    path = path or Path(__file__).resolve().parent / "examples/catalog.json"
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(catalog, dict) or not isinstance(catalog.get("id"), str) or not isinstance(catalog.get("cargo_types"), dict) or not isinstance(catalog.get("sources"), list):
        raise ValueError("Некорректный серверный каталог расчёта")
    return catalog


def unload_at(item, request):
    return len(request.route) - 1 if item.unload_at is None else item.unload_at


def profile(request):
    rows = []
    for index in range(len(request.route) - 1):
        active = [item for item in request.cargoes if item.load_at <= index < unload_at(item, request)]
        rows.append({
            "leg": index, "origin": request.route[index].city, "destination": request.route[index + 1].city,
            "weight_tonnes": sum((dec(item.weight_tonnes) for item in active), Decimal(0)),
            "volume_m3": sum((dec(item.volume_m3) for item in active), Decimal(0)) if all(item.volume_m3 is not None for item in active) else None,
            "cargo_ids": [item.id for item in active],
        })
    return rows


def check_common(request, catalog, terms, events):
    if not isinstance(terms, dict):
        raise SchemeProblem("insufficient_data", "Нет условий выбранной схемы", ["tariff"])
    if terms.get("available") is False:
        raise SchemeProblem("infeasible", "Услуга недоступна")
    if terms.get("available") is not True:
        raise SchemeProblem("insufficient_data", "Не подтверждена доступность услуги", ["available"])
    sources = {source.get("id") for source in catalog["sources"]}
    if terms.get("source_id") not in sources:
        raise SchemeProblem("insufficient_data", "Не указан источник тарифа", ["source_id"])
    allowed = terms.get("allowed_cargo_types")
    if not isinstance(allowed, list):
        raise SchemeProblem("insufficient_data", "Не определены допустимые грузы", ["allowed_cargo_types"])
    for item in request.cargoes:
        if item.cargo_type not in catalog["cargo_types"]:
            raise SchemeProblem("insufficient_data", "Нет сведений о свойствах груза", ["cargo_types." + item.cargo_type])
        if item.cargo_type not in allowed:
            raise SchemeProblem("infeasible", "Услуга не подходит для выбранного груза: " + item.cargo_type)
    for left, right in combinations(request.cargoes, 2):
        overlap = max(left.load_at, right.load_at) < min(unload_at(left, request), unload_at(right, request))
        if not overlap and left.cargo_type != right.cargo_type:
            left_profile = catalog["cargo_types"][left.cargo_type].get("handling_profile")
            right_profile = catalog["cargo_types"][right.cargo_type].get("handling_profile")
            if not left_profile or left_profile != right_profile:
                raise SchemeProblem("insufficient_data", "Для последовательной перевозки разных категорий нужен план подготовки/очистки кузова", ["cleaning_transition_plan"])
        if overlap and left.cargo_type != right.cargo_type:
            key = "|".join(sorted((left.cargo_type, right.cargo_type)))
            compatible = catalog.get("compatibility", {}).get(key)
            if compatible is False:
                raise SchemeProblem("infeasible", "Грузы несовместимы для совместной загрузки: " + key)
            if compatible is not True:
                raise SchemeProblem("insufficient_data", "Неизвестна совместимость грузов", ["compatibility." + key])
    try:
        first, last = date.fromisoformat(terms["valid_from"]), date.fromisoformat(terms["valid_until"])
        zone = ZoneInfo(terms["tariff_timezone"])
    except (KeyError, TypeError, ValueError, OSError):
        raise SchemeProblem("insufficient_data", "Нет корректного периода действия тарифа", ["valid_from", "valid_until", "tariff_timezone"])
    if first > last or any(not first <= event.astimezone(zone).date() <= last for event in events):
        raise SchemeProblem("insufficient_data", "Нет тарифа, действующего на все нужные даты", ["valid_tariff_for_period"])


def fleet_size(request, rows, terms, mass_key="capacity_tonnes", volume_key="capacity_m3"):
    mass_capacity = needed(terms, mass_key)
    for item in request.cargoes:
        if not item.splittable and dec(item.weight_tonnes) > mass_capacity:
            raise SchemeProblem("infeasible", "Неделимая позиция тяжелее вместимости транспортной единицы: " + item.id)
    count = max(1, max(ceil_ratio(row["weight_tonnes"], mass_capacity) for row in rows))
    if request.capacity_basis == "mass_and_volume" or terms.get(volume_key) is not None:
        volume_capacity = needed(terms, volume_key)
        for item in request.cargoes:
            if not item.splittable and item.volume_m3 is not None and dec(item.volume_m3) > volume_capacity:
                raise SchemeProblem("infeasible", "Неделимая позиция объёмнее транспортной единицы: " + item.id)
        count = max([count] + [ceil_ratio(row["volume_m3"], volume_capacity) for row in rows if row["volume_m3"] is not None])
    # Деление суммарной массы — недостаточное доказательство укладки неделимых объектов.
    if count > 1 and any(not item.splittable for item in request.cargoes):
        raise SchemeProblem("insufficient_data", "Для неделимого груза нужен отдельный план размещения по машинам", ["indivisible_load_plan"])
    return count


def finish(result, components, volume, duration, deadline):
    components = {key: money(value) for key, value in components.items()}
    total = sum(components.values(), Decimal(0))
    result.update({
        "total_cost_rub": float(total), "cost_rub_per_tonne": float(money(total / volume)),
        "max_delivery_hours": round(float(duration), 6),
        "status": "feasible" if float(duration) <= deadline + 1e-9 else "infeasible",
        "reason": "Условия расчётной модели соблюдены" if float(duration) <= deadline + 1e-9 else "Превышен максимальный срок от готовности груза",
        "breakdown_rub": {key: float(value) for key, value in components.items()},
    })
    return result


def calculate_road(request, catalog, events, start, end, rows, volume, scheme):
    terms = catalog.get(scheme)
    check_common(request, catalog, terms, events)
    quotes = []
    for row in rows:
        found = [leg for leg in terms.get("legs", []) if leg.get("origin", "").casefold() == row["origin"].casefold() and leg.get("destination", "").casefold() == row["destination"].casefold()]
        if len(found) != 1:
            raise SchemeProblem("insufficient_data", "Нет однозначного тарифа для участка маршрута", [f"legs.{row['origin']}->{row['destination']}"])
        quotes.append(found[0])
    vehicles = fleet_size(request, rows, terms)
    available = needed(terms, "available_vehicles_per_departure", zero=True, integer=True)
    if vehicles > available:
        raise SchemeProblem("infeasible", "Требуется больше параллельных машин, чем доступно у перевозчика")
    rates = [needed(quote, "rate_rub_per_vehicle", zero=True) for quote in quotes]
    travel = [needed(quote, "travel_hours", zero=True) for quote in quotes]
    wait = needed(terms, "dispatch_wait_hours", zero=True)
    completion, elapsed = [], wait
    for index, point in enumerate(request.route):
        if index:
            elapsed += travel[index - 1]
        elapsed += dec(point.service_hours)
        completion.append(elapsed)
    dispatches = [event + timedelta(hours=float(completion[0])) for event in events]
    check_common(request, catalog, terms, dispatches)
    durations = {item.id: completion[unload_at(item, request)] for item in request.cargoes}
    physical = vehicles * len(events)
    paid = physical
    detail = {"vehicles_per_departure": vehicles, "cargo_delivery_hours": {key: float(value) for key, value in durations.items()}, "fleet_policy": "fixed_fleet_entire_route", "dispatch_at": [event.isoformat() for event in dispatches]}
    fixed = Decimal(0)
    if scheme == "contract_road":
        minimum = int(needed(terms, "minimum_paid_trips_per_week", zero=True, integer=True))
        if terms.get("minimum_policy") != "full_iso_week":
            raise SchemeProblem("insufficient_data", "Не определено правило договорного минимума", ["minimum_policy"])
        try:
            billing_end = max(end, dispatches[-1] + timedelta(microseconds=1))
            weeks, zone = billing_weeks(start, billing_end, terms["billing_timezone"])
        except (KeyError, ValueError, OSError):
            raise SchemeProblem("insufficient_data", "Не определён часовой пояс договорной недели", ["billing_timezone"])
        check_common(request, catalog, terms, [start, billing_end - timedelta(microseconds=1)])
        counts = Counter(week_key(event, zone) for event in dispatches)
        billing = [{"week_start": week, "actual_trips": counts[week] * vehicles, "paid_trips": max(counts[week] * vehicles, minimum)} for week in weeks]
        paid = sum(item["paid_trips"] for item in billing)
        detail["billing_weeks"] = billing
        fixed = needed(terms, "fixed_cost_rub", zero=True)
    components = {
        "transport": sum(rates) * paid,
        "intermediate_stops": needed(terms, "stop_fee_rub_per_vehicle", zero=True) * (len(request.route) - 2) * physical,
        "cargo_handling": needed(terms, "handling_rub_per_tonne", zero=True) * volume,
        "fixed_contract": fixed,
    }
    return finish({"scheme": scheme, "departures": len(events), "physical_trips": physical, "paid_trips": paid, "source_ids": [terms["source_id"]], "details": detail}, components, volume, max(durations.values()), request.max_delivery_hours)


def rail_departure(ready, terms):
    if terms.get("departure_mode") == "on_demand":
        return ready
    if terms.get("departure_mode") != "interval":
        raise SchemeProblem("insufficient_data", "Нет расписания ж/д отправлений", ["departure_mode"])
    interval = needed(terms, "departure_every_hours")
    try:
        anchor = datetime.fromisoformat(terms["departure_anchor"].replace("Z", "+00:00"))
        if anchor.utcoffset() is None or not 2000 <= anchor.year <= 2100:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise SchemeProblem("insufficient_data", "Нет даты начала ж/д расписания с часовым поясом", ["departure_anchor"])
    seconds = float(interval) * 3600
    steps = max(0, math.ceil((ready - anchor).total_seconds() / seconds))
    return anchor + timedelta(seconds=steps * seconds)


def calculate_rail(request, catalog, events, volume):
    terms = catalog.get("road_rail_road")
    check_common(request, catalog, terms, events)
    if len(request.route) != 2 or any(item.load_at != 0 or unload_at(item, request) != 1 for item in request.cargoes):
        raise SchemeProblem("insufficient_data", "Для ж/д схемы с промежуточными пунктами нужен отдельный маршрутный тариф", ["rail_route_with_intermediate_stops"])
    if terms.get("origin", "").casefold() != request.route[0].city.casefold() or terms.get("destination", "").casefold() != request.route[-1].city.casefold():
        raise SchemeProblem("insufficient_data", "Нет ж/д услуги на этом направлении", ["rail_route"])
    minimum = needed(terms, "minimum_batch_tonnes")
    maximum_units = int(needed(terms, "max_units_per_departure", zero=True, integer=True))
    maximum_first = int(needed(terms, "max_first_mile_vehicles_per_departure", zero=True, integer=True))
    maximum_last = int(needed(terms, "max_last_mile_vehicles_per_departure", zero=True, integer=True))
    mass = sum((dec(item.weight_tonnes) for item in request.cargoes), Decimal(0))
    space = sum((dec(item.volume_m3) for item in request.cargoes), Decimal(0)) if all(item.volume_m3 is not None for item in request.cargoes) else None
    before = needed(terms, "before_rail_hours", zero=True)
    after = needed(terms, "rail_and_after_hours", zero=True)
    # Проверяем тарифы до перебора, чтобы не скрывать недостающие данные как невозможность схемы.
    prices = {key: needed(terms, key, zero=True) for key in (
        "first_mile_rub_per_vehicle", "last_mile_rub_per_vehicle", "rail_rub_per_unit", "terminal_rub_per_unit", "storage_rub_per_tonne_day", "terminal_storage_rub_per_tonne_day")}
    for key in ("capacity_tonnes", "first_mile_capacity_tonnes", "last_mile_capacity_tonnes"):
        needed(terms, key)
    if request.capacity_basis == "mass_and_volume":
        for key in ("capacity_m3", "first_mile_capacity_m3", "last_mile_capacity_m3"):
            needed(terms, key)
    rail_departure(events[0], terms)
    candidates, unknown = [], None
    for group_size in range(1, len(events) + 1):
        groups = [events[index:index + group_size] for index in range(0, len(events), group_size)]
        if any(mass * len(group) < minimum for group in groups):
            continue
        components = defaultdict(Decimal)
        batches, durations, concurrent_units, concurrent_last = [], [], Counter(), Counter()
        capacity_exceeded = False
        for group in groups:
            batch_mass = mass * len(group)
            row = {"weight_tonnes": batch_mass, "volume_m3": space * len(group) if space is not None else None}
            try:
                units = fleet_size(request, [row], terms)
                first = fleet_size(request, [row], terms, "first_mile_capacity_tonnes", "first_mile_capacity_m3")
                last = fleet_size(request, [row], terms, "last_mile_capacity_tonnes", "last_mile_capacity_m3")
            except SchemeProblem as problem:
                unknown = problem
                capacity_exceeded = True
                break
            all_ready = group[-1]
            if first > maximum_first:
                capacity_exceeded = True
                break
            rail_ready = all_ready + timedelta(hours=float(before) + request.route[0].service_hours)
            departure = rail_departure(rail_ready, terms)
            check_common(request, catalog, terms, [departure])
            concurrent_units[departure] += units
            concurrent_last[departure] += last
            if concurrent_units[departure] > maximum_units or concurrent_last[departure] > maximum_last:
                capacity_exceeded = True
                break
            delivered = departure + timedelta(hours=float(after) + request.route[-1].service_hours)
            waiting_days = sum((dec((all_ready - event).total_seconds()) / Decimal(86400) for event in group), Decimal(0))
            terminal_days = dec((departure - rail_ready).total_seconds()) / Decimal(86400)
            components["first_mile"] += first * prices["first_mile_rub_per_vehicle"]
            components["last_mile"] += last * prices["last_mile_rub_per_vehicle"]
            components["rail"] += units * prices["rail_rub_per_unit"]
            components["terminals"] += units * prices["terminal_rub_per_unit"]
            components["accumulation_storage"] += mass * waiting_days * prices["storage_rub_per_tonne_day"]
            components["terminal_storage"] += batch_mass * terminal_days * prices["terminal_storage_rub_per_tonne_day"]
            duration = (delivered - group[0]).total_seconds() / 3600
            durations.append(duration)
            batches.append({"shipments": len(group), "weight_tonnes": float(batch_mass), "units": units, "first_mile_vehicles": first, "last_mile_vehicles": last, "ready_at": all_ready.isoformat(), "rail_departure_at": departure.astimezone(timezone.utc).isoformat(), "delivered_at": delivered.astimezone(timezone.utc).isoformat(), "max_delivery_hours": round(duration, 6)})
        if capacity_exceeded:
            continue
        result = finish({"scheme": "road_rail_road", "departures": len(groups), "physical_trips": sum(item["first_mile_vehicles"] + item["last_mile_vehicles"] for item in batches), "source_ids": [terms["source_id"]], "details": {"group_size": group_size, "batches": batches, "search_scope": "equal_groups_with_final_remainder"}}, components, volume, max(durations), request.max_delivery_hours)
        candidates.append(result)
    if not candidates:
        if unknown:
            raise unknown
        raise SchemeProblem("infeasible", "Невозможно перевезти весь объём: минимальная партия, остаток или доступная ж/д мощность")
    feasible = [candidate for candidate in candidates if candidate["status"] == "feasible"]
    if feasible:
        return min(feasible, key=lambda result: (result["total_cost_rub"], result["max_delivery_hours"]))
    return min(candidates, key=lambda result: (result["max_delivery_hours"], result["total_cost_rub"]))


def compare_transport(payload, catalog=None):
    request = payload if isinstance(payload, CalculationRequest) else CalculationRequest.model_validate(payload)
    catalog = catalog if catalog is not None else load_catalog()
    if request.dataset_id != catalog["id"]:
        raise ValueError("Неизвестный dataset_id: выберите доступный серверный набор")
    # Compatibility adapter for the original three-scheme form: cargo is free text here too.
    from backend.network.cargo import normalized_name
    import hashlib
    original_request = request
    questions, cargoes = [], []
    for original in request.cargoes:
        item = original.model_copy(deep=True)
        if item.name is not None:
            matches = [key for key, value in catalog["cargo_types"].items()
                       if normalized_name(item.name) in {normalized_name(alias) for alias in [key, value.get("name",key), *value.get("aliases",[])]}]
            if len(matches) == 1 and (item.cargo_type is None or item.cargo_type == matches[0]):
                item.cargo_type = matches[0]
            else:
                item.cargo_type = "unresolved-" + hashlib.sha256(item.name.encode()).hexdigest()[:16]
                questions.append({"cargo_id":item.id,"fields":["name"],"code":"cargo_requires_details",
                                  "message":"Нужно уточнить свойства и допуск груза; для подробного ввода используйте сетевой расчёт с engine=network"})
        cargoes.append(item)
    request = request.model_copy(update={"cargoes":cargoes})
    events, start, end = build_schedule(request.schedule)
    rows = profile(request)
    mass = sum((dec(item.weight_tonnes) for item in request.cargoes), Decimal(0))
    volume = mass * len(events)
    schemes = []
    for scheme in ("spot_road", "contract_road", "road_rail_road"):
        try:
            result = calculate_rail(request, catalog, events, volume) if scheme == "road_rail_road" else calculate_road(request, catalog, events, start, end, rows, volume, scheme)
        except SchemeProblem as problem:
            result = {"scheme": scheme, "status": problem.status, "reason": problem.reason, "missing_fields": problem.fields}
        schemes.append(result)
    base_cost = schemes[0].get("total_cost_rub")
    for scheme in schemes:
        if base_cost is not None and "total_cost_rub" in scheme:
            savings = money(dec(base_cost) - dec(scheme["total_cost_rub"]))
            scheme["savings_vs_base_rub"] = float(savings)
            if base_cost > 0:
                scheme["savings_vs_base_percent"] = float(money(savings / dec(base_cost) * 100))
    eligible = [scheme for scheme in schemes if scheme["status"] == "feasible"]
    best = min(eligible, key=lambda scheme: (scheme["total_cost_rub"], scheme["max_delivery_hours"])) if eligible else None
    weeks, zone = billing_weeks(start, end, "Europe/Moscow")
    counts = Counter(week_key(event, zone) for event in events)
    warnings = []
    if catalog.get("status") == "synthetic":
        warnings.append("Все тарифы, совместимость и доступные мощности в этом наборе вымышленные. Вывод действителен только при заданных условиях.")
    if request.capacity_basis == "mass_only":
        warnings.append("Режим mass_only допускает оценку только по массе при отсутствии объёмов. Известные объёмы проверяются, но полнота проверки объёма кузова и фактическая укладка не гарантируются.")
    if any(point.service_hours == 0 for point in request.route):
        warnings.append("Для части пунктов задано нулевое время обработки; убедитесь, что это допустимо или уже включено в данные перевозчика.")
    if request.schedule.mode == "regular":
        warnings.append("Готовность распределена равномерно внутри каждого интервала. Для точных дат используйте explicit.")
    return {
        "schema_version": "0.2", "calculation_mode": "scenario", "request": original_request.model_dump(mode="json"),
        "input_status": "needs_clarification" if questions else "ready", "clarifications": questions,
        "dataset_id": catalog["id"], "data_status": catalog.get("status", "unspecified"),
        "shipments": len(events), "total_volume_tonnes": float(volume),
        "planning_period": {"start_at": start.isoformat(), "end_at": end.isoformat(), "end_exclusive": True},
        "ready_at": [event.isoformat() for event in events],
        "volume_by_week": [{"week_start": week, "timezone": "Europe/Moscow", "shipments": counts[week], "tonnes": float(mass * counts[week])} for week in weeks],
        "load_profile": [{key: float(value) if isinstance(value, Decimal) else value for key, value in row.items()} for row in rows],
        "schemes": schemes, "recommendation": best["scheme"] if best else None,
        "recommendation_scope": "Среди рассчитанных схем, при явно указанных допущениях и условиях набора данных",
        "assumptions": [
            "Это плановый сценарий, не статистический прогноз по истории",
            "Все позиции каждой отправки готовы в момент события расписания, включая грузы для промежуточной погрузки",
            "Срок каждой позиции считается от её готовности до завершения обработки в пункте выгрузки",
            "Автопарк фиксирован на весь маршрут: машины проходят все участки, в том числе пустыми",
            "Делимый груз можно распределить равномерно; геометрическая укладка и размеры упаковки не моделируются",
            "Сервисные времена не перекрываются; работа нескольких машин в пункте предполагается параллельной",
            "Стоимость считается для всех отправок периода, даже если доставка заканчивается после его конца",
        ],
        "warnings": warnings, "sources": catalog["sources"],
    }
