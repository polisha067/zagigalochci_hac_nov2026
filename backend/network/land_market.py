"""Scenario prices by road distance and container corridor, with published rates first."""

from collections import defaultdict
from decimal import Decimal
from functools import cache
import hashlib
from heapq import heappop, heappush
from itertools import product
import json
from math import ceil
from statistics import median

from backend.network.cargo import named_temperature_cargo, normalized_name
from backend.network.coverage_catalog import ROOT
from backend.network.market_assumptions import market_snapshot
from backend.network.published_land import hub_catalog, truck_path
from backend.transport_calculator import money


BASE_YEAR = 2026
BANDS = {"road": (250, 500, 1000, 2000, 4000), "rail": (1500, 3000, 5000)}
RAIL_SIZES = {20: (18, 28), 40: (24, 58)}
VERSION = "land-market-2026-11-v1"
RAIL_TRANSFER_REFERENCES = {
    "Владивосток": "https://www.fesco.ru/ru/press-center/news/fesco-uskorila-na-36-obrabotku-konteynernykh-poezdov-na-universalnom-terminale-vmtp/",
    "Хабаровск": "https://www.fesco.ru/ru/clients/terminals/sot/",
}


def observations(network):
    return {(network.hubs[edge["from_hub"]]["settlement_id"],
             network.hubs[edge["to_hub"]]["settlement_id"], edge["mode"]): edge["distance_km"]
            for edge in network.edges.values()
            if edge["kind"] == "observation" and edge["mode"] in BANDS and edge.get("distance_km")}


def group(mode, km):
    return sum(km >= upper for upper in BANDS[mode])


@cache
def road_distance_references(network):
    rows = json.loads((ROOT / "road_distance_references.json").read_text(encoding="utf-8"))
    return {(network.resolve(row["origin"]), network.resolve(row["destination"])): row for row in rows}


@cache
def road_reference_graph(network):
    graph = defaultdict(dict)
    for (left, right, mode), km in observations(network).items():
        if mode == "road":
            graph[left][right] = km
    for (left, right), rows in network.published_tariffs[1].items():
        distances = [row["distance_km"] for row in rows if row["mode"] == "road"
                     and unit_class(row) == 20 and row.get("distance_km")]
        if distances and right not in graph[left]:
            graph[left][right] = median(distances)
    return graph


@cache
def road_reference_distance(network, origin, destination):
    graph = road_reference_graph(network)
    queue = [(0, 0, origin)]
    visited = set()
    while queue:
        km, hops, node = heappop(queue)
        if (node, hops) in visited:
            continue
        if node == destination:
            return km
        visited.add((node, hops))
        if hops < 3:
            for neighbor, distance in graph.get(node, {}).items():
                if km + distance <= 9500:
                    heappush(queue, (km + distance, hops + 1, neighbor))
    return None


def unit_class(rate):
    if rate["mode"] == "road" and rate["unit"] == "vehicle" and rate["max_tonnes"] == 20:
        return 20
    if rate["mode"] == "rail" and rate["unit"] == "container":
        return next((size for size in RAIL_SIZES if f" {size} " in rate["equipment"]), None)
    return None


def fit_snapshot(network):
    distances = observations(network)
    sources, pairs, _ = network.published_tariffs
    samples = defaultdict(dict)
    for (left, right), rates in sorted(pairs.items()):
        for row in rates:
            size = unit_class(row)
            source_id = row["source_id"]
            mode = row["mode"]
            if (size is None or sources[source_id]["observed_on"][:4] != str(BASE_YEAR)
                    or int((row.get("valid_from") or str(BASE_YEAR))[:4]) != BASE_YEAR):
                continue
            # Use one consistent service and tax basis per model. Rail distance is
            # a proxy from the same network for both fitting and inference.
            if not (mode == "road" and source_id.startswith("logistic-avto-regional-")
                    and row["vat"] == "unknown" or mode == "rail"
                    and source_id == "sibir-container-expanded" and row["vat"] == "included"):
                continue
            km = row.get("distance_km") if mode == "road" else distances.get((left, right, mode))
            if km and row["amount_rub"] > 0:
                samples[(mode, size)][(left, right)] = (km, row["amount_rub"], source_id)

    models = {}
    for (mode, size), rows in samples.items():
        training = defaultdict(list)
        held_out = []
        for pair, (km, amount, source_id) in rows.items():
            # Group both directions in the same split. This is a pair holdout,
            # not an independent carrier or a historical time split.
            key = "|".join(sorted(pair))
            held = int.from_bytes(hashlib.sha256(key.encode()).digest()[:2], "big") % 5 == 0
            if held:
                held_out.append((km, amount))
            else:
                training[group(mode, km)].append((km, amount, source_id))
        buckets = {}
        for band, records in training.items():
            if len(records) >= 5:
                buckets[str(band)] = {
                    "rub_per_km": round(median(amount / km for km, amount, _ in records), 4),
                    "km_min": min(km for km, _, _ in records), "km_max": max(km for km, _, _ in records),
                    "train_rows": len(records), "source_ids": sorted({source for _, _, source in records}),
                }
        errors = []
        for km, amount in held_out:
            bucket = buckets.get(str(group(mode, km)))
            if bucket and bucket["km_min"] <= km <= bucket["km_max"]:
                errors.append(abs(bucket["rub_per_km"] * km / amount - 1))
        models[f"{mode}:{size}"] = {
            "bands": buckets, "eligible_rows": len(rows), "holdout_rows": len(held_out),
            "holdout_priced": len(errors), "holdout_median_error_pct": round(100 * median(errors), 1) if errors else None,
        }
    return {"version": VERSION, "base_year": BASE_YEAR, "models": models}


@cache
def calibration():
    return json.loads((ROOT / "land_market_calibration.json").read_text(encoding="utf-8"))


@cache
def reefer_market():
    return json.loads((ROOT / "reefer_market.json").read_text(encoding="utf-8"))


@cache
def regional_reefer_rates(network):
    rows = json.loads((ROOT / "reefer_ekaterinburg.json").read_text(encoding="utf-8"))
    return {identifier: amounts for name, amounts in rows.items() if (identifier := network.resolve(name))}


def reefer_options(network, ids, request, indices):
    cargo = request.cargoes[0]
    props = cargo.properties
    known = next((row for key, row in network.cargo_types.items()
                  if key == cargo.cargo_type or cargo.name and normalized_name(cargo.name) in
                  {normalized_name(name) for name in (row["name"], *row["aliases"])}), {})
    bounds = [value for value in (cargo.temperature_c, props.required_temperature_c if props else None,
                                  known.get("required_temperature_c")) if value is not None]
    if not bounds or "road" not in request.allowed_modes or (props and (props.hazardous or props.phase == "gas")) \
            or known.get("hazardous") or not cargo.volume_m3 or cargo.max_piece_dimensions_m:
        return None
    data = reefer_market()
    low, high = data["temperature_c"]
    if max(low, *(value[0] for value in bounds)) > min(high, *(value[1] for value in bounds)):
        return None
    tonnes, cubic = data["capacity"]
    units = ceil(max(Decimal(str(cargo.weight_tonnes)) / tonnes, Decimal(str(cargo.volume_m3)) / cubic))
    factor = indices.estimate("road", data["base_year"], request.target_year)
    if factor is None or cargo.max_piece_tonnes and cargo.max_piece_tonnes > tonnes or units > 1 and not cargo.splittable:
        return None
    if len(ids) == 2 and ids[0] == network.resolve("Екатеринбург") and (
            amounts := regional_reefer_rates(network).get(ids[1])):
        # Use the lower published volume across the carrier's 20 t fleet.
        volume = 68
        count = ceil(max(Decimal(str(cargo.weight_tonnes)) / tonnes, Decimal(str(cargo.volume_m3)) / volume))
        if count == 1 or cargo.splittable:
            net = request.cost_basis == "excluding_vat"
            base = amounts[int(net)]
            source = {"name": "ИнтерТрансАвто · рефрижератор 20 т из Екатеринбурга",
                      "url": "https://www.intertransavto.ru/services/gruzoperevozki-refrizheratorom/"}
            part = {"origin": network.settlements[ids[0]]["name"], "destination": network.settlements[ids[1]]["name"],
                    "origin_id": ids[0], "destination_id": ids[1], "mode": "road", "equipment": "Рефрижератор 20 т",
                    "units": count, "unit_tonnes": tonnes, "unit_m3": volume, "base_year": data["base_year"],
                    "base_unit_rub": base, "forecast_rub": float(money(base * count * factor.central)),
                    "kind": "published_base", "vat": "excluded" if net else "included",
                    "distance_km": None, "distance_source": None, "confidence": None, "sources": [source]}
            return {"version": "reefer-market-2026-11", "target_year": request.target_year,
                    "routes": [{"legs": [part], "transfer_count": 0, "vat": part["vat"],
                                "movement_rub": part["forecast_rub"],
                                "scenario_note": "Прогноз по специализированному прайсу. Дата начала действия ставки не указана. "
                                                 "Доступность машины и заданный режим нужно подтвердить у перевозчика."}],
                    "legs": [[part]]}
    legs = []
    missing = []
    for left, right in zip(ids, ids[1:]):
        path = truck_path([network.settlements[left], network.settlements[right]])
        if path["status"] in {"path_not_found", "ferry_required"}:
            missing.append((left, right))
            continue
        km = path.get("distance_km")
        source = "truck_graph"
        if not km:
            pair = (left, right)
            km = road_reference_graph(network).get(left, {}).get(right)
            source = "market_direct_distance"
            if not km and (ref := road_distance_references(network).get(pair)):
                km, source = ref["distance_km"], "regional_carrier_reference"
            if not km:
                km, source = road_reference_distance(network, *pair), "market_distance_chain"
        if not km:
            missing.append((left, right))
            continue
        legs.append((left, right, Decimal(str(km)), source))
    if not legs:
        return None
    total_km = sum(row[2] for row in legs)
    # The same dedicated vehicles carry the shipment throughout the itinerary.
    # Allocate the minimum order price by distance so it is applied once.
    base = max(total_km * data["rub_per_km"], Decimal(data["minimum_rub"]))
    parts = []
    for left, right, km, source in legs:
        unit_base = base * km / total_km
        refs = [data]
        if source == "regional_carrier_reference":
            refs.append(road_distance_references(network)[left, right])
        parts.append({
            "origin": network.settlements[left]["name"], "destination": network.settlements[right]["name"],
            "origin_id": left, "destination_id": right, "mode": "road", "equipment": "Рефрижератор 20 т",
            "units": units, "unit_tonnes": tonnes, "unit_m3": cubic, "base_year": data["base_year"],
            "base_unit_rub": float(money(unit_base)), "forecast_rub": float(money(unit_base * units * factor.central)),
            "kind": "model", "vat": "unknown", "distance_km": float(km), "distance_source": source,
            "confidence": None, "sources": [{key: row[key] for key in ("name", "url")} for row in refs],
        })
    note = ("Ориентир по рефрижератору на весь маршрут по опубликованной ставке «от». "
            "Режим, фактический проезд и наличие машин подтверждает перевозчик.")
    if request.stops:
        note += " На остановках груз остаётся в том же кузове с работающей установкой. Плата за стоянку требует котировки."
    if missing:
        by_pair = {(row["origin_id"], row["destination_id"]): row for row in parts}
        return {"version": "reefer-market-2026-11", "target_year": request.target_year, "routes": [],
                "legs": [[part] if (part := by_pair.get(pair)) else [] for pair in zip(ids, ids[1:])]}
    return {"version": "reefer-market-2026-11", "target_year": request.target_year,
            "routes": [{"legs": parts, "transfer_count": 0, "vat": "unknown", "scenario_note": note,
                        "movement_rub": float(sum(Decimal(str(row["forecast_rub"])) for row in parts))}],
            "legs": [[part] for part in parts]}


@cache
def rail_corridors():
    return json.loads((ROOT / "land_market_corridors.json").read_text(encoding="utf-8"))


@cache
def terminal_rates():
    return json.loads((ROOT / "land_market_terminal_rates.json").read_text(encoding="utf-8"))


def rail_services(network):
    rail = rail_corridors()
    services = {(network.resolve(a), network.resolve(b)): {"days": days, "source": rail["source"]}
                for a, b, days in rail["routes"]}
    services.update({(network.resolve(a), network.resolve(b)): {"days": None, "source": {"name": name, "url": url}}
                     for a, b, name, url in rail.get("additional", ())})
    return {pair: service for pair, service in services.items() if all(pair)}


def terminal_reference(network, ids, legs, request, indices):
    data = terminal_rates()
    hub = network.resolve(data["hub"])
    source = data["source"]
    references = []
    for i in range(1, len(ids) - 1):
        if ids[i] != hub or not any(row["mode"] == "rail" for row in legs[i - 1]) or "road" not in request.allowed_modes:
            continue
        row = next((row for row in data["outbound_container_truck"] if network.resolve(row[0]) == ids[i+1]), None)
        if row is None:
            continue
        for size, road_base in zip(RAIL_SIZES, row[1:]):
            road = indices.estimate("road", source["base_year"], request.target_year)
            handling = indices.estimate("handling", source["base_year"], request.target_year)
            if road and handling:
                references.append({
                    "station": data["station"], "destination": row[0], "size_ft": size, "source": source,
                    "receive_rub": float(money(data["arrival_platform"][str(size)] * handling.central)),
                    "outbound_rub": float(money(road_base * road.central)), "vat": data["vat"],
                })
    return references


def dry_cargo(network, request, *, allow_piece_dimensions=False):
    if len(request.cargoes) != 1:
        return None
    cargo = request.cargoes[0]
    known = next((row for key, row in network.cargo_types.items() if
                  (not cargo.name and key == cargo.cargo_type) or
                  (cargo.name and normalized_name(cargo.name) in
                   {normalized_name(name) for name in (row["name"], *row["aliases"])})), None)
    props = cargo.properties
    profiles = {"dry_food", "dry_general"}
    if (cargo.packaging not in ("bags", "boxes") or not cargo.volume_m3 or cargo.temperature_c
            or cargo.max_piece_dimensions_m and not allow_piece_dimensions
            or (not known and not props and named_temperature_cargo(cargo.name))
            or (known and (known["handling_profile"] not in profiles or cargo.packaging not in known["packagings"]))
            or (props and (props.phase != "solid" or props.hazardous or props.temperature_control_required
                           or props.handling_profile not in profiles
                           or known and props.handling_profile != known["handling_profile"]))):
        return None
    return cargo


def cargo_units(network, request, capacity):
    cargo = dry_cargo(network, request)
    if cargo is None:
        return None
    tonnes, volume = capacity
    if cargo.max_piece_tonnes and cargo.max_piece_tonnes > tonnes:
        return None
    units = ceil(max(Decimal(str(cargo.weight_tonnes)) / Decimal(str(tonnes)),
                     Decimal(str(cargo.volume_m3)) / Decimal(str(volume))))
    return units if units == 1 or cargo.splittable is True else None


def price_leg(network, pair, mode, size, request, indices, snapshot, km, distance_source, service,
              linehaul_only=False):
    sources, pairs, _ = network.published_tariffs
    rates = [row for row in pairs.get(pair, ()) if row["mode"] == mode and unit_class(row) == size
             and int(sources[row["source_id"]]["observed_on"][:4]) <= request.target_year
             and (not linehaul_only or row["scope"] in {"station_to_station", "container_linehaul"})]
    truck = market_snapshot()["truck"]
    estimates = []
    for row in rates or [None]:
        source = sources[row["source_id"]] if row else None
        if row:
            base = row["amount_rub"]
            base_year = int((row.get("valid_from") or source["observed_on"])[:4])
            vat, vat_percent = row["vat"], row.get("vat_percent")
            refs = [source]
            if mode == "road" and row.get("distance_km"):
                km, distance_source = row["distance_km"], "carrier_reference"
        else:
            if not km or mode == "rail" and service is None:
                continue
            model = snapshot["models"].get(f"{mode}:{size}", {})
            band = model.get("bands", {}).get(str(group(mode, km)))
            if not band or not band["km_min"] <= km <= band["km_max"]:
                continue
            base = Decimal(str(band["rub_per_km"])) * Decimal(str(km))
            base_year, vat, vat_percent = snapshot["base_year"], ("included" if mode == "rail" else "unknown"), None
            refs = [sources[key] for key in band["source_ids"] if key in sources]
            if distance_source == "regional_carrier_reference":
                refs.append(road_distance_references(network)[pair])
        if mode == "road":
            capacity = (truck["max_tonnes"], truck["assumed_m3"])
        else:
            capacity = RAIL_SIZES[size]
        if row and row.get("max_tonnes") and row.get("max_m3"):
            capacity = (row["max_tonnes"], row["max_m3"])
        elif row and mode == "rail" and row.get("max_tonnes"):
            capacity = (row["max_tonnes"], capacity[1])
        elif mode == "road":
            refs.append(truck)
        units = cargo_units(network, request, capacity)
        factor = indices.estimate(mode, base_year, request.target_year)
        if units is None or factor is None:
            continue
        if request.cost_basis == "excluding_vat" and vat == "included":
            if vat_percent is None:
                continue
            base = Decimal(str(base)) / (1 + Decimal(str(vat_percent)) / 100)
            vat = "excluded"
        forecast = money(Decimal(str(base)) * factor.central)
        estimates.append({
            "mode": mode, "equipment": "Фура 20 т" if mode == "road" else f"Контейнер {size} футов",
            "origin": network.settlements[pair[0]]["name"], "destination": network.settlements[pair[1]]["name"],
            "origin_id": pair[0], "destination_id": pair[1],
            "kind": "published_base" if row else "model", "distance_km": km, "distance_source": distance_source,
            "units": units, "unit_tonnes": capacity[0], "unit_m3": capacity[1],
            "forecast_rub": float(money(forecast * units)), "base_year": base_year,
            "base_unit_rub": float(money(base)), "vat": vat,
            "scope": row["scope"] if row else None,
            "origin_station": row.get("origin_station") if row else None,
            "destination_station": row.get("destination_station") if row else None,
            "sources": [{"name": item["name"], "url": item["url"]} for item in refs],
            "service": service if not row else None,
            "confidence": "exploratory" if not row and (mode == "rail" or distance_source in
                           {"market_distance_chain", "regional_carrier_reference"}) else None,
        })
    return sorted(estimates, key=lambda row: row["forecast_rub"])


def rail_connections(network, ids, request, indices, snapshot, services, covered):
    if len(ids) != 2 or request.max_legs < 2:
        return []
    left, right = ids
    pairs = set(services)
    pairs.update(pair for pair, rates in covered.items()
                 if any(rate["mode"] == "rail" and rate["scope"] == "station_to_station" for rate in rates))
    hubs = {network.resolve(row["settlement"]): row.get("source_url", hub_catalog()["source"]["url"])
            for row in hub_catalog()["hubs"] if "container_transfer" in row["operations"]}
    hubs.update({network.resolve(city): url for city, url in RAIL_TRANSFER_REFERENCES.items()})
    routes = []
    for hub in sorted(node for node in hubs if node and node not in ids
                      and (left, node) in pairs and (node, right) in pairs):
        for size in RAIL_SIZES:
            parts = []
            for a, b in ((left, hub), (hub, right)):
                km = network.edges.get(f"observation:rail:{a}:{b}", {}).get("distance_km")
                prices = price_leg(network, (a, b), "rail", size, request, indices, snapshot, km,
                                   "observation" if km else None, services.get((a, b)), linehaul_only=True)
                if prices:
                    parts.append(prices[0])
            if len(parts) != 2:
                continue
            vat = parts[0]["vat"] if parts[0]["vat"] == parts[1]["vat"] != "unknown" else None
            routes.append({"legs": parts, "transfer_count": 1,
                           "rail_transfer": network.settlements[hub]["name"], "rail_transfer_url": hubs[hub], "vat": vat,
                           "movement_rub": float(sum(Decimal(str(part["forecast_rub"])) for part in parts))
                           if vat else None})
    return sorted(routes, key=lambda row: (row["movement_rub"] is None, row["movement_rub"] or 0))


def land_options(network, result, request, indices):
    if (request.data_policy != "allow_assumptions" or not network.registry or result["clarifications"]
            or len(request.stops) + 1 > request.max_legs or result["shipments"] != 1
            or not all(result["settlements"]) or any(stop.action not in (None, "visit") for stop in request.stops)):
        return None
    if len(request.cargoes) == 1:
        cargo = request.cargoes[0]
        if cargo.temperature_c or cargo.properties and cargo.properties.temperature_control_required:
            return reefer_options(network, result["settlements"], request, indices)
    if any(stop.action != "visit" or stop.no_reload for stop in request.stops):
        return None
    snapshot = calibration()
    modes = [mode for mode in BANDS if mode in request.allowed_modes]
    if not modes or cargo_units(network, request, (28.2, 86)) is None:
        return None
    ids = result["settlements"]
    legs = []
    unpriced_rail = []
    covered = network.published_tariffs[1]
    corridors = rail_services(network) if "rail" in modes else {}
    for left, right in zip(ids, ids[1:]):
        pair = (left, right)
        options = []
        for mode in modes:
            edge = network.edges.get(f"observation:{mode}:{left}:{right}", {})
            km = edge.get("distance_km")
            distance_source = "observation" if km else None
            service = corridors.get(pair) if mode == "rail" else None
            if mode == "road" and not any(row["mode"] == "road" and unit_class(row) for row in covered.get(pair, ())):
                path = truck_path([network.settlements[key] for key in pair])
                if path["status"] == "found":
                    km, distance_source = path["distance_km"], "truck_graph"
                elif path["status"] in {"ferry_required", "path_not_found"}:
                    continue
                elif km is None:
                    if reference := road_distance_references(network).get(pair):
                        km, distance_source = reference["distance_km"], "regional_carrier_reference"
                    elif km := road_reference_distance(network, left, right):
                        distance_source = "market_distance_chain"
            for size in (20,) if mode == "road" else RAIL_SIZES:
                prices = price_leg(network, pair, mode, size, request, indices, snapshot, km, distance_source, service,
                                   linehaul_only=mode == "rail" and len(ids) > 2)
                if prices:
                    options.append(prices[0])
                    if mode == "rail" and len(ids) == 2:
                        for scope in ("door_to_door", "warehouse_to_station", "warehouse_to_warehouse"):
                            package = next((row for row in prices if row["scope"] == scope), None)
                            if package and package != prices[0]:
                                options.append(package)
            if service and not any(option["mode"] == "rail" for option in options):
                unpriced_rail.append({"origin": network.settlements[left]["name"],
                                      "destination": network.settlements[right]["name"], **service})
        legs.append(options)

    routes = []
    if all(legs):
        hubs = {network.resolve(row["settlement"]) for row in hub_catalog()["hubs"]
                if "container_transfer" in row["operations"]}
        for parts in product(*legs):
            transfers = [ids[i] for i in range(1, len(parts)) if parts[i-1]["mode"] != parts[i]["mode"]]
            if any(node not in hubs for node in transfers):
                continue
            # Show the movement subtotal. The tariff scopes, dwell costs and
            # container handoff terms need separate confirmation.
            taxes = {row["vat"] for row in parts}
            comparable = len(taxes) == 1 and (taxes != {"unknown"} or all(leg["mode"] == "road" for leg in parts))
            routes.append({
                "legs": list(parts), "transfer_count": len(transfers),
                "movement_rub": float(sum(Decimal(str(row["forecast_rub"])) for row in parts))
                if comparable else None,
                "vat": next(iter(taxes)) if len(taxes) == 1 else None,
            })
    if corridors:
        routes.extend(rail_connections(network, ids, request, indices, snapshot, corridors, covered))
    # Reserve room for distinct mode sequences before cheaper equipment variants.
    routes.sort(key=lambda row: (row["movement_rub"] is None, row["movement_rub"] or 0))
    diverse = {}
    for row in routes:
        diverse.setdefault((tuple(leg["mode"] for leg in row["legs"]), row.get("rail_transfer"),
                            row["legs"][0]["scope"] if len(row["legs"]) == 1 else None), row)
    visible = list(diverse.values())[:request.max_candidates]
    visible.extend([row for row in routes if row not in visible][:request.max_candidates - len(visible)])
    return {"version": snapshot["version"], "target_year": request.target_year, "routes": visible, "legs": legs,
            "unpriced_rail": unpriced_rail,
            "terminal_references": terminal_reference(network, ids, legs, request, indices)
            } if visible or any(legs) or unpriced_rail else None
