"""Forecasting from the frozen synthetic A1 route benchmark."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from statistics import mean
from typing import Literal

BENCHMARK = Path(__file__).resolve().parent / "data_sources/a1_forecast/route-benchmark-v2"
MIN_FORECAST_YEAR = 2026
MAX_FORECAST_YEAR = 2100
START = "2021-01"
TRAIN_MONTHS = 48
HOLDOUT_MONTHS = 12
Granularity = Literal["year", "quarter", "month"]


def fit(values):
    """Least squares with a common linear trend and a separate level per month."""
    groups = [list(range(month, len(values), 12)) for month in range(12)]
    centers = [(mean(group), mean(values[i] for i in group)) for group in groups]
    numerator = denominator = 0.0
    for group, (time_mean, volume_mean) in zip(groups, centers):
        for i in group:
            numerator += (i - time_mean) * (values[i] - volume_mean)
            denominator += (i - time_mean) ** 2
    slope = numerator / denominator

    def predict(i):
        time_mean, volume_mean = centers[i % 12]
        return round(max(0.0, volume_mean + slope * (i - time_mean)), 1)

    return predict


def month_label(start, offset):
    year, month = map(int, start.split("-"))
    years, month = divmod(month - 1 + offset, 12)
    return f"{year + years:04d}-{month + 1:02d}"


def read_snapshot(name, manifest):
    raw = (BENCHMARK / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest["files_sha256"][name]:
        raise ValueError(f"Benchmark file changed: {name}")
    return json.loads(raw)


def series_by_route(rows, route_ids, start_index, size):
    series = {route_id: {} for route_id in route_ids}
    for row in rows:
        values = series[row["route_id"]]
        if row["month"] in values or row["tonnes"] <= 0:
            raise ValueError(f"Invalid benchmark observation: {row}")
        values[row["month"]] = row["tonnes"]
    expected = [month_label(START, i) for i in range(start_index, start_index + size)]
    if any(set(values) != set(expected) for values in series.values()):
        raise ValueError("Each route must have exactly the expected months")
    return {route_id: [values[month] for month in expected] for route_id, values in series.items()}



@lru_cache(maxsize=1)
def load_routes():
    manifest = json.loads((BENCHMARK / "manifest.json").read_bytes())
    return read_snapshot("routes.json", manifest), manifest


@lru_cache(maxsize=1)
def route_models():
    routes, manifest = load_routes()
    ids = [route["route_id"] for route in routes]
    train = series_by_route(read_snapshot("train.json", manifest), ids, 0, TRAIN_MONTHS)
    holdout = series_by_route(
        read_snapshot("holdout.json", manifest), ids, TRAIN_MONTHS, HOLDOUT_MONTHS
    )
    return {route_id: fit(train[route_id] + holdout[route_id]) for route_id in ids}


def forecast_options():
    routes, manifest = load_routes()
    return {
        "routes": [
            {key: route[key] for key in ("route_id", "origin", "destination")}
            for route in routes
        ],
        "min_year": MIN_FORECAST_YEAR,
        "max_year": MAX_FORECAST_YEAR,
        "granularities": ["year", "quarter", "month"],
        "last_observed_year": MIN_FORECAST_YEAR - 1,
        "volume_status": manifest["volume_status"],
    }


def forecast_route(route_id, start_year: int, end_year: int, granularity: Granularity = "year"):
    if not MIN_FORECAST_YEAR <= start_year <= end_year <= MAX_FORECAST_YEAR:
        raise ValueError(f"Укажите период от {MIN_FORECAST_YEAR} до {MAX_FORECAST_YEAR}: начальный год не позже конечного")
    options = forecast_options()
    route = next((route for route in options["routes"] if route["route_id"] == route_id), None)
    if route is None:
        raise KeyError(route_id)
    predict = route_models()[route_id]
    first = (start_year - int(START[:4])) * 12
    count = (end_year - start_year + 1) * 12
    months_per_period = {"year": 12, "quarter": 3, "month": 1}[granularity]
    periods = []
    for offset in range(0, count, months_per_period):
        year, month = divmod(offset, 12)
        year += start_year
        if granularity == "quarter":
            period = f"{year}-Q{month // 3 + 1}"
        elif granularity == "month":
            period = month_label(START, first + offset)
        else:
            period = str(year)
        tonnes = sum(predict(first + offset + i) for i in range(months_per_period))
        periods.append({"period": period, "forecast_tonnes": round(tonnes, 1)})
    return {
        **route,
        "start_year": start_year,
        "end_year": end_year,
        "granularity": granularity,
        "last_observed_year": options["last_observed_year"],
        "volume_status": options["volume_status"],
        "forecast_tonnes": round(sum(row["forecast_tonnes"] for row in periods), 1),
        "periods": periods,
    }
