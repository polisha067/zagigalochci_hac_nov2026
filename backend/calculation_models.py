"""Контракт расчёта 0.2. Неизвестные параметры никогда не игнорируются."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Dict, List, Literal, Optional, Union

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StringConstraints, field_validator, model_validator


MAX_SHIPMENTS = 512
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Positive = Annotated[StrictFloat, Field(gt=0, le=1_000_000)]
Hours = Annotated[StrictFloat, Field(gt=0, le=87600)]
NonnegativeHours = Annotated[StrictFloat, Field(ge=0, le=87600)]


def date_input(value):
    if not isinstance(value, (str, datetime)):
        raise ValueError("Передайте дату ISO 8601 с часовым поясом, не число")
    return value


def date_bounds(value):
    if not 2000 <= value.year <= 2100:
        raise ValueError("Технический диапазон дат: 2000–2100 годы")
    return value


Moment = Annotated[AwareDatetime, BeforeValidator(date_input), AfterValidator(date_bounds)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OnceSchedule(InputModel):
    mode: Literal["once"]
    ready_at: Moment


class Frequency(InputModel):
    count: Annotated[StrictInt, Field(ge=1, le=168)]
    every: Annotated[StrictInt, Field(ge=1, le=3650)] = 1
    unit: Literal["minute", "hour", "day", "week", "month"]


class RegularSchedule(InputModel):
    mode: Literal["regular"]
    start_at: Moment
    end_at: Moment
    frequency: Frequency

    @model_validator(mode="after")
    def valid_period(self):
        if self.end_at <= self.start_at:
            raise ValueError("Конец периода должен быть позже начала; правая граница не включается")
        if self.end_at - self.start_at > timedelta(days=3660):
            raise ValueError("Технический предел периода — 3660 дней")
        return self


class ExplicitSchedule(InputModel):
    mode: Literal["explicit"]
    ready_at: Annotated[List[Moment], Field(min_length=1, max_length=MAX_SHIPMENTS)]

    @field_validator("ready_at")
    @classmethod
    def unique_dates(cls, values):
        values = sorted(values)
        if len(set(values)) != len(values):
            raise ValueError("Даты готовности не должны повторяться")
        if values[-1] - values[0] > timedelta(days=3660):
            raise ValueError("Технический предел периода — 3660 дней")
        return values


Schedule = Annotated[Union[OnceSchedule, RegularSchedule, ExplicitSchedule], Field(discriminator="mode")]


class RoutePoint(InputModel):
    city: Text
    service_hours: NonnegativeHours = 0


class CargoItem(InputModel):
    id: Text
    name: Optional[Text] = None
    cargo_type: Optional[Text] = None
    weight_tonnes: Positive
    volume_m3: Optional[Positive] = None
    load_at: Annotated[StrictInt, Field(ge=0, le=11)] = 0
    unload_at: Annotated[Optional[StrictInt], Field(ge=1, le=11)] = None
    splittable: StrictBool


    @model_validator(mode="after")
    def identity_present(self):
        if self.name is None and self.cargo_type is None:
            raise ValueError("Укажите название груза")
        return self


class CalculationRequest(InputModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, json_schema_extra={"example": {
        "dataset_id": "synthetic-pilot-v2",
        "route": [{"city": "Москва", "service_hours": 0}, {"city": "Брянск", "service_hours": 0}],
        "cargoes": [{"id": "flour-1", "cargo_type": "flour", "weight_tonnes": 10, "load_at": 0, "unload_at": 1, "splittable": True}],
        "schedule": {"mode": "regular", "start_at": "2026-11-09T00:00:00+03:00", "end_at": "2026-12-07T00:00:00+03:00", "frequency": {"count": 2, "every": 1, "unit": "week"}},
        "max_delivery_hours": 48, "capacity_basis": "mass_only",
    }})
    dataset_id: Text = "synthetic-pilot-v2"
    route: Annotated[List[RoutePoint], Field(min_length=2, max_length=12)]
    cargoes: Annotated[List[CargoItem], Field(min_length=1, max_length=20)]
    schedule: Schedule
    max_delivery_hours: Hours
    capacity_basis: Literal["mass_only", "mass_and_volume"]

    @model_validator(mode="after")
    def valid_route_and_cargo(self):
        for left, right in zip(self.route, self.route[1:]):
            if left.city.casefold() == right.city.casefold():
                raise ValueError("Соседние города совпадают. Для внутригородской перевозки нужны отдельные адреса и тарифы")
        ids = [item.id for item in self.cargoes]
        if len(set(ids)) != len(ids):
            raise ValueError("У каждой позиции груза должен быть уникальный id")
        last = len(self.route) - 1
        for item in self.cargoes:
            unload = last if item.unload_at is None else item.unload_at
            if not 0 <= item.load_at < unload <= last:
                raise ValueError(f"Груз {item.id}: погрузка должна предшествовать выгрузке в пределах маршрута")
            if self.capacity_basis == "mass_and_volume" and item.volume_m3 is None:
                raise ValueError(f"Груз {item.id}: для проверки объёма нужен volume_m3")
        if not any(item.load_at == 0 for item in self.cargoes):
            raise ValueError("Хотя бы один груз должен загружаться в начальном пункте")
        if not any((last if item.unload_at is None else item.unload_at) == last for item in self.cargoes):
            raise ValueError("Хотя бы один груз должен доставляться в конечный пункт")
        return self


class SchemeResult(BaseModel):
    scheme: Literal["spot_road", "contract_road", "road_rail_road"]
    status: Literal["feasible", "infeasible", "insufficient_data"]
    reason: str
    missing_fields: List[str] = Field(default_factory=list)
    total_cost_rub: Optional[float] = None
    cost_rub_per_tonne: Optional[float] = None
    savings_vs_base_rub: Optional[float] = None
    savings_vs_base_percent: Optional[float] = None
    max_delivery_hours: Optional[float] = None
    departures: Optional[int] = None
    physical_trips: Optional[int] = None
    paid_trips: Optional[int] = None
    breakdown_rub: Dict[str, float] = Field(default_factory=dict)
    details: dict = Field(default_factory=dict)
    source_ids: List[str] = Field(default_factory=list)


class CalculationResponse(BaseModel):
    schema_version: str
    calculation_mode: Literal["scenario"]
    request: dict
    dataset_id: str
    data_status: str
    shipments: int
    total_volume_tonnes: float
    planning_period: dict
    ready_at: List[str]
    volume_by_week: List[dict]
    load_profile: List[dict]
    schemes: List[SchemeResult]
    recommendation: Optional[str]
    recommendation_scope: str
    assumptions: List[str]
    warnings: List[str]
    sources: List[dict]
    input_status: Literal["ready", "needs_clarification"] = "ready"
    clarifications: List[dict] = Field(default_factory=list)
