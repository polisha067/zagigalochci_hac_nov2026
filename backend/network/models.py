from datetime import date, datetime
from typing import Annotated, Dict, List, Literal, Optional, Tuple

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.calculation_models import Hours, InputModel, Moment, NonnegativeHours, Positive, Schedule, Text


Mode = Literal["road", "rail", "sea", "river", "air", "pipeline"]
Category = Literal["road", "rail", "sea", "river", "air", "pipeline", "multimodal"]
Nonnegative = Annotated[float, Field(strict=True, ge=0, le=1e9)]
Count = Annotated[StrictInt, Field(ge=0, le=10000)]


class Window(InputModel):
    start: Moment
    end: Moment

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Конец окна должен быть позже начала")
        return self


class Evidence(InputModel):
    source_ids: Annotated[List[Text], Field(min_length=1, max_length=10)]
    status: Literal["fact", "market_reference", "assumption", "synthetic", "unknown"]
    observed_on: date
    valid_from: Moment
    valid_until: Moment

    @model_validator(mode="after")
    def ordered(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("Некорректный срок действия сведений")
        return self


class WeightTariff(InputModel):
    minimum_kg: Positive
    actual_weight_step_kg: Positive
    bands: Dict[Nonnegative, Nonnegative]

    @model_validator(mode="after")
    def complete(self):
        if 0 not in self.bands:
            raise ValueError("Для весового тарифа нужна начальная ступень")
        return self


class Quote(InputModel):
    component: Literal["transport", "handling", "storage", "all_in"]
    currency: Literal["RUB"] = "RUB"
    basis: Literal["per_unit", "per_tonne", "per_kg", "per_tonne_km", "weight_or_measure", "per_tonne_hour", "per_unit_day", "per_100_tonnes", "per_100_tonne_km"]
    rate_rub: Optional[Nonnegative] = None
    minimum_rub: Nonnegative = 0
    fixed_rub: Nonnegative = 0
    volumetric_kg_per_m3: Optional[Positive] = None
    tonnes_per_m3: Optional[Positive] = None
    weight_tariff: Optional[WeightTariff] = None
    tax_basis: Literal["including_vat", "excluding_vat", "unknown"]
    tax_rate: Annotated[Optional[float], Field(strict=True, ge=0, le=1)] = None
    evidence: Evidence

    @model_validator(mode="after")
    def weight_basis(self):
        if self.weight_tariff is not None and self.basis != "per_kg":
            raise ValueError("Весовые ступени применяются к ставкам за килограмм")
        return self


class Operation(InputModel):
    fixed_hours: NonnegativeHours
    tonnes_per_hour: Optional[Positive] = None
    resource_id: Text
    lanes: Count
    quote: Optional[Quote] = None
    evidence: Evidence


class CargoType(InputModel):
    name: Text
    aliases: List[Text] = Field(default_factory=list)
    phase: Literal["solid", "liquid", "gas"]
    hazardous: StrictBool
    handling_profile: Text
    packagings: Annotated[List[Text], Field(min_length=1)]
    required_temperature_c: Optional[Tuple[float, float]] = None
    evidence: Evidence


class Hub(InputModel):
    id: Text
    name: Text
    city: Text
    kind: Literal["warehouse", "rail_terminal", "seaport", "river_port", "airport", "pipeline_terminal", "logistics_hub"]
    settlement_id: Optional[Text] = None
    identity_status: Literal["scenario", "reference", "confirmed"] = "scenario"
    reference_ids: List[Text] = Field(default_factory=list)
    country: Literal["RU"] = "RU"
    available: Optional[StrictBool] = None
    latitude: Annotated[Optional[float], Field(strict=True, ge=-90, le=90)] = None
    longitude: Annotated[Optional[float], Field(strict=True, ge=-180, le=180)] = None
    modes: Annotated[List[Mode], Field(min_length=1, max_length=6)]
    rail_gauges_mm: Optional[List[Annotated[StrictInt, Field(gt=0, le=3000)]]] = None
    cargo_types: Annotated[List[Text], Field(min_length=1)]
    cargo_profiles: List[Text] = Field(default_factory=list)
    packagings: Annotated[List[Text], Field(min_length=1)]
    hazardous_allowed: Optional[StrictBool] = None
    temperature_c: Optional[Tuple[float, float]] = None
    windows: Annotated[List[Window], Field(min_length=1, max_length=64)]
    storage_tonnes: Optional[Nonnegative] = None
    storage_m3: Optional[Nonnegative] = None
    max_storage_hours: Optional[NonnegativeHours] = None
    storage_quote: Optional[Quote] = None
    storage_free_hours: NonnegativeHours = 0
    entry: Optional[Operation] = None
    exit: Optional[Operation] = None
    evidence: Evidence

    @model_validator(mode="after")
    def storage_units(self):
        if self.storage_quote is not None and (self.storage_quote.basis not in ("per_tonne_hour", "per_unit_day") or self.storage_quote.minimum_rub != 0 or self.storage_quote.fixed_rub != 0):
            raise ValueError("Хранение тарифицируется за тонно-час либо контейнеро-сутки, без фиксированных сборов")
        return self


class Timetable(InputModel):
    kind: Literal["on_demand", "interval", "explicit"]
    anchor: Optional[Moment] = None
    every_hours: Optional[Hours] = None
    departures: Annotated[List[Moment], Field(max_length=4096)] = Field(default_factory=list)

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "interval" and (self.anchor is None or self.every_hours is None):
            raise ValueError("Для интервального расписания нужны anchor и every_hours")
        if self.kind == "interval" and self.every_hours < 1/3600:
            raise ValueError("Техническая точность интервала расписания — не меньше секунды")
        if self.kind == "explicit" and not self.departures:
            raise ValueError("Пустое расписание отправлений")
        if len(set(self.departures)) != len(self.departures):
            raise ValueError("Дубли отправлений в расписании")
        self.departures.sort()
        return self


class ServiceCharge(InputModel):
    id: Text
    at: Literal["departure", "arrival"]
    quote: Quote

    @model_validator(mode="after")
    def handling_only(self):
        if self.quote.component != "handling":
            raise ValueError("Дополнительная плата услуги — отдельно раскрытая обработка")
        return self


class Service(InputModel):
    id: Text
    from_hub: Text
    to_hub: Text
    mode: Mode
    cost_scope: Literal["complete_scenario", "published_component"] = "complete_scenario"
    missing_cost_components: List[Text] = Field(default_factory=list)
    connection_status: Literal["scenario", "published_service", "observation_based_scenario"] = "scenario"
    reference_metadata: dict = Field(default_factory=dict)
    terminal_handling_included: StrictBool = False
    available: Optional[StrictBool] = None
    cargo_types: Annotated[List[Text], Field(min_length=1)]
    cargo_profiles: List[Text] = Field(default_factory=list)
    packagings: Annotated[List[Text], Field(min_length=1)]
    hazardous_allowed: Optional[StrictBool] = None
    temperature_c: Optional[Tuple[float, float]] = None
    windows: Annotated[List[Window], Field(min_length=1, max_length=64)]
    timetable: Timetable
    cutoff_hours: NonnegativeHours = 0
    travel_hours: NonnegativeHours
    distance_km: Optional[Nonnegative] = None
    min_batch_tonnes: Nonnegative = 0
    require_volume: StrictBool = False
    allowed_phases: List[Literal["solid", "liquid", "gas"]] = Field(default_factory=list)
    storage_quotes: Dict[Text, Quote] = Field(default_factory=dict)
    unit_tonnes: Optional[Positive] = None
    unit_m3: Optional[Positive] = None
    gross_limit_tonnes: Optional[Positive] = None
    tare_tonnes: Optional[Nonnegative] = None
    rail_gauge_mm: Annotated[Optional[StrictInt], Field(gt=0, le=3000)] = None
    available_units: Optional[Count] = None
    booking: Literal["dedicated", "shared", "flow"]
    capacity_scope: Literal["departure", "fleet"] = "departure"
    resource_id: Text
    turnaround_hours: Optional[NonnegativeHours] = None
    quote: Optional[Quote] = None
    additional_charges: Annotated[List[ServiceCharge], Field(max_length=8)] = Field(default_factory=list)
    max_piece_tonnes: Optional[Positive] = None
    max_piece_dimensions_m: Optional[Tuple[Positive, Positive, Positive]] = None
    allow_rotation: StrictBool = False
    vessel_draft_m: Optional[Positive] = None
    max_draft_m: Optional[Positive] = None
    vessel_air_draft_m: Optional[Positive] = None
    max_air_draft_m: Optional[Positive] = None
    ice_class_required: Optional[StrictBool] = None
    ice_class_confirmed: Optional[StrictBool] = None
    commodity_ids: Annotated[List[Text], Field(max_length=1)] = Field(default_factory=list)
    flow_tonnes_per_hour: Optional[Positive] = None
    evidence: Evidence

    @model_validator(mode="after")
    def topology_and_booking(self):
        if len({c.id for c in self.additional_charges}) != len(self.additional_charges):
            raise ValueError("Повтор ID дополнительной платы услуги")
        for hub_id, quote in self.storage_quotes.items():
            if hub_id not in (self.from_hub, self.to_hub) or quote.component != "storage" or quote.basis != "per_unit_day" or quote.minimum_rub != 0 or quote.fixed_rub != 0:
                raise ValueError("Хранение контейнера требует тарифа за контейнеро-сутки на одном из концов услуги")
        if self.from_hub == self.to_hub:
            raise ValueError("Перевозочная услуга должна соединять разные хабы")
        if (self.mode == "pipeline") != (self.booking == "flow"):
            raise ValueError("Трубопровод использует flow; остальные виды — dedicated/shared")
        if self.capacity_scope == "fleet" and self.turnaround_hours is None:
            raise ValueError("Для общего парка нужен явный turnaround_hours с возвратом/подготовкой")
        if self.timetable.kind == "on_demand" and self.booking == "shared":
            raise ValueError("Для сборной услуги нужны отправления с явной общей вместимостью")
        if self.capacity_scope == "fleet" and self.booking == "shared":
            raise ValueError("Общий парк для сборной загрузки требует отдельной модели; используйте capacity_scope=departure")
        if self.timetable.kind == "on_demand" and self.booking == "dedicated" and self.capacity_scope != "fleet":
            raise ValueError("Для выделенного транспорта по готовности нужен ограниченный парк с turnaround_hours")
        return self


class Transfer(InputModel):
    id: Text
    hub_id: Text
    from_mode: Mode
    to_mode: Mode
    from_service_id: Optional[Text] = None
    to_service_id: Optional[Text] = None
    cargo_types: Annotated[List[Text], Field(min_length=1)]
    cargo_profiles: List[Text] = Field(default_factory=list)
    packagings: Annotated[List[Text], Field(min_length=1)]
    hazardous_allowed: Optional[StrictBool] = None
    temperature_c: Optional[Tuple[float, float]] = None
    operation: Operation
    evidence: Evidence


class NetworkCatalog(InputModel):
    id: Text
    coverage: str
    sources: Annotated[List[dict], Field(min_length=1)]
    cargo_types: Dict[str, CargoType]
    compatibility: Dict[str, StrictBool]
    hubs: Annotated[List[Hub], Field(min_length=1, max_length=20000)]
    services: Annotated[List[Service], Field(max_length=100000)]
    transfers: Annotated[List[Transfer], Field(max_length=100000)]

    @model_validator(mode="after")
    def references(self):
        hubs = {hub.id: hub for hub in self.hubs}
        services = {service.id: service for service in self.services}
        if len(hubs) != len(self.hubs) or len(services) != len(self.services) or len({t.id for t in self.transfers}) != len(self.transfers):
            raise ValueError("ID объектов сети должны быть уникальными внутри типа")
        sources = {source.get("id") for source in self.sources}
        if None in sources or len(sources) != len(self.sources):
            raise ValueError("Источникам нужны уникальные id")
        for key in self.compatibility:
            pair = key.split("|")
            if len(pair) != 2 or pair != sorted(set(pair)) or any(item not in self.cargo_types for item in pair):
                raise ValueError("Совместимость задаётся упорядоченной парой существующих типов грузов")
        for obj in [*self.hubs, *self.services, *self.transfers]:
            if any(item not in self.cargo_types for item in obj.cargo_types):
                raise ValueError("Неизвестный тип груза в объекте сети")
        for service in self.services:
            if any(item not in self.cargo_types for item in service.commodity_ids):
                raise ValueError("Неизвестный продукт трубопровода")
            if service.from_hub not in hubs or service.to_hub not in hubs:
                raise ValueError("Услуга ссылается на неизвестный хаб")
            if service.mode not in hubs[service.from_hub].modes or service.mode not in hubs[service.to_hub].modes:
                raise ValueError("Режим услуги не поддерживается в её хабе")
        for transfer in self.transfers:
            if transfer.hub_id not in hubs or transfer.from_mode not in hubs[transfer.hub_id].modes or transfer.to_mode not in hubs[transfer.hub_id].modes:
                raise ValueError("Переход ссылается на неизвестный хаб/режим")
            for service_id, side, mode in ((transfer.from_service_id, "to_hub", transfer.from_mode), (transfer.to_service_id, "from_hub", transfer.to_mode)):
                if service_id and (service_id not in services or getattr(services[service_id], side) != transfer.hub_id or services[service_id].mode != mode):
                    raise ValueError("Переход ссылается на несогласованную услугу")
        def inspect(value):
            if isinstance(value, Evidence):
                if any(source_id not in sources for source_id in value.source_ids):
                    raise ValueError("Неизвестный источник в provenance")
            elif isinstance(value, InputModel):
                for field in type(value).model_fields:
                    if field in ("temperature_c", "required_temperature_c"):
                        interval = getattr(value, field)
                        if interval is not None and interval[0] > interval[1]:
                            raise ValueError("Некорректный температурный диапазон")
                    inspect(getattr(value, field))
            elif isinstance(value, (list, tuple)):
                for item in value:
                    inspect(item)
            elif isinstance(value, dict):
                for item in value.values():
                    inspect(item)
        inspect(self.cargo_types)
        inspect(self.hubs)
        inspect(self.services)
        inspect(self.transfers)
        # Один resource_id не может иметь разные ёмкости у разных объектов.
        resources = {}
        def register(key, capacity):
            if capacity is not None and key in resources and resources[key] != capacity:
                raise ValueError("Противоречивые ёмкости общего ресурса: " + key)
            if capacity is not None:
                resources[key] = capacity
        for hub in self.hubs:
            for operation in (hub.entry, hub.exit):
                if operation:
                    register("handling:" + operation.resource_id, operation.lanes)
        for transfer in self.transfers:
            register("handling:" + transfer.operation.resource_id, transfer.operation.lanes)
        for service in self.services:
            register("units:" + service.resource_id, service.available_units)
            register("unit-tonnes:" + service.resource_id, service.unit_tonnes)
            register("unit-m3:" + service.resource_id, service.unit_m3)
            register("service-resource:" + service.resource_id, (service.booking, service.capacity_scope, service.gross_limit_tonnes, service.tare_tonnes, service.flow_tonnes_per_hour, service.turnaround_hours))
        return self


class CargoProperties(InputModel):
    phase: Literal["solid", "liquid", "gas"]
    hazardous: StrictBool
    handling_profile: Text
    temperature_control_required: StrictBool
    required_temperature_c: Optional[Tuple[float, float]] = None

    @model_validator(mode="after")
    def consistent_temperature(self):
        if self.required_temperature_c is not None:
            if not self.temperature_control_required or self.required_temperature_c[0] > self.required_temperature_c[1]:
                raise ValueError("Уточните требования к температуре")
        return self


class NetworkCargo(InputModel):
    id: Text
    name: Optional[Text] = None
    cargo_type: Optional[Text] = None
    properties: Optional[CargoProperties] = None
    weight_tonnes: Positive
    volume_m3: Optional[Positive] = None
    packaging: Optional[Text] = None
    splittable: Optional[StrictBool] = None
    pickup_hub_id: Optional[Text] = None
    delivery_hub_id: Optional[Text] = None
    ready_offset_hours: NonnegativeHours = 0
    max_delivery_hours: Optional[Hours] = None
    shelf_life_hours: Optional[Hours] = None
    temperature_c: Optional[Tuple[float, float]] = None
    max_piece_tonnes: Optional[Positive] = None
    max_piece_dimensions_m: Optional[Tuple[Positive, Positive, Positive]] = None

    @model_validator(mode="after")
    def cargo_consistency(self):
        if self.name is None and self.cargo_type is None:
            raise ValueError("Укажите название груза")
        if self.max_piece_tonnes is not None and self.max_piece_tonnes > self.weight_tonnes:
            raise ValueError("Масса отдельного места больше массы позиции")
        if self.temperature_c and self.temperature_c[0] > self.temperature_c[1]:
            raise ValueError("Температурный диапазон задан в обратном порядке")
        return self


class NetworkRequest(InputModel):
    # Пример не связан с секретами и доступен фронту в Swagger.
    model_config = dict(InputModel.model_config, json_schema_extra={"example": {
        "engine":"network", "origin_hub_id":"origin", "destination_hub_id":"destination",
        "cargoes":[{"id":"flour-1","name":"Мука","weight_tonnes":10,"volume_m3":20,"packaging":"bags","splittable":True,"max_piece_tonnes":0.025,"max_piece_dimensions_m":[0.5,0.4,0.2]}],
        "schedule":{"mode":"once","ready_at":"2026-11-09T08:00:00+03:00"},
        "max_delivery_hours":240,"capacity_basis":"mass_and_volume","cost_basis":"including_vat",
        "data_policy":"allow_assumptions","required_modes":["road","sea","air"]}})
    engine: Literal["network"]
    network_id: Text = "synthetic-network-v1"
    origin_hub_id: Text
    destination_hub_id: Text
    cargoes: Annotated[List[NetworkCargo], Field(min_length=1, max_length=20)]
    schedule: Schedule
    max_delivery_hours: Hours
    capacity_basis: Literal["mass_only", "mass_and_volume"]
    cost_basis: Literal["including_vat", "excluding_vat"]
    data_policy: Literal["verified_only", "allow_assumptions"]
    service_ids: Annotated[Optional[List[Text]], Field(min_length=1, max_length=12)] = None
    required_modes: Annotated[Optional[List[Mode]], Field(min_length=1, max_length=12)] = None
    via_hub_ids: Annotated[List[Text], Field(max_length=10)] = Field(default_factory=list)
    stop_dwell_hours: Dict[Text, NonnegativeHours] = Field(default_factory=dict)
    group_sizes: Annotated[List[Annotated[StrictInt, Field(ge=1, le=64)]], Field(min_length=1, max_length=4)] = Field(default_factory=lambda: [1])
    max_legs: Annotated[StrictInt, Field(ge=1, le=12)] = 6
    max_paths: Annotated[StrictInt, Field(ge=1, le=32)] = 16

    @model_validator(mode="after")
    def coherent_request(self):
        if any(key not in self.via_hub_ids for key in self.stop_dwell_hours):
            raise ValueError("Время остановки задаётся только для обязательных промежуточных хабов")
        if self.origin_hub_id == self.destination_hub_id:
            raise ValueError("Нужны разные хабы отправления и получения")
        if len({item.id for item in self.cargoes}) != len(self.cargoes):
            raise ValueError("Повтор id груза")
        if len(set(self.group_sizes)) != len(self.group_sizes) or len(set(self.via_hub_ids)) != len(self.via_hub_ids):
            raise ValueError("Повтор группы консолидации или промежуточного хаба")
        for item in self.cargoes:
            pickup = item.pickup_hub_id or self.origin_hub_id
            delivery = item.delivery_hub_id or self.destination_hub_id
            if pickup == delivery:
                raise ValueError("Погрузка и выгрузка позиции должны быть в разных хабах")
            if self.capacity_basis == "mass_and_volume" and item.volume_m3 is None:
                raise ValueError("Для mass_and_volume нужен объём каждой позиции")
        if not any((item.pickup_hub_id or self.origin_hub_id) == self.origin_hub_id for item in self.cargoes):
            raise ValueError("Нужна хотя бы одна погрузка в начале маршрута")
        if not any((item.delivery_hub_id or self.destination_hub_id) == self.destination_hub_id for item in self.cargoes):
            raise ValueError("Нужна хотя бы одна выгрузка в конце маршрута")
        return self


class NetworkResponse(InputModel):
    schema_version: Literal["0.3"]
    engine: Literal["network"]
    calculation_mode: Literal["scenario"]
    network_id: str
    network_fingerprint: str
    coverage: str
    request: dict
    shipments: int
    total_volume_tonnes: float
    categories: List[dict]
    candidates: List[dict]
    recommendation: Optional[str]
    search: dict
    assumptions: List[str]
    warnings: List[str]
    sources: List[dict]
    input_status: Literal["ready", "needs_clarification"] = "ready"
    cargo_resolution: List[dict] = Field(default_factory=list)
    clarifications: List[dict] = Field(default_factory=list)
