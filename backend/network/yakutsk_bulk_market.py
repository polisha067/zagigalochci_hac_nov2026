"""Budget scenarios via Moscow using published tariffs for each service."""

import json
from decimal import Decimal
from functools import cache
from math import ceil
from statistics import median

from backend.network.coverage_catalog import ROOT
from backend.network.land_market import RAIL_SIZES, dry_cargo, price_leg, calibration
from backend.transport_calculator import money


@cache
def snapshot():
    return json.loads((ROOT / "yakutsk_bulk_market.json").read_text(encoding="utf-8"))


@cache
def regional_snapshot():
    return json.loads((ROOT / "regional_bulk_market.json").read_text(encoding="utf-8"))


def bulk_market_routes(network, result, request, indices):
    if (request.data_policy != "allow_assumptions" or result["clarifications"]
            or result["shipments"] != 1 or request.stops or request.max_delivery_hours is not None):
        return []
    cargo = dry_cargo(network, request, allow_piece_dimensions=True)
    if cargo is None:
        return []
    origin, destination = result["settlements"]
    if not origin or not destination:
        return []
    yakutsk = destination == network.resolve("Якутск")
    regional = regional_snapshot()
    city = network.settlements[destination]["name"]
    road_rate = regional["road"]["destinations"].get(city)
    air_rate = regional["air"].get(city)
    if not yakutsk and (not (road_rate or air_rate) or network.resolve(city) != destination
                        or request.target_year < 2026 or origin == destination
                        or cargo.splittable is not True
                        or cargo.max_piece_tonnes is not None or cargo.max_piece_dimensions_m is not None):
        return []
    hub = network.resolve("Москва")
    terminal = network.resolve("Нижний Бестях, Республика Саха (Якутия)")
    modes = set(request.allowed_modes)
    data = snapshot()
    mass = Decimal(str(cargo.weight_tonnes))
    volume = Decimal(str(cargo.volume_m3))

    def units(capacity):
        tonnes, cubic = map(Decimal, capacity)
        count = ceil(max(mass / tonnes, volume / cubic))
        if cargo.max_piece_tonnes and cargo.max_piece_tonnes > tonnes:
            return None
        return count if count == 1 or cargo.splittable is True else None

    def leg(start, end, mode, label, rate, count, capacity, year, sources, *, vat="unknown", model=False, index=None):
        factor = indices.estimate(index or mode, year, request.target_year)
        return {
            "origin": network.settlements[start]["name"], "destination": network.settlements[end]["name"],
            "origin_id": start, "destination_id": end, "mode": mode, "equipment": label,
            "units": count, "unit_tonnes": capacity[0], "unit_m3": capacity[1], "base_year": year,
            "base_unit_rub": float(money(rate)), "forecast_rub": float(money(rate * count * factor.central)),
            "vat": vat, "kind": "model" if model else "published_base",
            "sources": [{"name": source["name"], "url": source["url"]} for source in sources],
            "distance_km": None, "distance_source": None, "confidence": None,
        }

    feeder = []
    if origin != hub:
        if "road" not in modes or request.max_legs < 2:
            return []
        if source := data["feeders"].get(network.settlements[origin]["name"]):
            if not (count := units(source["capacity"])):
                return []
            base = Decimal(str(median(row["rub"] for row in source["benchmarks"])))
            feeder.append(leg(origin, hub, "road", "Фура 20 т, медиана прайсов", base, count,
                              source["capacity"], source["year"], source["benchmarks"], model=True))
        elif cargo.max_piece_dimensions_m is None:
            pair = (origin, hub)
            edge = network.edges.get(f"observation:road:{origin}:{hub}", {})
            estimates = price_leg(network, pair, "road", 20, request, indices, calibration(),
                                  edge.get("distance_km"), "observation", None)
            feeder = estimates[:1]
        if not feeder:
            return []

    routes = []

    def add(parts, note):
        taxes = {part["vat"] for part in parts}
        routes.append({"legs": parts, "transfer_count": len(parts) - 1,
                       "movement_rub": float(sum(Decimal(str(part["forecast_rub"])) for part in parts)),
                       "vat": taxes.pop() if len(taxes) == 1 else "mixed",
                       "scenario_note": note})

    transfer = data["moscow_transfer"]
    transfer_units = units(transfer["capacity"])
    # The public Moscow tariff covers collection inside MKAD. Use it as a
    # budget allowance for transfer between the carriers' receiving points.
    transfer_leg = []
    if feeder and transfer_units:
        transfer_leg.append(leg(hub, hub, "road", "Передача между перевозчиками в Москве",
                                Decimal(transfer["rub"]), transfer_units, transfer["capacity"],
                                transfer["year"], [transfer], model=True))
    if feeder and (not transfer_leg or request.max_legs < 3):
        return []
    if feeder and request.cost_basis == "excluding_vat":
        # The Moscow transfer price has no published tax breakdown.
        return []
    if not yakutsk:
        included = request.cost_basis == "including_vat"
        tax = "included" if included else "excluded"
        capacity = (cargo.weight_tonnes, cargo.volume_m3)
        if road_rate and "road" in modes:
            density, rub = road_rate
            paid = max(mass * 1000, volume * Decimal("1.1") * density)
            if paid > 2000:
                # One warehouse entry per 20-tonne truck in the budget scenario.
                entries = units((20, 90))
                base = paid * Decimal(str(rub)) + 250 * entries
                add(feeder + transfer_leg + [
                    leg(hub, destination, "road", "Сборный груз · тариф свыше 2 т", base /
                        (1 if included else Decimal("1.22")), 1, capacity, 2026,
                        [regional["road"]], vat=tax, model=True)
                ], "Склад — склад. Оценка для делимой партии обычных мест до 3 м по сумме сторон "
                   "и менее 500 кг каждое. Учтён минимальный въезд на склад на машину. "
                   "Довоз, особые сборы и график подтверждает перевозчик.")
        if air_rate and "air" in modes:
            rub, published, slug = air_rate
            paid = max(mass * 1000, volume * 167)
            if paid > 1000:
                source = {"name": f"СТК · генеральный груз, тариф от {published}",
                          "url": f"https://stcargo.ru/aviaperevozki-moskva-{slug}"}
                add(feeder + transfer_leg + [
                    leg(hub, destination, "air", "Генеральный груз · тариф свыше 1 т",
                        paid * rub / (1 if included else Decimal("1.20")), 1, capacity, 2026,
                        [source], vat=tax, model=True)
                ], "Бюджет аэропорт — аэропорт по опубликованной сетке. "
                   "Число отправок, доплаты по отдельным накладным, получение и вместимость уточняет перевозчик.")
        return sorted(routes, key=lambda route: route["movement_rub"])

    if request.cost_basis == "including_vat":
        for mode, rate in data["bulk"].items():
            if mode not in modes:
                continue
            paid = max(mass * 1000, volume * Decimal("1.1") * rate["density"])
            applicable = [rub for minimum, rub in rate["bands"] if paid >= Decimal(str(minimum))]
            if not applicable:
                continue
            # The upper tariff tier specifies price, not guaranteed capacity.
            if paid >= 5001 and cargo.splittable is not True:
                continue
            parts = feeder + transfer_leg + [
                leg(hub, destination, mode, f"Расчёт на весь объём · {applicable[-1]} ₽/кг базы", paid * applicable[-1], 1,
                    (cargo.weight_tonnes, cargo.volume_m3), 2026, [rate | {"url": data["bulk_source"]}], model=True)
            ]
            add(parts, "Бюджет на всю партию по весовой сетке перевозчика. Сборы за особые места, "
                       "укладка, график и вместимость нескольких отправок требуют согласования."
                       + (" Проезд через Лену зависит от переправы на дату отправки." if mode == "road" else ""))

    rail = data["rail"]
    delivery = data["delivery"]
    if terminal and {"rail", "road"}.issubset(modes) and request.max_legs >= (4 if feeder else 2):
        for size, (_, cubic) in RAIL_SIZES.items():
            key = str(size)
            capacity = (rail["limits_tonnes"][key], cubic)
            if not (count := units(capacity)):
                continue
            included = request.cost_basis == "including_vat"
            vat_factor = 1 + Decimal(delivery["scenario_vat_percent"]) / 100
            tax = "included" if included else "excluded"
            parts = feeder + transfer_leg + [
                leg(hub, terminal, "rail", f"Контейнер {size} футов · ставка от", Decimal(rail["rates"][key]) /
                    (1 if included else Decimal("1.20")), count, capacity, rail["year"],
                    [rail, rail["capacity_source"]], vat=tax, model=True),
                leg(terminal, destination, "road", f"Терминал и довоз · контейнер {size} футов",
                    Decimal(delivery["rates"][key]) * (vat_factor if included else 1), count, capacity,
                    delivery["year"], [delivery], vat=tax, model=True, index="handling"),
            ]
            add(parts, "Нижняя рыночная оценка по контейнерной ставке «от» и комплексному тарифу обработки и довоза. "
                       "Тарифный состав в Москве и сезонную переправу через Лену подтверждает перевозчик.")
    return sorted(routes, key=lambda route: route["movement_rub"])
