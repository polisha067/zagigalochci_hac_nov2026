"""Budget estimates using public benchmarks, separate from confirmed quotes."""

import json
from decimal import Decimal, ROUND_CEILING
from functools import cache

from backend.network.cargo import normalized_name
from backend.network.coverage_catalog import ROOT
from backend.network.published_land import price
from backend.transport_calculator import money


@cache
def market_snapshot():
    return json.loads((ROOT / "market_assumptions.json").read_text(encoding="utf-8"))


def road_budget(network, result, request, indices):
    """Estimate the main leg of a single direct shipment on the carrier's own base."""
    if (request.data_policy != "allow_assumptions" or not network.registry
            or "road" not in request.allowed_modes or request.stops or len(request.cargoes) != 1
            or result["shipments"] != 1 or result["clarifications"]
            or not all(result["settlements"])):
        return None
    cargo = request.cargoes[0]
    if (cargo.packaging not in ("bags", "boxes") or cargo.volume_m3 is None or cargo.volume_m3 <= 0
            or cargo.temperature_c or cargo.max_piece_dimensions_m):
        return None
    matches = [
        (key, row) for key, row in network.cargo_types.items()
        if (not cargo.name and key == cargo.cargo_type)
        or (cargo.name and normalized_name(cargo.name) in
            {normalized_name(name) for name in (row["name"], *row["aliases"])})
    ]
    if len(matches) > 1 or cargo.cargo_type and (not matches or cargo.cargo_type != matches[0][0]):
        return None
    known = matches[0][1] if matches else None
    profiles = {"dry_food", "dry_general"}
    if known and (known["handling_profile"] not in profiles or cargo.packaging not in known["packagings"]):
        return None
    props = cargo.properties
    if props and (props.phase != "solid" or props.hazardous or props.temperature_control_required
                  or props.handling_profile not in profiles
                  or known and props.handling_profile != known["handling_profile"]):
        return None
    if not known and not props:
        return None

    snapshot = market_snapshot()
    truck = snapshot["truck"]
    tonnes, volume = truck["max_tonnes"], truck["assumed_m3"]
    if cargo.max_piece_tonnes and cargo.max_piece_tonnes > tonnes:
        return None
    units = int(max(Decimal(str(cargo.weight_tonnes)) / tonnes,
                    Decimal(str(cargo.volume_m3)) / volume).to_integral_value(rounding=ROUND_CEILING))
    if units > 1 and cargo.splittable is not True:
        return None

    sources, pairs, _ = network.published_tariffs
    rows = [
        rate for rate in pairs.get(tuple(result["settlements"]), ())
        if rate["source_id"].startswith("logistic-avto-regional-")
        and rate["mode"] == "road" and rate["scope"] == "road_linehaul"
        and rate["unit"] == "vehicle" and rate["max_tonnes"] == tonnes
        and rate["max_m3"] is None and rate["vat"] == "unknown"
    ]
    if len(rows) != 1:
        return None
    rate = rows[0]
    source = sources[rate["source_id"]]
    if int(source["observed_on"][:4]) > request.target_year:
        return None
    projection = price(rate, source, request.target_year, indices)
    projected_unit = projection["forecast_amount_rub"]
    if projected_unit is None:
        return None
    return {
        "version": snapshot["version"], "origin": rate["origin"], "destination": rate["destination"],
        "target_year": request.target_year, "base_year": projection["base_year"], "trucks": units,
        "tonnes_per_truck": tonnes, "assumed_m3_per_truck": volume,
        "base_unit_rub": rate["amount_rub"], "forecast_unit_rub": projected_unit,
        "forecast_road_rub": float(money(Decimal(str(projected_unit)) * units)),
        "vat": "unknown", "tariff_source": source, "capacity_source": truck,
        "index_factors": projection["index_factors"],
    }


def connection_budget(tonnes, request, indices):
    """Project benchmark unit prices to the target year before filling the pilot."""
    snapshot = market_snapshot()
    handling, waiting, storage = (snapshot["connection"][name] for name in ("handling", "waiting", "storage"))
    gross = request.cost_basis == "including_vat"
    transfer = Decimal(handling["unpack_loose_20ft_rub_excluding_vat"]) + (
        Decimal(handling["load_rub_per_tonne_excluding_vat"]) * Decimal(str(tonnes))
    )
    if gross:
        transfer *= 1 + Decimal(handling["scenario_vat_percent"]) / 100
    wait = Decimal(waiting["rub_per_hour_including_vat" if gross else "rub_per_hour_excluding_vat"])
    store = Decimal(storage["rub_per_billable_day_including_vat"])
    if not gross:
        store /= 1 + Decimal(storage["vat_percent"]) / 100
    rates = {}
    for field, base, category, source, method in (
        ("transfer_rub", transfer, "handling", handling,
         "Выгрузка мешков без паллет из одного 20-футового контейнера и отдельная загрузка машины. "
         "Применять, если обе операции выставляются отдельно."),
        ("truck_wait_rub_per_hour", wait, "road", waiting,
         "Цена часа работы машины в Екатеринбурге взята как ориентир цены простоя."),
        ("storage_rub_per_day", store, "storage", storage,
         "Ориентир за платные сутки хранения гружёного контейнера. "
         "Льготный период считают по условиям выбранного терминала."),
    ):
        estimate = indices.estimate(category, source["base_year"], request.target_year)
        if estimate is not None:
            rates[field] = {
                "base_amount_rub": float(money(base)),
                "forecast_amount_rub": float(money(base * estimate.central)),
                "base_year": source["base_year"], "source_name": source["name"],
                "source_url": source["url"], "method": method,
                "index_factors": [point.as_dict() for point in estimate.points],
            }
    return {"version": snapshot["version"], "rates": rates} if rates else None
