from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import Field, StrictFloat, StrictInt, StringConstraints, model_validator

from backend.calculation_models import InputModel, Text


TransportMode = Literal["road", "rail", "air", "water"]
GrowthMetric = Literal["tonnes", "percent"]
Year = Annotated[StrictInt, Field(ge=2000, le=2100)]
Tonnes = Annotated[StrictFloat, Field(ge=0, le=1_000_000_000_000)]
Method = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
TRANSPORT_LABELS = {
    "road": "автомобильных",
    "rail": "железнодорожных",
    "air": "воздушных",
    "water": "водных",
}


class ForecastQuery(InputModel):
    year: Year
    transport: TransportMode
    metric: GrowthMetric = "tonnes"
    limit: Annotated[StrictInt, Field(ge=1, le=20)] = 5


class RegionVolumes(InputModel):
    region_id: Text
    region: Text
    base_tonnes: Tonnes
    forecast_tonnes: Tonnes


class ForecastSeries(InputModel):
    year: Year
    base_year: Year
    transport: TransportMode
    method: Method
    regions: list[RegionVolumes] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_comparison(self):
        if self.base_year >= self.year:
            raise ValueError("Базовый год должен предшествовать прогнозному")
        if len({row.region_id for row in self.regions}) != len(self.regions):
            raise ValueError("В прогнозе есть повторяющиеся идентификаторы регионов")
        return self


class ForecastDataset(InputModel):
    title: Text
    source_url: Annotated[str, StringConstraints(pattern=r"^https?://[^\s]+$")]
    series: list[ForecastSeries] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_series(self):
        if len({(series.year, series.transport) for series in self.series}) != len(self.series):
            raise ValueError("Для года и вида транспорта допустим один ряд прогноза")
        return self


class RegionGrowth(RegionVolumes):
    growth_tonnes: float
    growth_percent: Optional[float]


class RegionalForecast(InputModel):
    status: Literal["available", "unavailable"]
    query: ForecastQuery
    base_year: Optional[Year] = None
    method: Optional[Method] = None
    regions_covered: int = 0
    rows: list[RegionGrowth] = Field(default_factory=list)


def load_regional_forecast(path: Optional[Path]) -> Optional[ForecastDataset]:
    if path is None:
        return None
    return ForecastDataset.model_validate_json(path.read_text(encoding="utf-8"))


def rank_regional_forecast(query: ForecastQuery, dataset: Optional[ForecastDataset]) -> RegionalForecast:
    series = next(
        (item for item in dataset.series if (item.year, item.transport) == (query.year, query.transport)),
        None,
    ) if dataset is not None else None
    if series is None:
        return RegionalForecast(status="unavailable", query=query)

    rows = []
    for region in series.regions:
        growth = region.forecast_tonnes - region.base_tonnes
        if growth <= 0 or (query.metric == "percent" and region.base_tonnes == 0):
            continue
        percent = growth / region.base_tonnes * 100 if region.base_tonnes else None
        rows.append(RegionGrowth(**region.model_dump(), growth_tonnes=growth, growth_percent=percent))
    rows.sort(key=lambda row: (
        -(row.growth_tonnes if query.metric == "tonnes" else row.growth_percent), row.region_id,
    ))
    return RegionalForecast(
        status="available", query=query, base_year=series.base_year, method=series.method,
        regions_covered=len(series.regions), rows=rows[:query.limit],
    )


def forecast_reply(forecast: RegionalForecast) -> str:
    query = forecast.query
    transport = TRANSPORT_LABELS[query.transport]
    if forecast.status == "unavailable":
        return f"В каталоге нет регионального прогноза {transport} грузоперевозок на {query.year} год."
    if not forecast.rows:
        return f"Среди {forecast.regions_covered} регионов прогноза нет положительного роста по выбранному показателю."

    metric = "в тоннах" if query.metric == "tonnes" else "в процентах"
    lines = [f"Прогноз роста {transport} грузоперевозок к {query.year} году относительно {forecast.base_year} года. Рейтинг {metric} среди {forecast.regions_covered} регионов."]
    for rank, row in enumerate(forecast.rows, 1):
        tonnes = f"{row.growth_tonnes:,.0f}".replace(",", " ")
        percent = f" (+{row.growth_percent:.1f}%)" if row.growth_percent is not None else ""
        lines.append(f"{rank}. {row.region} — +{tonnes} т{percent}")
    lines.append(f"Методика — {forecast.method}")
    return "\n".join(lines)
