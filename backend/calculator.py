"""Сценарная оценка объёма и стоимости регулярных автоперевозок."""

import math


def number(data, field, *, integer=False, zero=False):
    value = data.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field}: укажите число")
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError(f"{field}: недопустимое значение")
    if integer and value != int(value):
        raise ValueError(f"{field}: укажите целое число")
    return int(value) if integer else value


def compare_delivery(request, dataset):
    """Чистая функция: одинаковые входные данные дают одинаковый результат."""
    allowed = {"origin", "destination", "cargo", "batch_tonnes", "shipments_per_week", "horizon_weeks", "max_delivery_days"}
    extra = set(request) - allowed
    if extra:
        raise ValueError("Старый формат не поддерживает поля: " + ", ".join(sorted(extra)) + ". Используйте новый POST /calculations")
    for field in ("origin", "destination", "cargo"):
        if not isinstance(request.get(field), str) or not request[field].strip():
            raise ValueError(f"{field}: заполните поле")
        if request[field] != dataset.get(field):
            raise ValueError(f"{field}: набор тарифов не соответствует запросу")
    weight = number(request, "batch_tonnes")
    frequency = number(request, "shipments_per_week", integer=True)
    weeks = number(request, "horizon_weeks", integer=True)
    deadline = number(request, "max_delivery_days")
    volume = weight * frequency * weeks
    schemes = []
    for name in ("spot_road", "contract_road"):
        terms = dataset.get(name) or {}
        required = ["capacity_tonnes", "rate_rub_per_trip", "delivery_days"]
        if name == "contract_road":
            required.append("minimum_paid_trips_per_week")
        missing = [field for field in required if terms.get(field) is None]
        if missing:
            schemes.append({"scheme": name, "status": "insufficient_data", "missing_fields": missing})
            continue
        capacity = number(terms, "capacity_tonnes")
        rate = number(terms, "rate_rub_per_trip")
        days = number(terms, "delivery_days")
        minimum = number(terms, "minimum_paid_trips_per_week", integer=True, zero=True) if name == "contract_road" else 0
        weekly_trips = math.ceil(weight / capacity) * frequency
        paid_trips = max(weekly_trips, minimum) * weeks
        cost = round(paid_trips * rate, 2)
        schemes.append({
            "scheme": name,
            "status": "feasible" if days <= deadline else "infeasible",
            "reason": "Срок соблюдён" if days <= deadline else "Срок превышает допустимый",
            "physical_trips": weekly_trips * weeks,
            "paid_trips": paid_trips,
            "delivery_days": days,
            "total_cost_rub": cost,
            "cost_rub_per_tonne": round(cost / volume, 2),
        })
    schemes.append({
        "scheme": "road_rail_road",
        "status": "insufficient_data",
        "reason": "Расчёт ж/д цепочки пока не реализован; услуга не подтверждена",
        "missing_fields": ["minimum_batch_tonnes", "first_mile_cost", "rail_cost", "handling_cost", "last_mile_cost", "chain_delivery_days", "consolidation_wait_days"],
    })
    base = schemes[0].get("total_cost_rub")
    for scheme in schemes:
        if base is not None and "total_cost_rub" in scheme:
            scheme["savings_vs_base_rub"] = round(base - scheme["total_cost_rub"], 2)
    feasible = [scheme for scheme in schemes if scheme["status"] == "feasible"]
    best = min(feasible, key=lambda scheme: scheme["total_cost_rub"]) if feasible else None
    return {
        "schema_version": "0.1",
        "request": dict(request),
        "calculation_mode": "scenario",
        "volume_method": "Вес партии × отправки в неделю × число недель; рост не задан",
        "total_volume_tonnes": volume,
        "shipments": frequency * weeks,
        "volume_by_week": [{"week": i, "tonnes": weight * frequency} for i in range(1, weeks + 1)],
        "schemes": schemes,
        "recommendation": best["scheme"] if best else None,
        "recommendation_scope": "Среди рассчитанных схем с допустимым сроком",
        "data_status": dataset.get("status", "unspecified"),
        "source": dataset.get("source"),
        "assumptions": [
            "Каждая отправка отдельная; объединение партий не применяется",
            "Если партия больше вместимости, доступны параллельные машины",
            "Срок считается от готовности груза до доставки",
            "Ставки включают всю автодоставку и сопоставимы по налогам и доплатам",
            "Договорный минимум оплачивается каждую неделю",
            "Это плановая сценарная оценка, не прогноз по истории",
        ],
    }
