"""Versioned annual freight tariff series and reproducible scenario factors."""

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import cached_property, lru_cache
from math import prod
from pathlib import Path
from statistics import median


SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data_sources/delivery_forecast/indices.json"
WINDOW = 5
MIN_BACKTEST_WINDOWS = 3


def quantile(values, fraction):
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * Decimal(fraction)
    index = int(position)
    return ordered[index] + (ordered[min(index + 1, len(ordered) - 1)] - ordered[index]) * (position - index)


@dataclass(frozen=True)
class IndexPoint:
    year: int
    factor: Decimal
    source_id: str
    kind: str

    def as_dict(self):
        return dict(year=self.year, factor=float(self.factor), source_id=self.source_id, kind=self.kind)


@dataclass(frozen=True)
class IndexSeries:
    actual: dict[int, IndexPoint]
    published: dict[int, IndexPoint]
    model_source: str

    @cached_property
    def last_actual(self):
        return max(self.actual, default=0)

    def history(self, end):
        years = range(end - WINDOW + 1, end + 1)
        return [self.actual[year].factor for year in years] if all(year in self.actual for year in years) else []

    @staticmethod
    def central(history):
        # For an odd window median(log(x)).exp() equals median(x).
        return median(history)

    def backtest(self, horizon):
        rows = []
        for end in range(min(self.actual, default=0) + WINDOW - 1, self.last_actual - horizon + 1):
            history = self.history(end)
            future = [self.actual.get(year) for year in range(end + 1, end + horizon + 1)]
            if history and all(future):
                actual = prod(point.factor for point in future)
                rows.append((actual, self.central(history) ** horizon, history[-1] ** horizon))
        return rows

    @cached_property
    def bands(self):
        low = high = Decimal(0)
        bands = {}
        for horizon in range(1, max(1, len(self.actual) - WINDOW + 1)):
            rows = self.backtest(horizon)
            if len(rows) < MIN_BACKTEST_WINDOWS:
                break
            errors = [(actual / model).ln() for actual, model, _ in rows]
            low, high = min(low, quantile(errors, "0.2")), max(high, quantile(errors, "0.8"))
            bands[horizon] = (low.exp(), high.exp())
        return bands


@dataclass(frozen=True)
class IndexEstimate:
    central: Decimal
    lower: Decimal | None
    upper: Decimal | None
    points: tuple[IndexPoint, ...]
    proxy_for: str | None
    long_term: bool


class ForecastIndices:
    def __init__(self, data):
        if data["basis"] != "december_over_previous_december":
            raise ValueError("Несопоставимая база индексов прогноза")
        self.version = data["version"]
        self.max_year = data["max_year"]
        self.sources = {source["id"]: source for source in data["sources"]}
        self.proxies = data["proxies"]
        if len(self.sources) != len(data["sources"]):
            raise ValueError("Дубли источников индексации")
        self.series = {}
        for category, definition in data["series"].items():
            actual, published = {}, {}
            for row in definition["points"]:
                year, value, source_id, kind = row
                target = actual if kind == "observed" else published
                if kind not in ("observed", "published", "published_scenario") or year in actual or year in published:
                    raise ValueError("Повтор или неверный тип годового индекса")
                factor = Decimal(str(value)) / 100
                if not factor.is_finite() or factor <= 0 or source_id not in self.sources:
                    raise ValueError("Неверное значение или источник индекса")
                target[year] = IndexPoint(year, factor, source_id, kind)
            model_source = definition["model_source"]
            if model_source not in self.sources:
                raise ValueError("Не указан источник сценарного расчёта индекса")
            self.series[category] = IndexSeries(actual, published, model_source)
        if any(proxy not in self.series for proxy in self.proxies.values()):
            raise ValueError("Отсутствует заменяющий ряд индексов")

    def estimate(self, category, base_year, target_year):
        if target_year < base_year or target_year > self.max_year:
            return None
        proxy = self.proxies.get(category)
        series = self.series.get(proxy or category)
        central = Decimal(1)
        points = []
        estimated_years = []
        for year in range(base_year + 1, target_year + 1):
            if series is None:
                return None
            point = series.actual.get(year) or series.published.get(year)
            if point is None:
                if year <= series.last_actual:
                    return None
                history = series.history(series.last_actual)
                if not history:
                    return None
                point = IndexPoint(year, series.central(history), series.model_source, "estimated")
                estimated_years.append(year)
            elif point.kind == "published_scenario":
                estimated_years.append(year)
            central *= point.factor
            points.append(point)
        horizon = max(estimated_years, default=base_year) - series.last_actual if estimated_years else 0
        band = series.bands.get(horizon) if horizon else (Decimal(1), Decimal(1))
        return IndexEstimate(
            central, central * band[0] if band else None, central * band[1] if band else None,
            tuple(points), category if proxy else None, bool(horizon and not band),
        )


@lru_cache(maxsize=1)
def load_indices():
    return ForecastIndices(json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
