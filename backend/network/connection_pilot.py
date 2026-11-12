"""Scenario calculation for one rail-to-road connection in Yekaterinburg."""

from decimal import Decimal, ROUND_CEILING
from math import prod
from typing import Annotated

from pydantic import Field

from backend.calculation_models import InputModel
from backend.transport_calculator import money


HOURS = Annotated[Decimal, Field(ge=0, le=720, allow_inf_nan=False)]
RUBLES = Annotated[Decimal, Field(ge=0, le=100_000_000, allow_inf_nan=False)]


class ConnectionConditions(InputModel):
    buffer_hours: HOURS = Decimal(4)
    delay_hours: HOURS = Decimal(6)
    next_slot_hours: HOURS | None = None
    free_truck_wait_hours: HOURS = Decimal(0)
    free_storage_hours: HOURS = Decimal(0)
    transfer_rub: RUBLES | None = None
    truck_wait_rub_per_hour: RUBLES | None = None
    storage_rub_per_day: RUBLES | None = None
    rebooking_rub: RUBLES | None = None


ROAD_PRICE_URL = "https://logistic-avto.ru/gruzoperevozki-po-rf/ekaterinburg-krasnoyarsk/"
TERMINAL = "https://www.tscont.ru/services/konteynernyy-terminal/"
TRUCK_10T_RUB = Decimal(85068)


def connection_pilot(row, route, request):
    if (route != ["Москва", "Екатеринбург", "Красноярск"] or row["modes"] != ["rail", "road"]
            or row["status"] != "feasible" or len(row["connections"]) != 2
            or len(request.cargoes) != 1 or row["stop_actions"][0]["action"] != "visit"
            or (request.schedule is not None and request.schedule.mode != "once")):
        return None
    cargo = request.cargoes[0]
    if (cargo.weight_tonnes != 10 or cargo.volume_m3 != 20 or cargo.packaging != "bags"
            or not (cargo.cargo_type == "flour" or (cargo.name or "").lower() in ("мука", "пшеничная мука"))):
        return None
    rail, road = row["connections"]
    if (rail["reference_metadata"].get("tariff_source_id") != "tlm-rail-2026"
            or road["reference_metadata"].get("tariff_source_id") != "logistic-avto-road-2026"):
        return None
    movement = [item for item in row["cost_items"] if item["kind"] == "transport"]
    if len(movement) != 2 or any(item["amount_rub"] is None or item["pricing"]["quantity"] != 1 for item in movement):
        return None
    subtotal = sum(Decimal(str(item["amount_rub"])) for item in movement)
    road_charge = next((item for item in movement if item["object_id"] == road["id"]), None)
    truck_10t_forecast = None
    if road_charge and road_charge["pricing"]["effective_price_year"] == 2026:
        base = TRUCK_10T_RUB
        if request.cost_basis == "excluding_vat":
            base = money(base / Decimal("1.22"))
        factor = prod((Decimal(str(point["factor"])) for point in road_charge["index_factors"]), start=Decimal(1))
        truck_10t_forecast = float(money(base * factor))
    conditions = request.connection_conditions or ConnectionConditions()
    sample_delays = [("planned", Decimal(0))]
    if conditions.buffer_hours > 0:
        sample_delays.append(("within_buffer", conditions.buffer_hours / 2))
    sample_delays.append(("requested", conditions.delay_hours))
    scenarios = [evaluate_connection(subtotal, conditions, name, delay) for name, delay in sample_delays]
    if conditions.delay_hours > conditions.buffer_hours:
        for name, hours_after_ready in (("pickup_after_6h", 6), ("pickup_after_24h", 24)):
            scenarios.append(evaluate_connection(
                subtotal, conditions, name, conditions.delay_hours,
                scenario_pickup=conditions.delay_hours + hours_after_ready,
            ))
    return {
        "movement_rub": float(subtotal),
        "conditions": conditions.model_dump(mode="json"),
        "scenarios": scenarios,
        "terminal_reference_url": TERMINAL,
        "truck_10t_reference_rub": float(TRUCK_10T_RUB),
        "truck_10t_forecast_rub": truck_10t_forecast,
        "truck_10t_reference_url": ROAD_PRICE_URL,
    }


def evaluate_connection(movement, conditions, name, delay, *, scenario_pickup=None):
    # Clock zero is the booked truck pickup, when the cargo was planned ready.
    # A slot exactly at the cutoff is still usable. Next slot is measured from zero.
    buffer = conditions.buffer_hours
    missed = delay > buffer
    next_slot = conditions.next_slot_hours if scenario_pickup is None else scenario_pickup
    pickup = delay
    if missed:
        pickup = next_slot if next_slot is not None and next_slot >= delay else None
    storage = max(Decimal(0), pickup - delay) if pickup is not None else None
    billable_days = (max(Decimal(0), storage - conditions.free_storage_hours) / 24).to_integral_value(
        rounding=ROUND_CEILING) if storage is not None else None
    billable_truck_hours = max(Decimal(0), min(delay, buffer) - conditions.free_truck_wait_hours)
    charges = [
        ("transfer", Decimal(1), conditions.transfer_rub),
        ("truck_wait", billable_truck_hours, conditions.truck_wait_rub_per_hour),
        ("storage", billable_days, conditions.storage_rub_per_day),
        ("rebooking", Decimal(missed), conditions.rebooking_rub),
    ]
    components = {key: (Decimal(0) if units == 0 else money(units * rate)
                        if units is not None and rate is not None else None)
                  for key, units, rate in charges}
    known = movement + sum(amount for amount in components.values() if amount is not None)
    missing = [key for key, amount in components.items() if amount is None]
    if pickup is None:
        missing.append("next_slot")
    return {
        "scenario": name, "delay_hours": float(delay), "buffer_remaining_hours": float(buffer - delay),
        "missed": missed, "extra_hours": float(pickup) if pickup is not None else None,
        "billable_truck_hours": float(billable_truck_hours),
        "billable_storage_days": int(billable_days) if billable_days is not None else None,
        "charges_rub": {key: float(amount) if amount is not None else None for key, amount in components.items()},
        "known_rub": float(known), "total_rub": float(known) if not missing else None, "missing": missing,
    }
