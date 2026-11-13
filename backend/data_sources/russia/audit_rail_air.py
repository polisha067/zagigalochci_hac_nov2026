"""Count directional budget coverage and probe geographically diverse requests."""

from collections import Counter
from decimal import Decimal
from math import prod
from unittest.mock import patch

from backend.data_sources.russia.audit_coverage import INDEPENDENT, request
from backend.network.air_market import air_rates, air_tariff_rows, compatible_airports
from backend.network.coverage_catalog import settlement_catalog
from backend.network.delivery_forecast import calculate_forecast
from backend.network.forecast_indices import load_indices
from backend.network.land_market import calibration, price_leg, rail_connections, rail_services
from backend.network.published_land import additional_rail_tariffs
from backend.network.russia import RussiaNetwork
from backend.transport_calculator import money


PROBES = [
    ("Москва", "Братск"), ("Москва", "Инта"), ("Москва", "Нижний Бестях, Республика Саха (Якутия)"),
    ("Санкт-Петербург", "Хабаровск"), ("Владивосток", "Нижнекамск"), ("Новороссийск", "Владивосток"),
    ("Новосибирск", "Владивосток"), ("Красноярск", "Калининград"), ("Владивосток", "Анадырь"),
    ("Екатеринбург", "Архангельск"), ("Санкт-Петербург", "Нарьян-Мар"), ("Санкт-Петербург", "Кызыл"),
    ("Москва", "Екатеринбург", "Красноярск"), ("Новосибирск", "Москва", "Мурманск"),
    ("Брянск", "Оренбург"), ("Воронеж", "Новокузнецк"), *INDEPENDENT,
]


def summarize(pairs, priority):
    endpoints = {city for pair in pairs for city in pair}
    return len(pairs), sum(set(pair) <= priority for pair in pairs), len(endpoints)


def run():
    network = RussiaNetwork(registry=settlement_catalog())
    indices = load_indices()
    priority = {network.resolve(row["gar_id"]) for key in ("regional_centres", "required_population_50000")
                for row in network.registry[key]}
    rail_request = request(("Москва", "Братск"), modes=("rail",)).model_copy(
        update={"data_policy": "allow_assumptions"})
    air_request = request(("Москва", "Мурманск"), modes=("air",)).model_copy(
        update={"data_policy": "allow_assumptions", "cargoes": [
            rail_request.cargoes[0].model_copy(update={"weight_tonnes": 0.1, "volume_m3": 0.25})]})
    print("priority", len(priority), len(priority) * (len(priority) - 1))
    new_sources, rows = additional_rail_tariffs()
    _, tariffs, rejected = network.published_tariffs
    existing_rail = {pair for pair, rows in tariffs.items()
                     if any(row["mode"] == "rail" and row["source_id"] not in new_sources for row in rows)}
    all_rail = {pair for pair, rows in tariffs.items() if any(row["mode"] == "rail" for row in rows)}
    services = rail_services(network)
    print("rail rows", Counter(row["source_id"] for row in rows),
          "rejected", [(row["origin"], row["destination"]) for row in rejected if row["source_id"] in new_sources])
    print("rail published", summarize(existing_rail, priority), summarize(all_rail, priority))
    print("rail verified services", len(services))
    direct_rail = set()
    for pair in all_rail | services.keys():
        km = network.edges.get(f"observation:rail:{pair[0]}:{pair[1]}", {}).get("distance_km")
        if any(price_leg(network, pair, "rail", size, rail_request, indices, calibration(), km,
                         "observation", services.get(pair)) for size in (20, 40)):
            direct_rail.add(pair)
    possible_rail_via = {(a, b) for a, hub in direct_rail for other, b in direct_rail
                         if hub == other and a != b and (a, b) not in direct_rail}
    rail_via = {pair: options for pair in possible_rail_via
                if (options := rail_connections(network, pair, rail_request, indices, calibration(), services, tariffs))}
    print("rail with budget", summarize(direct_rail, priority), "via only", summarize(rail_via, priority))
    print("rail via with comparable taxes", summarize(
        [pair for pair, options in rail_via.items() if any(row["movement_rub"] is not None for row in options)], priority))
    old_air = {(network.hubs[edge["from_hub"]]["settlement_id"], network.hubs[edge["to_hub"]]["settlement_id"])
               for edge in network.edges.values() if edge["kind"] == "provider_air"}
    air = air_rates(network, air_request, indices)
    via = {(a, b) for (a, mid), arriving in air.items() for (hub, b), leaving in air.items()
           if hub == mid and a != b and (a, b) not in air
           and any(compatible_airports((x, y)) for x in arriving for y in leaving)}
    imported = Counter(source["name"] for _, _, _, _, _, _, _, source in air_tariff_rows(network))
    print("air rows", imported)
    print("air unresolved", sorted({(origin, destination) for origin, _, destination, *_ in air_tariff_rows(network)
                                   if not network.resolve(origin) or not network.resolve(destination)}))
    print("air baseline", summarize(old_air, priority), "with tariff", summarize(air.keys(), priority),
          "via only", summarize(via, priority), "union", summarize(air.keys() | via, priority))
    outcomes = Counter()
    with patch.dict("os.environ", {"COVERAGE_VALHALLA_URL": ""}):
        for route in PROBES:
            args = {"origin": route[0], "destination": route[-1],
                    "stops": request(route).stops}
            land = calculate_forecast(rail_request.model_copy(update={
                **args, "allowed_modes": ["road", "rail"]}), network)
            flight = calculate_forecast(air_request.model_copy(update=args), network)
            kinds = {tuple(part["mode"] for part in row["legs"])
                     for row in (land.land_market or {}).get("routes", [])}
            outcomes["road"] += ("road",) * (len(route) - 1) in kinds
            outcomes["rail"] += any(all(part == "rail" for part in path) for path in kinds)
            outcomes["air"] += bool(flight.air_market and flight.air_market["routes"])
            outcomes["any"] += bool(kinds or (flight.air_market and flight.air_market["routes"]))
            selected = (flight.air_market or {}).get("routes", [])[:1]
            for option in selected:
                for leg in option["legs"]:
                    factor = prod(Decimal(str(point["factor"])) for point in leg["index_factors"])
                    assert abs(money(Decimal(str(leg["base_rub"])) * factor) - Decimal(str(leg["forecast_rub"]))) <= Decimal(".02")
            print(" → ".join(route), sorted(kinds), [(row["forecast_rub"], row["transfer_count"]) for row in selected])
    print("probe count", len(PROBES), outcomes)


if __name__ == "__main__":
    run()
