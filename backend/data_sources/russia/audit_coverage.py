"""Reproduce coverage counts and exercise the declared acceptance routes."""

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import json
from math import prod
from pathlib import Path

from backend.network.delivery_forecast import ForecastRequest, calculate_forecast
from backend.network.published_land import catalog, hub_catalog, volume_catalog
from backend.network.russia import RussiaNetwork, load_russia
from backend.transport_calculator import money


ACCEPTANCE = {
    "road": [
        ("Самара", "Альметьевск"), ("Новосибирск", "Барнаул"), ("Владивосток", "Уссурийск"),
        ("Краснодар", "Сочи"), ("Тюмень", "Курган"), ("Казань", "Набережные Челны"),
        ("Екатеринбург", "Нижний Тагил"), ("Томск", "Кемерово"), ("Пермь", "Ижевск"),
        ("Волгоград", "Астрахань"), ("Уфа", "Стерлитамак"), ("Челябинск", "Магнитогорск"),
    ],
    "hub_access": [
        ("Бердск", "Омск"), ("Искитим", "Томск"), ("Обь, Новосибирская область", "Кемерово"),
        ("Кольцово, Новосибирская область", "Барнаул"), ("Краснообск", "Иркутск"),
        ("Омск", "Бердск"), ("Барнаул", "Искитим"), ("Томск", "Краснообск"),
    ],
    "transfer": [
        ("Санкт-Петербург", "Новосибирск", "Иркутск"), ("Находка", "Новосибирск", "Томск"),
        ("Владивосток", "Новосибирск", "Барнаул"), ("Самара", "Екатеринбург", "Новосибирск"),
        ("Москва", "Новосибирск", "Красноярск"), ("Тюмень", "Новосибирск", "Хабаровск"),
    ],
    "remote": [
        ("Красноярск", "Дудинка"), ("Владивосток", "Петропавловск-Камчатский"),
        ("Хабаровск", "Южно-Сахалинск"), ("Якутск", "Магадан"),
    ],
}
INDEPENDENT = [
    ("Рязань", "Кострома"), ("Вологда", "Петрозаводск"), ("Майкоп", "Элиста"),
    ("Нальчик", "Черкесск"), ("Саранск", "Йошкар-Ола"), ("Курган", "Ханты-Мансийск"),
    ("Кызыл", "Горно-Алтайск"), ("Биробиджан", "Благовещенск, Амурская область"),
]


def request(route, year=2030, modes=("road", "rail")):
    return ForecastRequest(origin=route[0], destination=route[-1], target_year=year,
                           stops=[{"settlement": point, "action": "visit"} for point in route[1:-1]],
                           cargoes=[{"id": "flour", "name": "Мука", "weight_tonnes": 10,
                                     "volume_m3": 20, "packaging": "bags", "splittable": True}],
                           allowed_modes=list(modes), data_policy="verified_only")


def summarize(network, category, route, year=2030, modes=("road", "rail")):
    result = calculate_forecast(request(route, year, modes), network)
    coverage = result.published_coverage or {}
    quoted = []
    for row in result.candidates:
        rate = row["published_rate"]
        if rate["forecast_amount_rub"] is not None:
            factor = prod((Decimal(str(point["factor"])) for point in rate["index_factors"]), start=Decimal(1))
            expected = money(Decimal(rate["published_amount_rub"]) * factor)
            assert expected == Decimal(str(rate["forecast_amount_rub"])), (route, expected, rate)
        quoted.append({key: rate[key] for key in (
            "origin", "destination", "mode", "source_id", "source_url", "equipment", "observed_on",
            "vat", "published_amount_rub", "forecast_amount_rub", "index_factors"
        )} | {"partial_rub": row["known_cost_subtotal_rub"]})
    return {
        "category": category, "route": route, "year": year, "status": result.status,
        "clarifications": result.clarifications, "road_path": coverage.get("truck_path"),
        "terminals": [{"id": hub["id"], "path": hub["path"]} for hub in coverage.get("terminals", [])],
        "quotes": quoted, "complete_quote": bool(result.recommendation),
        "volume_status": coverage.get("volume", {}).get("status"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    old, new = RussiaNetwork(), load_russia()
    sources, pairs, unresolved = new.published_tariffs
    old_pairs = {(old.hubs[edge["from_hub"]]["settlement_id"], old.hubs[edge["to_hub"]]["settlement_id"])
                 for edge in old.edges.values() if edge["mode"] in {"road", "rail"}
                 and edge.get("reference_metadata", {}).get("tariff_source_id") and "quote" in edge}
    per_mode = {mode: sum(any(rate["mode"] == mode for rate in rows) for rows in pairs.values())
                for mode in ("road", "rail")}
    registry = new.registry
    by_region = defaultdict(Counter)
    for row in new.settlements.values():
        key = row.get("region_code", "unmatched")
        by_region[key]["localities"] += 1
        if row.get("latitude") is not None and row.get("longitude") is not None:
            by_region[key]["with_coordinates"] += 1
    district_by_code = {row["region_code"]: row["district"] for row in registry["regional_centres"]}
    by_district = defaultdict(Counter)
    for code, counts in by_region.items():
        by_district[district_by_code.get(code, "unmatched")].update(counts)
    checked = [summarize(new, category, route, modes=("road",) if category == "road" else ("road", "rail"))
               for category, routes in ACCEPTANCE.items() for route in routes]
    heldout = [summarize(new, "independent", route) for route in INDEPENDENT]
    forecast_examples = [summarize(new, mode, route, year, (mode,))
                         for mode, route in (("road", ("Самара", "Альметьевск")),
                                             ("rail", ("Санкт-Петербург", "Новосибирск")))
                         for year in (2028, 2030)]
    report = {
        "baseline": {"settlements": len(old.settlements), "with_coordinates": sum(
            row.get("latitude") is not None for row in old.settlements.values()), "priced_pairs_on_legacy_edges": len(old_pairs)},
        "expanded": {
            "settlements": len(new.settlements), "with_coordinates": sum(
                row.get("latitude") is not None for row in new.settlements.values()),
            "mapped_tariff_pairs_by_mode": per_mode,
            "mapped_tariff_pairs_road_or_rail": sum(any(row["mode"] for row in rows) for rows in pairs.values()),
            "confirmed_hubs_imported": len(hub_catalog()["hubs"]),
            "directed_volume_observations": 0, "aggregate_volume_observations": len(volume_catalog()["observations"]),
        },
        "localities_by_region": by_region,
        "localities_by_district": by_district,
        "census_50000": registry["required_population_50000"],
        "regional_centres": registry["regional_centres"],
        "published_sources": list(sources.values()),
        "unresolved_tariff_rows": [{"source": row["source_id"], "origin": row["origin"],
                                    "destination": row["destination"]} for row in unresolved],
        "rejected": catalog()["rejected"],
        "acceptance": checked,
        "independent": heldout,
        "forecast_examples": forecast_examples,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"baseline": report["baseline"], "expanded": report["expanded"],
                      "acceptance": len(checked), "independent": len(heldout)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
