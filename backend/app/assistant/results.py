"""Render engine results without asking the language model to restate numbers"""

from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from backend.network.chukotavia import ChukotaviaResponse
from backend.network.delivery_forecast import ForecastResponse
from backend.network.models import NetworkResponse
from backend.network.pipeline import PipelineResponse
from backend.network.russia import RussiaResponse
from backend.network.river_tariffs import river_catalog

from .references import AIR_PATH, MODE_LABELS, PIPELINE_PATH, RIVER_PATH, number, pipeline_points, snapshot, source_ref


VAT = {"including_vat": "с НДС", "excluding_vat": "без НДС"}


def _tariff_scope(row):
    for connection in row.get("connections", ()):
        metadata = connection["reference_metadata"]
        if metadata.get("scope") == "station_to_station":
            return (f"Для ж/д использован тариф ТЛМ на контейнер {metadata['container_size_ft']} футов "
                    "от станции до станции. Подвоз, доставка от станции и охрана оплачиваются отдельно, если требуются.")
    return None


def _expired_river_summary(result):
    if not isinstance(result, NetworkResponse):
        return None
    if not result.candidates or not all(
        any(issue["code"] == "expired_evidence" for issue in row["issues"])
        for row in result.candidates
    ):
        return None

    data = river_catalog()
    if result.network_id != data["id"]:
        return None
    hubs = {hub["id"]: hub["city"] for hub in data["hubs"]}
    origin = hubs.get(result.request.get("origin_hub_id"))
    destination = hubs.get(result.request.get("destination_hub_id"))
    direction = f" для маршрута {origin} → {destination}" if origin and destination else ""
    services = {identifier for row in result.candidates for identifier in row["service_ids"]}
    evidence = [service["evidence"] for service in data["services"] if service["id"] in services]
    if not evidence:
        return None
    start = min(datetime.fromisoformat(item["valid_from"]) for item in evidence)
    end = max(datetime.fromisoformat(item["valid_until"]) for item in evidence) - timedelta(microseconds=1)
    if start.year == end.year:
        period = f"только навигацию {start.year} года"
    else:
        period = f"период с {start:%d.%m.%Y} по {end:%d.%m.%Y}"
    return (f"Речной снимок{direction} покрывает {period}. "
            "Для выбранных дат в снимке нет действующих условий перевозки, поэтому цену и срок рассчитать нельзя. "
            "Для сравнения контейнеров по этому снимку укажите дату в пределах этой навигации.")


def calculation_sources(result, engine):
    path = {
        "pilot": "backend/examples/catalog.json",
        "network": RIVER_PATH if getattr(result, "network_id", "").startswith("river-") else "docs/calculations/network/NETWORK.md",
        "russia": "docs/calculations/RUSSIA_NETWORK.md",
        "chukotavia": AIR_PATH,
        "pipeline": PIPELINE_PATH,
    }[engine]
    sources = result.sources
    if engine == "russia":
        used = {"source-01", "russia-freight-assumptions-v1"}
        for row in result.candidates:
            used.update(row["source_ids"])
            used.update(connection["reference_metadata"].get("source_id") for connection in row["connections"])
        sources = [source for source in sources if source["id"] in used]
    if engine == "pipeline" and result.known_components:
        ids = {row["source_id"] for row in result.known_components}
        sources = [source for source in sources if source["id"] in ids]
    return [source_ref(source, source.get("path") or path) for source in sources]


def forecast_summary(result):
    if result.published_coverage is not None:
        return _published_summary(result)
    forecast = result.forecast
    rows = result.candidates
    chosen = next((row for row in rows if row["id"] == result.recommendation), None)
    request = result.request
    route = " → ".join([request["origin"], *(stop["settlement"] for stop in request["stops"]), request["destination"]])
    header = f"{route}, {forecast['target_year']} год"
    schedule = request.get("schedule")
    if schedule and schedule.get("mode") == "once":
        header += f", готовность {schedule['ready_at']}"
    cargo_lines = []
    for cargo in request["cargoes"]:
        pieces = [cargo.get("name") or cargo["cargo_type"], f"{number(cargo['weight_tonnes'])} т"]
        if cargo.get("volume_m3") is not None:
            pieces.append(f"{number(cargo['volume_m3'])} м³")
        if cargo.get("max_piece_tonnes"):
            pieces.append(f"до {number(cargo['max_piece_tonnes'] * 1000)} кг на место")
        if cargo.get("splittable"):
            pieces.append("делимая партия")
        cargo_lines.append(", ".join(pieces))
    if request.get("max_delivery_hours"):
        cargo_lines.append(f"Срок до {number(request['max_delivery_hours'])} ч")
    header = "\n".join([header, *cargo_lines])
    air_row = next((row for row in rows if row["modes"] == ["air"] and "air_median_rub" in row["forecast_price"]), None)
    if air_row is None:
        air_row = next((row for row in rows if "air_median_rub" in row["forecast_price"]), None)
    air_lines = []
    if air_row:
        air = air_row["forecast_price"]
        vat = VAT[request["cost_basis"]]
        label = "Авиа" if air_row["modes"] == ["air"] else "Авиаплечо маршрута " + " → ".join(MODE_LABELS[m] for m in air_row["modes"])
        air_lines.append(f"{label}, базовый сценарий — {number(air['air_median_rub'])} ₽ {vat}.")
        if air["air_softened_rub"] is not None:
            air_lines.append(f"Авиа, сценарий с ослаблением роста — {number(air['air_softened_rub'])} ₽ {vat}.")
        if air_row["multimodal"] and air["known_subtotal_rub"] is not None:
            air_lines.append(f"Оценённые участки этого маршрута — {number(air['known_subtotal_rub'])} ₽ {vat}.")
        air_lines.append("Оценка включает перелёт груза и авианакладную по базе «Якутии». "
                         "Терминальные и агентские сборы оплачиваются отдельно. Суммы служат ориентиром для бюджета.")
    if chosen is None:
        if result.land_market and any(row.get("scenario_note") for row in result.land_market["routes"]):
            return header
        if not rows and any(stop.get("no_reload") for stop in request["stops"]):
            return "\n\n".join([header, "В данных проекта нет подходящей цепочки с остановкой без перегрузки. "
                                         "Уточните сквозной маршрут у перевозчика."])
        continuity = next((row for row in rows if any(
            issue["code"] == "unconfirmed_stop_continuity" for issue in row["issues"]
        )), None)
        if continuity:
            lines = [header, "Перевозка без перегрузки на остановке не подтверждена."]
            if (subtotal := continuity["known_cost_subtotal_rub"]) is not None:
                lines.append(f"Прогноз по опубликованным ставкам отдельных плеч — {number(subtotal)} ₽ "
                             f"{VAT[request['cost_basis']]}. Это часть стоимости.")
            lines.append("У перевозчика нужно подтвердить сохранение контейнера или машины, "
                         "длительность стоянки и возможные сборы.")
            return "\n\n".join(lines)
        if air_lines:
            return "\n\n".join([header, *air_lines])
        if request["allowed_modes"] == ["air"] and result.air_market and (
            result.air_market["routes"] or any(result.air_market["segments"]) or any(result.air_market["weight_limits"])
        ):
            return header
        if not rows and request["allowed_modes"] == ["air"] and result.status == "no_route_in_dataset" and not (
            result.air_market and result.air_market["routes"]
        ):
            return "\n\n".join([header, "Для этого направления в проекте нет прямого авиаплеча с применимой базовой ставкой."])
        if any("max_piece_dimensions_m" in issue["fields"]
               for row in rows if row["modes"] == ["air"] for issue in row["issues"]):
            return "\n\n".join([header, "Для расчёта авиатарифа укажите длину, ширину и высоту одного грузового места."])
        if rows and all("air" in row["modes"] for row in rows) and any(
            issue["code"] == "stage_capacity" for row in rows for issue in row["issues"]
        ):
            return "\n\n".join([
                header,
                "Найденные авиаварианты упираются в ограничение вместимости на одно отправление. "
                "Для этой партии нужно уточнить у перевозчика вместимость или задать несколько отдельных отправлений.",
                "Наземных вариантов с расчётной ценой для этих условий в данных проекта нет.",
            ])
        for cargo in result.request.get("cargoes", []):
            bounds = (cargo.get("properties") or {}).get("required_temperature_c") or cargo.get("temperature_c")
            if bounds is not None:
                low, high = bounds
                return "\n\n".join([
                    header,
                    f"В данных проекта нет варианта с полной ценой и подтверждённым режимом от {low:+g} до {high:+g} °C.",
                    "Для сравнения нужны ставки на перевозку с таким температурным режимом и подтверждение условий обработки на всём маршруте.",
                ])
        return "\n\n".join([
            header,
            "Для этого маршрута и условий не удалось рассчитать полную стоимость ни одного варианта.",
            "В результатах расчёта указано, где не хватило данных или какие условия помешали доставке.",
        ])

    def price(value, row):
        step = 1000 if row["forecast_price"]["long_term"] else 100
        amount = Decimal(str(value))
        rounded = (amount / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step if amount >= step else amount
        return f"{number(rounded)} ₽"

    def name(row):
        labels = {"road": "машина", "rail": "поезд", "air": "самолёт", "river": "судно по реке", "sea": "судно по морю"}
        return " → ".join(labels.get(mode, MODE_LABELS[mode]) for mode in row["modes"])

    def duration(row):
        if row["max_delivery_hours"] is None:
            return "срок доставки неизвестен"
        days, hours = divmod(round(row["max_delivery_hours"]), 24)
        parts = ([f"{days} дн."] if days else []) + ([f"{hours} ч"] if hours or not days else [])
        return f"срок доставки около {' '.join(parts)}"

    budget = chosen["forecast_price"]
    lines = [
        header,
        f"Дешевле всего среди вариантов с полной оценкой — {name(chosen)}. "
        f"Около {price(chosen['total_cost_rub'], chosen)} {VAT[result.request['cost_basis']]}, {duration(chosen)}.",
    ]
    if scope := _tariff_scope(chosen):
        lines.append(scope)
    if budget["lower_rub"] is not None:
        lines.append(f"Ориентир для бюджета — от {price(budget['lower_rub'], chosen)} до {price(budget['upper_rub'], chosen)}.")
    else:
        lines.append("Для выбранного года можно дать только примерную сумму без диапазона цен.")
    if alternative := forecast["alternative"]:
        other = next(row for row in rows if row["id"] == alternative["id"])
        lines.append(f"Другой вариант — {name(other)}, около {price(other['total_cost_rub'], other)}, {duration(other)}.")
        if alternative["ranges_overlap"]:
            lines.append("Диапазоны цен пересекаются. Фактическая цена может поменять порядок вариантов.")
    shown_modes = {tuple(chosen["modes"])}
    if alternative:
        shown_modes.add(tuple(other["modes"]))
    for row in rows:
        modes = tuple(row["modes"])
        if modes in shown_modes or len(set(modes)) != 1 or row["status"] != "feasible" or row["total_cost_rub"] is None:
            continue
        shown_modes.add(modes)
        lines.append(f"{MODE_LABELS[modes[0]]} · около {price(row['total_cost_rub'], row)} "
                     f"{VAT[request['cost_basis']]}, {duration(row)}.")
    lines.append("Сценарный прогноз использует базовые ставки проекта и индексы грузовых тарифов. "
                 "Перед отправкой уточните цену и срок у перевозчика.")
    if forecast["date_precision"] == "year":
        lines.append("Срок сценарный. Для проверки рейсов нужна дата отправки.")
    lines.extend(air_lines)
    return "\n\n".join(lines)


def _published_summary(result, reference=None):
    coverage = reference["coverage"] if reference else result.published_coverage
    candidates = reference["candidates"] if reference else result.candidates
    lines = []
    road = coverage["truck_path"]
    if road["status"] == "found":
        lines.append(f"Грузовой путь по дорожному графу — {number(road['distance_km'])} км.")
    for row in candidates[:3]:
        rate = row["published_rate"]
        tax = "с НДС" if rate["vat"] == "included" else "НДС в источнике не указан"
        entry = f"{rate['origin']} → {rate['destination']}, {rate['equipment']}: {number(rate['published_amount_rub'])} ₽ за единицу ({tax})."
        if rate["forecast_amount_rub"] is not None:
            entry += f" Прогноз по опубликованной базе на {rate['forecast_year']} год — {number(rate['forecast_amount_rub'])} ₽ за единицу."
        if row["known_cost_subtotal_rub"] is not None:
            entry += f" Частичная смета — {number(row['known_cost_subtotal_rub'])} ₽ {VAT[result.request['cost_basis']]}."
        lines.append(entry)
    if not candidates:
        lines.append("Опубликованная ставка для выбранных условий в каталоге не найдена.")
    lines.append("Полная цена и доступность перевозки требуют котировки перевозчика. "
                 "Для этого направления нет фактического ряда объёмов. В отчёте доступна статистика по России.")
    return "\n\n".join(lines)


def engine_summary(result):
    if isinstance(result, ForecastResponse):
        reply = forecast_summary(result)
        if market := result.land_market:
            if (not market.get("unpriced_rail") and result.road_market_estimate and len(market["routes"]) == 1
                    and len(market["routes"][0]["legs"]) == 1
                    and market["routes"][0]["legs"][0]["mode"] == "road"
                    and market["routes"][0]["movement_rub"] == result.road_market_estimate["forecast_road_rub"]):
                market = None
        if market:
            variants = []
            has_bulk_budget = any(row.get("scenario_note") for row in market["routes"])
            for route in market["routes"][:4 if has_bulk_budget else 3]:
                parts = route["legs"]
                reefer = market["version"].startswith("reefer-market")
                scheme = (f"Рефрижераторы 20 т × {parts[0]['units']}" if reefer
                          else " → ".join(MODE_LABELS[leg["mode"]] for leg in parts))
                if route.get("scenario_note"):
                    path = [parts[0]["origin"], *(leg["destination"] for leg in parts if leg["origin"] != leg["destination"])]
                    equipment = next((leg["equipment"] for leg in parts if leg["mode"] == "rail"), None)
                    scheme = f"{scheme} · {' → '.join(path)}"
                    if equipment:
                        scheme += f" · {equipment}"
                if route.get("rail_transfer"):
                    scheme += f" через {route['rail_transfer']}, стыковка по согласованию"
                if len(parts) == 1 and not reefer:
                    scheme += f" ({parts[0]['equipment']})"
                    if parts[0].get("distance_source") in {"market_distance_chain", "regional_carrier_reference"}:
                        leg = parts[0]
                        scheme += f" · {leg['origin']} → {leg['destination']} · машин: {leg['units']}"
                    if parts[0].get("scope") == "door_to_door":
                        scheme += " от двери до двери по условиям прайса"
                    elif parts[0].get("scope") == "warehouse_to_station":
                        scheme += " от склада до станции"
                amount = route["movement_rub"]
                if amount is None:
                    amount_text = "по плечам: " + " + ".join(f"{number(leg['forecast_rub'])} ₽" for leg in parts)
                else:
                    tax = {"included": "с НДС", "excluded": "без НДС", "unknown": "НДС не указан",
                           "mixed": "часть ставок без разбивки НДС"}
                    amount_text = f"{number(amount)} ₽ ({tax[route['vat']]})"
                basis = "с модельной ставкой" if any(leg["kind"] == "model" for leg in parts) else "по опубликованным ставкам"
                variants.append(f"• {scheme}: {amount_text}, {basis}.")
                if any(leg.get("distance_source") in {"market_distance_chain", "regional_carrier_reference"}
                       for leg in parts):
                    variants.append("  Километраж справочный, проезд и срок требуют проверки.")
            if has_bulk_budget:
                notes = [row["scenario_note"] for row in market["routes"] if row.get("scenario_note")]
                if market["version"].startswith("reefer-market"):
                    variants.append(notes[0])
                else:
                    variants.append("Суммы служат ориентиром по учтённым плечам. График, вместимость и условия стыковки уточняются.")
            if variants:
                reply += "\n\nПрогноз по вариантам перевозки:\n" + "\n".join(variants)
            elif any(market["legs"]):
                reply += "\n\nВ разделе рыночных оценок есть цены отдельных плеч. Для остальных нужна котировка."
            for leg in market.get("unpriced_rail", [])[:3]:
                reply += f"\n\nЖ/д {leg['origin']} → {leg['destination']}: оператор публикует контейнерный сервис. Для оценки цены нужна котировка."
        if estimate := result.road_market_estimate:
            reply += (f"\n\nСценарная оценка основного автоплеча на {estimate['target_year']} год — "
                      f"{number(estimate['forecast_road_rub'])} ₽. Количество машин — {estimate['trucks']}, "
                      f"до {estimate['tonnes_per_truck']} т и {estimate['assumed_m3_per_truck']} м³ на машину. "
                      f"Прогноз ставки — {number(estimate['forecast_unit_rub'])} ₽ за машину. "
                      "НДС не указан. Подтвердите раскладку и цену всей партии у перевозчика.")
        if air := result.air_market:
            if air["routes"]:
                variants = []
                for option in air["routes"][:3]:
                    legs = option["legs"]
                    path = " → ".join([f"{legs[0]['origin']} ({legs[0]['departure']})",
                                       *(point for leg in legs for point in (*leg["via"], leg["destination"] +
                                         (f" ({leg['arrival']})" if leg["arrival"] else "")))])
                    tax = "с НДС" if option["vat"] == "included" else "без НДС"
                    split = [f"{leg['shipment_count']} × {number(round(leg['paid_kg_per_shipment'], 2))} кг"
                             for leg in legs if leg["shipment_count"] > 1]
                    detail = f" Сценарное деление по платному весу: {', '.join(split)}." if split else ""
                    if option["transfer_count"]:
                        detail += " Стыковку отдельных перевозок нужно согласовать."
                    variants.append(f"• {path} — {number(option['forecast_rub'])} ₽ ({tax}).{detail}")
                reply += (f"\n\nАвиа · прогноз на {air['target_year']} год по опубликованным ставкам:\n"
                          + "\n".join(variants))
                if any(leg["shipment_count"] > 1 for option in air["routes"][:3] for leg in option["legs"]):
                    cargo = result.request["cargoes"][0]
                    reply += (f"\n\nОбъём партии в расчёте — {number(cargo['volume_m3'])} м³. "
                              "Платный вес берётся по большей из двух величин, массе и объёмному весу. "
                              "Для отправки согласуйте раскладку по грузовым местам и запросите ставку на всю партию.")
                reply += " Подвоз, довоз и доступность рейсов уточняются."
            elif any(air["segments"]):
                reply += "\n\nВ авиационных прайсах найдены ставки на отдельные плечи. Для всего маршрута нужна котировка."
            if not air["routes"] and (limit := next((item for item in air["weight_limits"] if item), None)):
                reply += (f"\n\nАвиатариф найден. Платный вес всей партии на этом плече — "
                          f"{number(limit['paid_kg'])} кг. Для расчёта одной отправки по общей ставке нужен "
                          f"платный вес менее {number(limit['threshold_kg'])} кг. "
                          "Уточните объём или запросите у перевозчика ставку на всю партию и допуск грузовых мест.")
        if result.published_reference and result.published_reference["candidates"]:
            reply += "\n\n" + _published_summary(result, result.published_reference)
        return reply
    if isinstance(result, RussiaResponse) and result.published_coverage is not None:
        return _published_summary(result)
    if isinstance(result, PipelineResponse):
        data = snapshot(PIPELINE_PATH)
        points = pipeline_points(data)
        route = next(row for row in data["routes"] if row["id"] == result.request["route_id"])
        direction = f"{points[route['origin']]['name']} → {points[route['destination']]['name']}"
        mass = number(result.request["net_weight_tonnes"])
        lines = [f"Товарная нефть, {direction}, {mass} т нетто."]
        for row in result.known_components:
            lines.append(f"Опубликованная часть стоимости — {number(row['amount_excluding_vat_rub'])} ₽ без НДС "
                         f"по ставке {number(row['rate_rub'])} ₽/т.")
        if not result.known_components:
            lines.append("По запрошенной дате опубликованный компонент не рассчитан.")
        return "\n".join([*lines, *result.warnings])

    if isinstance(result, ChukotaviaResponse):
        overview = result.overview
        lines = [f"ЧукотАВИА, {result.request['origin']} → {result.request['destination']}. "
                 f"Отправок — {overview['shipment_count']}, расчётный вес на отправку — "
                 f"{number(round(float(overview['chargeable_weight_kg_per_shipment']), 2))} кг."]
        shown = 0
        for shipment in result.shipments:
            candidates = shipment["candidates"]
            if not candidates:
                continue
            candidate = candidates[0]
            shown += 1
            line = f"Отправка {shipment['number']}, первый плановый вариант — {candidate['flight']}, {candidate['departure_at']}."
            cost = candidate["transport_component_estimate_rub"]
            if cost is not None:
                line += f" Тарифная составляющая — {number(cost)} ₽ {VAT[overview['cost_basis']]}."
            if candidate["scenario_delivery_hours"] is not None:
                line += f" Срок по заданному сценарию — {number(round(candidate['scenario_delivery_hours'], 2))} ч."
            lines.append(line)
            if shown == 3:
                break
        if not shown:
            lines.append("По условиям запроса плановые варианты в снимке отсутствуют.")
        assumptions = overview["assumptions"]
        if assumptions:
            lines.append("Допущения пользователя в часах — "
                         f"сдача за {number(assumptions['cutoff_hours'])}, обработка перед вылетом "
                         f"{number(assumptions['origin_processing_hours'])}, после прилёта "
                         f"{number(assumptions['destination_processing_hours'])}.")
        return "\n".join([*lines, *result.limitations])

    lines = []
    chosen = next((row for row in result.candidates if row["id"] == result.recommendation), None)
    if chosen:
        modes = " → ".join(MODE_LABELS[mode] for mode in chosen["modes"])
        lines.append(f"Рекомендованный вариант — {modes}. "
                     f"Примерная стоимость — {number(chosen['total_cost_rub'])} ₽ {VAT[result.request['cost_basis']]}. "
                     f"Самая долгая доставка займёт около {number(chosen['max_delivery_hours'])} ч.")
        if isinstance(result, RussiaResponse):
            if scope := _tariff_scope(chosen):
                lines.append(scope)
            lines.append("Цену, срок и наличие мест уточните у перевозчика. "
                         "Другие варианты и подробности доступны в результатах расчёта.")
            return "\n\n".join(lines)
    else:
        if coverage := _expired_river_summary(result):
            return coverage
        if not result.candidates and isinstance(result, RussiaResponse):
            return "По заданным условиям не удалось найти маршрут. Проверьте даты и выбранные виды транспорта."
        lines.append("Среди рассчитанных вариантов подходящих для ваших условий не нашлось.")
        partial = next((row for row in result.candidates if row.get("known_cost_subtotal_rub")), None)
        if partial:
            lines.append(f"По опубликованным ставкам рассчитана часть стоимости — "
                         f"{number(partial['known_cost_subtotal_rub'])} ₽ {VAT[result.request['cost_basis']]}.")
        reasons = dict.fromkeys(issue["message"] for row in result.candidates for issue in row["issues"])
        lines.extend(f"Причина: {message.rstrip('.')}." for message in list(reasons)[:3])
    partial = next((row["published_tariff_estimate"] for row in result.candidates
                    if "published_tariff_estimate" in row), None)
    if partial:
        lines.append(f"В одном из вариантов известна цена только за перевозку по трубопроводу — "
                     f"{number(partial['amount_excluding_vat_rub'])} ₽ без НДС.")
    if isinstance(result, NetworkResponse):
        lines.extend(result.assumptions)
        lines.extend(result.warnings)
    return "\n\n".join(lines)
