"""Compare delivery hypotheses using the A1 route benchmark."""
from copy import deepcopy
from decimal import Decimal
from functools import lru_cache
import json
from pathlib import Path

from .a1_comparison import BASE_RATE, CONTRACT_RATE, catalog_for, compare_year
from .a1_forecast import forecast_route
from .transport_calculator import compare_transport

DATA = Path(__file__).resolve().parent / "data_sources"
CONNECTIVITY = DATA / "russia/connectivity.json"
PILOT_REFERENCE = DATA / "a1_forecast/kemerovo-reference.json"
MAIN_DEADLINE_DAYS = 14


def pilot_links(snapshot, route):
    cities = {city["name"]: city["id"] for city in snapshot["settlements"]}
    endpoints = (cities[route["origin"]], cities[route["destination"]])
    return {
        mode: {"km": km, "hours": hours}
        for origin, destination, mode, km, hours, reason in snapshot["connections"]
        if (origin, destination) == endpoints and mode in ("road", "rail") and not reason
    }


def corridor_catalog(route, anchor, reference, year):
    endpoints = {key: route[key] for key in ("origin", "destination")}
    catalog = catalog_for(endpoints, reference, year)
    catalog["id"] = f"synthetic-corridor-{route['route_id']}-{year}"
    catalog["sources"][0].update(
        description="Синтетические ставки и операции. Кемеровский пилот масштабирован по снимку связности.",
    )
    modes = route["connectivity_reference"]
    road_distance_ratio = modes["road"]["km"] / anchor["road"]["km"]
    road_hours_ratio = modes["road"]["hours"] / anchor["road"]["hours"]
    rail_distance_ratio = modes["rail"]["km"] / anchor["rail"]["km"]
    rail_hours_ratio = modes["rail"]["hours"] / anchor["rail"]["hours"]
    spot_rate = round(BASE_RATE * (0.10 + 0.90 * road_distance_ratio))
    for scheme, rate in (
        ("spot_road", spot_rate),
        ("contract_road", round(spot_rate * CONTRACT_RATE / BASE_RATE)),
    ):
        catalog[scheme]["legs"][0].update(
            rate_rub_per_vehicle=rate,
            travel_hours=round(6 + 114 * road_hours_ratio, 2),
        )
    catalog["road_rail_road"].update(
        rail_rub_per_unit=round(reference["rail_rub_per_kg"] * 40_000 * rail_distance_ratio),
        rail_and_after_hours=round(12 + reference["rail_published_days"] * 24 * rail_hours_ratio, 2),
    )
    return catalog


def scenario_result(forecast, catalog, days, year):
    result = compare_year(compare_transport, forecast, catalog, days, year)
    annual = result["annual"]
    contract = annual["contract_road"]
    spot = annual["spot_road"]
    paid = contract["paid_road_trips"]
    if result["forecast_tonnes"] and paid and spot["feasible"] and contract["feasible"]:
        contract["break_even_rate_rub_per_trip"] = round(spot["cost_rub"] / paid, 2)
    return annual


def compare_corridor(route, anchor, reference, year):
    endpoints = {key: route[key] for key in ("origin", "destination")}
    prediction = forecast_route(route["route_id"], year, year, "month")
    forecast = {
        "route": endpoints,
        "forecast_months": [
            {"month": row["period"], "forecast_tonnes": row["forecast_tonnes"]}
            for row in prediction["periods"]
        ],
        "forecast_yearly_tonnes": {str(year): prediction["forecast_tonnes"]},
    }
    catalog = corridor_catalog(route, anchor, reference, year)
    sensitivity = []
    for label, days, contract_discount, rail_multiplier in (
        ("Срок 7 дней", 7, None, None),
        ("Срок 18 дней", 18, None, None),
        ("Договорная скидка 5%", MAIN_DEADLINE_DAYS, 0.05, None),
        ("Договорная скидка 20%", MAIN_DEADLINE_DAYS, 0.20, None),
        ("Ж/д ставка −20%", MAIN_DEADLINE_DAYS, None, 0.80),
        ("Ж/д ставка +20%", MAIN_DEADLINE_DAYS, None, 1.20),
    ):
        variant = deepcopy(catalog)
        if contract_discount is not None:
            variant["contract_road"]["legs"][0]["rate_rub_per_vehicle"] = round(
                catalog["spot_road"]["legs"][0]["rate_rub_per_vehicle"] * (1 - contract_discount)
            )
        if rail_multiplier is not None:
            variant["road_rail_road"]["rail_rub_per_unit"] = round(
                catalog["road_rail_road"]["rail_rub_per_unit"] * rail_multiplier
            )
        annual = scenario_result(forecast, variant, days, year)
        sensitivity.append({
            "scenario": label, "deadline_days": days,
            "contract_rate_rub_per_trip": variant["contract_road"]["legs"][0]["rate_rub_per_vehicle"],
            "rail_rate_rub_per_unit": variant["road_rail_road"]["rail_rub_per_unit"],
            "annual": annual,
        })
    for multiplier in (Decimal("0.5"), Decimal("2")):
        variant = deepcopy(forecast)
        total = Decimal(0)
        for month in variant["forecast_months"]:
            tonnes = Decimal(str(month["forecast_tonnes"])) * multiplier
            month["forecast_tonnes"] = float(tonnes)
            total += tonnes
        variant["forecast_yearly_tonnes"][str(year)] = float(total)
        sensitivity.append({
            "scenario": f"Объём ×{multiplier}", "deadline_days": MAIN_DEADLINE_DAYS,
            "forecast_tonnes": float(total),
            "annual": scenario_result(variant, catalog, MAIN_DEADLINE_DAYS, year),
        })
    return {
        "route_id": route["route_id"], **endpoints,
        "forecast_tonnes": prediction["forecast_tonnes"],
        "catalog": catalog,
        "annual": scenario_result(forecast, catalog, MAIN_DEADLINE_DAYS, year),
        "sensitivity": sensitivity,
    }


@lru_cache(maxsize=1)
def pilot_reference():
    pilot = json.loads(PILOT_REFERENCE.read_bytes())
    snapshot = json.loads(CONNECTIVITY.read_bytes())
    return {
        "route": pilot["route"],
        "connectivity": pilot_links(snapshot, pilot["route"]),
        "rail_reference": pilot["rail_reference"],
        "connectivity_source": snapshot["source"]["url"],
    }
