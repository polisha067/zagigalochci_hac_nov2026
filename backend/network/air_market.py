"""Airport-to-airport freight budgets from directional carrier price lists."""

from collections import defaultdict
from decimal import Decimal, ROUND_CEILING
from functools import cache
from itertools import product
import json

from backend.network.coverage_catalog import ROOT
from backend.network.land_market import dry_cargo
from backend.transport_calculator import money


CONSOLIDATED_WEIGHT_LIMIT_KG = Decimal("500")
# The general-cargo rates are used below 500 kg. Round up to half a kg
# without crossing into the special rate at 500 kg.
SPLIT_WEIGHT_LIMIT_KG = Decimal("499.5")


@cache
def air_catalog():
    return json.loads((ROOT / "air_market_rates.json").read_text(encoding="utf-8"))


@cache
def aeroflot_catalog():
    return json.loads((ROOT / "aeroflot_winter_2026.json").read_text(encoding="utf-8"))


def air_tariff_rows(network):
    catalog = air_catalog()
    sources = catalog["sources"]
    for key, airport in (("cargobooking", "SVO"), ("utair", "VKO")):
        rows = catalog[f"moscow_{airport.lower()}"]
        for destination, code, amount, *via in rows:
            yield "Москва", airport, destination, code, [amount], [0], via, sources[key]
    for destination, code, amount in catalog["petersburg"]:
        yield "Санкт-Петербург", "LED", destination, code, [amount], [0], [], sources["mts"]
    for origin, departure, destination, arrival, amount, date, slug in catalog["regional"]:
        source = sources["stk"] | {"base_date": date, "url": f"https://stcargo.ru/aviaperevozki-{slug}"}
        yield origin, departure, destination, arrival, [amount], [0], [], source
    winter = aeroflot_catalog()
    airports = winter["airports"]
    for document, rows in winter["rates"].items():
        source = {
            "name": "Аэрофлот · грузовое тарифное письмо, зима 2026–2027", "url": winter["sources"][document],
            "base_date": winter["base_date"], "valid_until": winter["valid_until"], "rules_url": winter["rules_url"],
            "fuel_url": winter["fuel_url"], "vat_percent": None, "minimum_kg": 25,
            "kg_per_m3": 1_000_000 / 6000, "step_kg": 0.5, "round_paid_weight": True,
            "fixed_rub": 370, "fuel_rub_per_kg": winter["fuel_rub_per_kg"],
        }
        for departure, arrival, via, amount, _ in rows:
            yield airports[departure], departure, airports[arrival], arrival, [amount], [0], [via] if via else [], source
    # Moscow in this release is a city code. Keep it for a single tariff
    # and disallow connections with an unidentified airport.
    release = network.air_tariff
    if release:
        source = {
            "name": "Якутия · федеральные грузовые тарифы", "url": release["source_url"],
            "base_date": release["valid_from"], "valid_until": release["valid_until"], "vat_percent": None,
            "minimum_kg": release["minimum_weight_kg"], "step_kg": release["actual_weight_step_kg"],
            "fixed_rub": release["awb_rub"], "kg_per_m3": 1_000_000 / release["cubic_cm_per_kg"],
        }
        names = {"С.-Петербург": "Санкт-Петербург", "Мин. Воды": "Минеральные Воды", "Улан–Удэ": "Улан-Удэ"}
        codes = {"МОВ": "MOW", "ОВБ": "OVB", "СПТ": "LED", "ЯКТ": "YKS", "ВВО": "VVO",
                 "ЕКБ": "SVX", "ИКТ": "IKT", "МРВ": "MRV", "ХБР": "KHV", "УЛН": "UUD",
                 "ЧТА": "HTA", "УУС": "UUS", "КЯА": "KJA", "ПТК": "PKC", "НРГ": "NER",
                 "НЧК": "NAL", "СОЧ": "AER", "ОМС": "OMS", "УЛЭ": "UUD"}
        for origin, destination, amounts in release["federal_rows"]:
            left, departure = origin.rstrip(")").rsplit(" (", 1)
            right, arrival = destination.rstrip(")").rsplit(" (", 1)
            yield (names.get(left, left), codes.get(departure, departure),
                   names.get(right, right), codes.get(arrival, arrival),
                   amounts, release["breakpoints_kg"], [], source)


def compatible_airports(parts):
    if any(a["arrival"] != b["departure"] or a["arrival"] in (None, "MOW")
           for a, b in zip(parts, parts[1:])):
        return False
    airports = [parts[0]["departure"], *(code for leg in parts for code in (*leg["via"], leg["arrival"]))]
    known = [code for code in airports if code and code != "MOW"]
    return len(set(known)) == len(known)


def paid_weight(mass, volume_kg, minimum, step, round_paid):
    if step and not round_paid:
        mass = (mass / step).to_integral_value(rounding=ROUND_CEILING) * step
    paid = max(mass, volume_kg, Decimal(minimum))
    return (paid / step).to_integral_value(rounding=ROUND_CEILING) * step if round_paid else paid


def air_rates(network, request, indices, *, limits=None):
    cargo = request.cargoes[0]
    mass = Decimal(str(cargo.weight_tonnes)) * 1000
    volume = Decimal(str(cargo.volume_m3))
    estimates = defaultdict(list)
    @cache
    def resolve(name):
        return network.resolve(name)

    @cache
    def factor(base_year):
        series = indices.series.get("air")
        historical_end = max(base_year, series.last_actual) if series else base_year
        historical = indices.estimate("air", base_year, historical_end)
        growth = indices.air_scenarios(historical_end, Decimal(request.target_year - historical_end))
        if growth and historical:
            return historical.central * growth[0], [point.as_dict() for point in (*historical.points, *growth[2])]
        return None

    for origin, departure, destination, arrival, amounts, breaks, via, source in air_tariff_rows(network):
        left, right = resolve(origin), resolve(destination)
        if not left or not right or left == right:
            continue
        if request.cost_basis == "excluding_vat" and not source["vat_percent"]:
            continue
        base_year = int(source["base_date"][:4])
        if request.target_year < base_year:
            continue
        forecast = factor(base_year)
        if forecast is None:
            continue
        step = Decimal(str(source["step_kg"]))
        round_paid = source.get("round_paid_weight")
        volume_kg = volume / Decimal("0.006") if round_paid else volume * Decimal(str(source["kg_per_m3"]))
        paid = paid_weight(mass, volume_kg, source["minimum_kg"], step, round_paid)
        units = 1
        if paid >= CONSOLIDATED_WEIGHT_LIMIT_KG:
            if limits is not None:
                limit = {"paid_kg": float(paid), "threshold_kg": float(CONSOLIDATED_WEIGHT_LIMIT_KG),
                         "source": {"name": source["name"], "url": source["url"]}}
                if (left, right) not in limits or limit["paid_kg"] < limits[left, right]["paid_kg"]:
                    limits[left, right] = limit
            if request.data_policy != "allow_assumptions" or cargo.splittable is not True:
                continue
            units = int((max(mass, volume_kg) / SPLIT_WEIGHT_LIMIT_KG).to_integral_value(rounding=ROUND_CEILING))
            units = max(2, units)
            paid = paid_weight(mass / units, volume_kg / units, source["minimum_kg"], step, round_paid)
            if cargo.max_piece_dimensions_m:
                piece_volume = Decimal(1)
                for dimension in cargo.max_piece_dimensions_m:
                    piece_volume *= Decimal(str(dimension))
                if piece_volume > volume / units:
                    continue
            if cargo.max_piece_tonnes and Decimal(str(cargo.max_piece_tonnes)) * 1000 > mass / units:
                continue
        band = max(i for i, limit in enumerate(breaks) if paid >= limit)
        rate = Decimal(str(amounts[band]))
        fixed = Decimal(source["fixed_rub"])
        tax = "included"
        if request.cost_basis == "excluding_vat":
            tax = "excluded"
            divisor = 1 + Decimal(source["vat_percent"]) / 100
            rate, fixed = rate / divisor, fixed / divisor
        variable_fees = Decimal(source.get("terminal_rub_per_kg", 0) + source.get("fuel_rub_per_kg", 0))
        base = (paid * (rate + variable_fees) + fixed) * units
        estimates[left, right].append({
            "origin": network.settlements[left]["name"], "destination": network.settlements[right]["name"],
            "origin_id": left, "destination_id": right,
            "via_points": [{"code": code, "name": name, "id": resolve(name)}
                           for code in via if (name := aeroflot_catalog()["airports"].get(code))],
            "departure": departure, "arrival": arrival, "via": via, "vat": tax, "base_year": base_year,
            "paid_kg": float(paid * units), "paid_kg_per_shipment": float(paid),
            "shipment_count": units, "volume_m3_per_shipment": float(volume / units),
            "published_rub_per_kg": amounts[band], "base_rub": float(money(base)),
            "base_rate_rub_per_kg": float(rate), "fixed_rub": float(fixed),
            "variable_fees_rub_per_kg": float(variable_fees),
            "forecast_rub": float(money(base * forecast[0])), "index_factors": forecast[1],
            "source": {key: source[key] for key in ("name", "url", "base_date", "valid_until", "rules_url", "fuel_url")
                       if key in source},
        })
    return estimates


def air_options(network, result, request, indices):
    if ("air" not in request.allowed_modes or request.target_year > indices.max_year
            or result["clarifications"] or result["shipments"] != 1
            or len(request.stops) + 1 > request.max_legs or not all(result["settlements"])
            or any(stop.action != "visit" or stop.no_reload for stop in request.stops)
            or dry_cargo(network, request, allow_piece_dimensions=True) is None):
        return None
    ids = result["settlements"]
    limits = {}
    legs = air_rates(network, request, indices, limits=limits)
    requested_pairs = list(zip(ids, ids[1:]))
    segments = [min(rows, key=lambda row: row["forecast_rub"]) if rows else None
                for rows in (legs.get(pair) for pair in requested_pairs)]
    paths = [tuple(ids)] if len(ids) == 2 or request.data_policy == "allow_assumptions" else []
    if len(ids) == 2 and request.max_legs >= 2 and request.data_policy == "allow_assumptions":
        paths.extend((ids[0], middle, ids[-1]) for left, middle in legs
                     if left == ids[0] and middle not in ids and (middle, ids[-1]) in legs)
    routes = []
    for path in paths:
        for parts in product(*(legs.get(pair, ()) for pair in zip(path, path[1:]))):
            if not compatible_airports(parts):
                continue
            routes.append({
                "legs": list(parts), "transfer_count": len(parts) - 1,
                "forecast_rub": float(sum(Decimal(str(leg["forecast_rub"])) for leg in parts)),
                "vat": parts[0]["vat"],
            })
    routes.sort(key=lambda row: (row["transfer_count"], row["forecast_rub"]))
    # Keep distinct airports and carrier products ahead of different quotes
    # for the same itinerary and source.
    options, itineraries = {}, {}
    for route in routes:
        itinerary = tuple((leg["departure"], tuple(leg["via"]), leg["arrival"]) for leg in route["legs"])
        providers = tuple(leg["source"]["name"] for leg in route["legs"])
        options.setdefault((itinerary, providers), route)
        itineraries.setdefault(itinerary, route)
    visible = list(itineraries.values())[:request.max_candidates]
    visible.extend([route for route in options.values() if route not in visible][:request.max_candidates - len(visible)])
    return {"version": air_catalog()["version"], "target_year": request.target_year,
            "routes": visible, "segments": segments,
            "weight_limits": [limits.get(pair) if not segment else None for pair, segment in zip(requested_pairs, segments)]}
