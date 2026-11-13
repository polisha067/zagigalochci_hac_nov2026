"""Published land tariffs and optional truck routing, without operational assumptions."""

import json
import os
from collections import defaultdict
from datetime import date
from decimal import Decimal
from functools import cache
from itertools import permutations
from math import isfinite

import httpx

from backend.network.coverage_catalog import ROOT
from backend.network.forecast_indices import load_indices
from backend.network.tlm_tariff import ROAD_URL, URL, MOSCOW
from backend.transport_calculator import money


ROAD_GRAPH = {
    "id": "valhalla-truck", "name": "Valhalla, грузовой профиль; данные OpenStreetMap",
    "url": "https://www.openstreetmap.org/copyright", "roles": ["routing"], "status": "graph",
}


@cache
def catalog():
    return json.loads((ROOT / "coverage_tariffs.json").read_text(encoding="utf-8"))


@cache
def volume_catalog():
    return json.loads((ROOT / "coverage_volumes.json").read_text(encoding="utf-8"))


@cache
def hub_catalog():
    return json.loads((ROOT / "coverage_hubs.json").read_text(encoding="utf-8"))


def terminal_access(locations, request, router_status):
    if not {"rail", "road"}.issubset(request.allowed_modes) or any(
        cargo.temperature_c or cargo.properties and (
            cargo.properties.hazardous or cargo.properties.temperature_control_required)
        or cargo.cargo_type == "crude_oil" for cargo in request.cargoes):
        return []
    rows = []
    for operation, point in (("pickup", locations[0]), ("delivery", locations[-1])):
        for hub in hub_catalog()["hubs"]:
            if point.get("region") != hub["region"]:
                continue
            points = [point, hub] if operation == "pickup" else [hub, point]
            route = {"status": router_status} if router_status == "router_unavailable" else truck_path(points)
            rows.append(hub | {"operation": operation, "path": route})
    return sorted(rows, key=lambda row: (row["operation"], row["path"].get("navigation_hours", float("inf"))))


def provider_rows(network):
    """Reuse existing TLM releases alongside the new carrier catalog."""
    sources = {source["id"]: source for source in catalog()["sources"]}
    rows = list(catalog()["tariffs"])
    road, rail = network.tlm_road_tariff, network.tlm_tariff
    for identifier, title, url, release in (
        ("tlm-road-2026", "ТЛМ — тарифы на выделенную фуру", ROAD_URL, road),
        ("tlm-rail-2026", "ТЛМ — тарифы станция–станция", URL, rail),
    ):
        sources[identifier] = {"id": identifier, "name": title, "url": url, "roles": ["tariff"],
                               "status": "fact", "observed_on": release["observed_on"],
                               "version": release["version"]}
    for destination, rate in road["rows"]:
        rows.append({"origin": "Москва", "destination": destination, "mode": "road",
                     "equipment": "Выделенная фура", "unit": "vehicle",
                     "max_tonnes": road["vehicle_tonnes"], "max_m3": road["vehicle_m3"],
                     "amount_rub": rate, "vat": "included", "vat_percent": road["vat_percent"],
                     "scope": "road_linehaul", "source_id": "tlm-road-2026"})
    for destination, rate20, rate40, guard20, guard40, days in rail["rows"]:
        if days.endswith("+"):
            continue
        for size, rate, guard in ((20, rate20, guard20), (40, rate40, guard40)):
            rows.append({"origin": "Москва", "destination": "Улан-Удэ" if destination == "Тальцы (Улан-Удэ)" else destination,
                         "mode": "rail", "equipment": f"Контейнер {size} футов", "unit": "container",
                         "max_tonnes": None, "max_m3": None, "amount_rub": rate, "vat": "included",
                         "vat_percent": rail["vat_percent"], "security_rub": guard,
                         "valid_from": rail["valid_from"], "transit_days": [float(days)],
                         "scope": "station_to_station", "source_id": "tlm-rail-2026"})
    return sources, rows


def index_tariffs(network):
    sources, rows = provider_rows(network)
    pairs = defaultdict(list)
    rejected = []
    for row in rows:
        left = network.resolve(row["origin"])
        right = network.resolve(row["destination"])
        if left and right and left != right:
            pairs[left, right].append(row)
        else:
            rejected.append(row)
    return sources, pairs, rejected


def truck_path(locations):
    url = os.getenv("COVERAGE_VALHALLA_URL")
    if not url:
        return {"status": "router_unavailable"}
    if any(city.get("latitude") is None or city.get("longitude") is None for city in locations):
        return {"status": "missing_coordinates"}
    try:
        response = httpx.post(url.rstrip("/") + "/route", timeout=5, json={
            "locations": [{"lat": city["latitude"], "lon": city["longitude"], "radius": 2500} for city in locations],
            "costing": "truck", "costing_options": {"truck": {"use_ferry": 0}},
            "units": "kilometers",
        })
        payload = response.json()
        if payload.get("error_code") in {170, 171, 172, 173, 442}:
            return {"status": "path_not_found"}
        response.raise_for_status()
        trip = payload["trip"]
        if any(m.get("type") in {28, 29} for leg in trip["legs"] for m in leg.get("maneuvers", [])):
            return {"status": "ferry_required"}
        km, seconds = trip["summary"]["length"], trip["summary"]["time"]
        if not all(isinstance(x, (int, float)) and isfinite(x) and x > 0 for x in (km, seconds)):
            return {"status": "router_unavailable"}
        return {"status": "found", "distance_km": km, "navigation_hours": seconds / 3600,
                "graph_version": os.getenv("COVERAGE_VALHALLA_GRAPH_VERSION") or None}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"status": "router_unavailable"}


def price(rate, source, target_year, indices):
    """Return the published unit price and its projection on the same tax basis."""
    result = {"published_amount_rub": rate["amount_rub"], "forecast_amount_rub": None,
              "index_factors": [], "forecast_year": target_year}
    observed = date.fromisoformat(source["observed_on"])
    base_year = date.fromisoformat(rate["valid_from"]).year if rate.get("valid_from") else observed.year
    result["base_year"] = base_year
    if target_year is not None and (estimate := indices.estimate(rate["mode"], base_year, target_year)):
        result["forecast_amount_rub"] = float(money(Decimal(rate["amount_rub"]) * estimate.central))
        result["index_factors"] = [point.as_dict() for point in estimate.points]
    return result


def candidate(network, pair, rate, source, request, events, target_year, indices, graph, ordinal):
    left, right = pair
    mode = rate["mode"]
    pricing = price(rate, source, target_year, indices)
    published = rate | pricing | {"source_url": source["url"], "observed_on": source["observed_on"]}
    if source.get("separate_services"):
        published["separate_services"] = source["separate_services"]
    identifier = f"published:{ordinal}:{rate['source_id']}:{left}:{right}"
    flags = [{"code": "evidence_policy", "fields": [], "message":
              "Прайс не подтверждает полную цену, фактический срок и доступность оборудования для отправки."}]
    known = None
    on_date = all(event.astimezone(MOSCOW).date().isoformat() == source["observed_on"] for event in events)
    if target_year is None and not on_date:
        flags.append({"code": "tariff_date_unconfirmed", "fields": ["schedule"],
                      "message": "Опубликованная ставка показана справочно. На выбранную дату её действие требует подтверждения."})
    if rate["vat"] == "unknown":
        flags.append({"code": "unknown_vat", "fields": ["cost_basis"],
                      "message": "В этом прайсе НДС не указан. Ставка показана в исходном виде."})
    elif request.cost_basis == "excluding_vat" and rate.get("vat_percent") is None:
        flags.append({"code": "vat_rate_unconfirmed", "fields": ["cost_basis"],
                      "message": "В прайсе цена с НДС, но процент не указан. Для суммы без НДС нужна разбивка перевозчика."})
    amount = pricing["forecast_amount_rub"] if target_year else rate["amount_rub"] if on_date else None
    can_fit = rate["max_tonnes"] is not None and sum(c.weight_tonnes for c in request.cargoes) <= rate["max_tonnes"]
    if request.capacity_basis == "mass_and_volume":
        can_fit = can_fit and rate["max_m3"] is not None and all(c.volume_m3 is not None for c in request.cargoes) and (
            sum(c.volume_m3 for c in request.cargoes) <= rate["max_m3"])
    can_fit = can_fit and all(not c.temperature_c
                             and not (c.properties and (c.properties.hazardous or c.properties.temperature_control_required))
                             and c.cargo_type != "crude_oil" for c in request.cargoes)
    for cargo in request.cargoes:
        if cargo.max_piece_dimensions_m:
            limits, door = rate.get("internal_dimensions_m"), rate.get("door_dimensions_m")
            can_fit = can_fit and bool(limits and door and any(
                all(value <= limit for value, limit in zip(dimensions, limits))
                and all(value <= limit for value, limit in zip(dimensions[1:], door))
                for dimensions in permutations(cargo.max_piece_dimensions_m)))
    if not can_fit:
        flags.append({"code": "equipment_fit_unconfirmed", "fields": ["cargoes"],
                      "message": "Ставка указана за одну единицу транспорта. Вместимость или допуск груза требуют проверки."})
    elif rate["unit"] == "container":
        flags.append({"code": "packing_unconfirmed", "fields": ["cargoes"],
                      "message": "Цена за один контейнер в пределах опубликованных ограничений массы и объёма. Укладку подтвердит перевозчик."})
    if amount is not None and can_fit and rate["vat"] == "included":
        if request.cost_basis == "including_vat":
            known = float(money(Decimal(str(amount)) * len(events)))
        elif rate.get("vat_percent") is not None:
            known = float(money(Decimal(str(amount)) * len(events) / (1 + Decimal(rate["vat_percent"]) / 100)))
    if len(request.stops):
        known = None
        flags.append({"code": "unconfirmed_stop_chain", "fields": ["stops"],
                      "message": "Ставка относится к одному участку. Сквозная перевозка с остановками требует подтверждения."})
    return {
        "id": identifier, "service_ids": [identifier], "modes": [mode], "group_size": 1, "multimodal": False,
        "status": "insufficient_data", "complete": False, "total_cost_rub": None, "known_cost_subtotal_rub": known,
        "cost_rub_per_tonne": None, "max_delivery_hours": None, "issues": flags, "steps": [], "deliveries": [],
        "cost_items": [], "source_ids": [source["id"]], "evidence_levels": ["published_rate"],
        "route_hubs": [{"id": city, "settlement_id": city, "name": network.settlements[city]["name"],
                        "identity_status": "settlement"} for city in pair],
        "connections": [{"id": identifier, "mode": mode, "kind": "published_tariff",
                         "distance_km": graph.get("distance_km") if mode == "road" else None,
                         "operational_status": "unconfirmed",
                         "reference_metadata": {"tariff_source_id": source["id"], "scope": rate["scope"]}}],
        "stop_actions": [], "booking_confirmed": False, "published_rate": published, "calculation_mode": "published_data",
    }


def calculate_published(network, base, request, events, target_year=None, indices=None):
    if base["clarifications"]:
        return base
    ids = base["settlements"]
    locations = [network.settlements[city] for city in ids]
    road = truck_path(locations) if "road" in request.allowed_modes else {"status": "not_requested"}
    terminals = terminal_access(locations, request, road["status"])
    sources, rates, _ = network.published_tariffs
    indices = indices or load_indices()
    candidates, references = [], []
    for pair in zip(ids, ids[1:]):
        for rate in rates.get(pair, ()):
            source = sources[rate["source_id"]]
            if not rate["mode"]:
                references.append(rate | {"source_url": source["url"], "observed_on": source["observed_on"]})
                continue
            if rate["mode"] not in request.allowed_modes:
                continue
            if target_year and date.fromisoformat(source["observed_on"]).year > target_year:
                continue
            candidates.append(candidate(network, pair, rate, source, request, events, target_year, indices,
                                        road if len(ids) == 2 else {}, len(candidates)))
    candidates.sort(key=lambda row: (row["known_cost_subtotal_rub"] is None,
                                    row["published_rate"]["vat"] == "unknown", row["id"]))
    visible = candidates[:request.max_candidates]
    used_sources = {row["published_rate"]["source_id"] for row in visible} | {row["source_id"] for row in references}
    evidence = [sources[source_id] for source_id in sorted(used_sources)]
    if road["status"] == "found" or any(hub["path"]["status"] == "found" for hub in terminals):
        evidence.append(ROAD_GRAPH)
    if terminals:
        evidence.append(hub_catalog()["source"])
    for source_id in sorted({item["source_id"] for row in visible for item in row["published_rate"]["index_factors"]}):
        evidence.append(indices.sources[source_id])
    volume = volume_catalog()
    base.update(
        candidates=visible, sources=evidence + [volume["source"]],
        status="not_confirmed" if visible or road["status"] == "found" else "no_route_in_dataset",
        search={"expansions": 0, "truncated": len(visible) < len(candidates), "scope": "published_direct_segments"},
        warnings=["Указаны опубликованные ставки и известные компоненты. "
                  "Проверка наличия оборудования, грузовых операций и полная котировка остаются у перевозчика."],
        published_coverage={"truck_path": road, "other_tariffs": references, "terminals": terminals,
                            "volume": {"status": "no_directed_observations", "observations": volume["observations"]}},
    )
    return base
