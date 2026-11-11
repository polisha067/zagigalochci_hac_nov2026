"""Allowlisted tools and adaptation to the existing calculation contracts"""

from datetime import date, datetime, time
import re
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import Field

from backend.calculation_models import Text
from backend.network.chukotavia import ChukotaviaRequest, ChukotaviaResponse, LOCAL
from backend.network.models import NetworkRequest, NetworkResponse
from backend.network.pipeline import PipelineRequest, PipelineResponse
from backend.network.russia import RussiaRequest, RussiaResponse

from . import references as refs
from .aviation import AirTariffQuery, answer_air_tariff
from backend.network.delivery_forecast import TOOL as FUTURE_COST_TOOL, ForecastRequest, ForecastResponse
from .regional_forecast import ForecastQuery


class PipelineArguments(PipelineRequest):
    route_id: Optional[Text] = Field(default=None, description="Код маршрута, если он явно указан")
    origin: Optional[Text] = None
    destination: Optional[Text] = None


CALCULATIONS = {
    "calculate_network": ("network", NetworkRequest, NetworkResponse, "Расчёт по хабам и услугам из именованного сетевого каталога. Сюда относится речной сценарий Красноярск ↔ Дудинка."),
    "calculate_russia": ("russia", RussiaRequest, RussiaResponse, "Общий сценарный расчёт по населённым пунктам России с автоматическим выбором хабов и возможными остановками."),
    FUTURE_COST_TOOL: ("russia", ForecastRequest, ForecastResponse, "Оценить цену перевозки заданного груза в будущем году и сравнить схемы. Передай target_year, заданные параметры груза и график только при указанных датах."),
    "calculate_chukotavia": ("chukotavia", ChukotaviaRequest, ChukotaviaResponse, "Расчёт перевозки ЧукотАВИА с грузом, готовностью и предельным сроком. Плановые варианты и частичная цена."),
    "calculate_pipeline": ("pipeline", PipelineArguments, PipelineResponse, "Рассчитать опубликованный компонент перевозки товарной нефти по направлению, массе нетто в тоннах и дате отправки."),
}

# The last argument to a reference handler is supplied only for the two local contexts that have already been loaded by the application
REFERENCES = {
    "lookup_air_freight_tariff": (AirTariffQuery, answer_air_tariff, "aviation", "Найти опубликованную ставку ЧукотАВИА и при наличии веса и объёма оценить только тарифную составляющую."),
    "lookup_chukotavia_schedule": (refs.AirScheduleQuery, refs.air_schedule, "aviation", "Плановые рейсы ЧукотАВИА по направлению и календарной дате вылета в местном времени."),
    "lookup_connectivity": (refs.ConnectivityQuery, refs.connectivity, None, "Исходные наблюдения связности городов: расстояние и время поездок по видам транспорта."),
    "lookup_fesco_voyages": (refs.FescoQuery, refs.fesco, None, "Опубликованные календарные даты отхода и прибытия FESCO из PDF. date_from и date_to включительно."),
    "lookup_fesco_reefer_tariff": (refs.FescoReeferQuery, refs.fesco_reefer_tariff, None, "Опубликованная ставка FESCO на рефконтейнер грузоотправителя между Владивостоком и Петропавловском-Камчатским. Это часть цены, не прогноз и не бронь."),
    "lookup_river_scenario": (refs.RiverQuery, refs.river, None, "Компонентные ставки, условная навигация и вместимость речной схемы Красноярск ↔ Дудинка."),
    "lookup_pipeline_reference": (refs.PipelineReferenceQuery, refs.pipeline_reference, None, "Справка о трубопроводных ставках, операциях, диспетчеризации или справочной длине, без расчёта партии."),
    "lookup_rosmorport": (refs.PortQuery, refs.ports, None, "Справочник Росморпорта по названию или бассейну: навигация, характеристики, сборы, расположение и услуги."),
    "lookup_asop": (refs.AsopQuery, refs.asop, None, "Статистика АСОП по году, периоду, показателю и категории. Например, вывоз из портов по транспорту — port_outbound."),
    "lookup_transport_strategy": (refs.StrategyQuery, refs.strategy, None, "Макросценарии Транспортной стратегии 2021 года по России в целом. Внутренний водный — river."),
    "calculate_corridor_forecast": (refs.CorridorForecastQuery, refs.corridor_forecast, None, "Рассчитать сценарный прогноз объёма для направления между двумя городами в 2026–2100 годах, по годам, кварталам или месяцам."),
    "compare_corridor_hypotheses": (refs.CorridorComparisonQuery, refs.corridor_comparison, None, "Сравнить две гипотезы A1 на опорном направлении и годе: авто по договору и авто → ж/д → авто относительно разовых автозаказов."),
    "rank_regional_forecast": (ForecastQuery, refs.regional_forecast, "forecast", "Рейтинг регионов по прогнозному росту грузоперевозок по году и виду транспорта. Не подменяет региональные данные макросценариями."),
}


class ToolQuestion(ValueError):
    def __init__(self, fields, message):
        self.fields = fields
        super().__init__(message)


def merge_arguments(previous, changes):
    merged = dict(previous)
    for field, value in changes.items():
        old = merged.get(field)
        if isinstance(old, dict) and isinstance(value, dict):
            if field != "schedule" or value.get("mode", old.get("mode")) == old.get("mode"):
                value = merge_arguments(old, value)
        elif field == "cargoes" and isinstance(old, list) and isinstance(value, list) and len(old) == len(value):
            value = [_merge_cargo(original, change) for original, change in zip(old, value)]
        merged[field] = value
    return merged


def _merge_cargo(original, change):
    if not isinstance(original, dict) or not isinstance(change, dict):
        return change
    if any(key in original and key in change and original[key] != change[key]
           for key in ("id", "name", "cargo_type")):
        return change
    return merge_arguments(original, change)


def _air_moment(value):
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?", value):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=LOCAL).isoformat()
        except ValueError:
            pass  # The request model reports the invalid calendar date at the original field.
    return value


def _prepare_arguments(engine, arguments):
    payload = dict(arguments)
    if engine == "network":
        payload.setdefault("capacity_basis", "mass_and_volume")
        payload.setdefault("cost_basis", "including_vat")
        payload.setdefault("data_policy", "allow_assumptions")
    river = engine == "network" and payload.get("network_id") == "river-krasnoyarsk-dudinka-scenario-v1"
    if isinstance(payload.get("cargoes"), list):
        payload["cargoes"] = [
            {"id": f"cargo-{index}", **item} if isinstance(item, dict) else item
            for index, item in enumerate(payload["cargoes"], 1)
        ]
        if engine in ("network", "russia"):
            for cargo in payload["cargoes"]:
                props = cargo.get("properties") if isinstance(cargo, dict) else None
                if (isinstance(props, dict) and props.get("phase") == "solid" and props.get("hazardous") is False
                        and props.get("temperature_control_required") is False and props.get("handling_profile") == "general"):
                    cargo["properties"] = dict(props, handling_profile="dry_general")
    if river:
        schedule = payload.get("schedule")
        if isinstance(schedule, dict) and schedule.get("mode") == "once":
            payload["group_sizes"] = [1]
    if engine == "chukotavia" and isinstance(payload.get("schedule"), dict):
        schedule = dict(payload["schedule"])
        for field in ("ready_at", "start_at", "end_at"):
            value = schedule.get(field)
            schedule[field] = [_air_moment(moment) for moment in value] if isinstance(value, list) else _air_moment(value)
            if field not in payload["schedule"]:
                schedule.pop(field)
        payload["schedule"] = schedule
    if engine == "pipeline":
        value = payload.get("shipment_at")
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            try:
                day = date.fromisoformat(value)
            except ValueError as exc:
                raise ToolQuestion(["shipment_at"], "Уточните календарную дату отправки нефти.") from exc
            payload["shipment_at"] = datetime.combine(day, time.min, ZoneInfo("Europe/Moscow"))
    return payload


def _pipeline_request(query):
    data = refs.snapshot(refs.PIPELINE_PATH)
    points = refs.pipeline_points(data)
    if query.route_id is None and (query.origin is None or query.destination is None):
        fields = [field for field in ("origin", "destination") if getattr(query, field) is None]
        raise ToolQuestion(fields, "Укажите пункты отправления и назначения товарной нефти.")
    routes = [
        row for row in data["routes"] if query.route_id in (None, row["id"])
        and refs.point_matches(query.origin, points[row["origin"]])
        and refs.point_matches(query.destination, points[row["destination"]])
    ]
    if len(routes) != 1:
        raise ToolQuestion(["route_id"], "Уточните пункты отправления и назначения. В снимке нет однозначной опубликованной ставки для указанного направления.")
    return PipelineRequest(route_id=routes[0]["id"], net_weight_tonnes=query.net_weight_tonnes,
                           shipment_at=query.shipment_at)


def calculation_request(tool, arguments):
    engine, model, _, _ = CALCULATIONS[tool]
    payload = model.model_validate(_prepare_arguments(engine, arguments) | {"engine": engine})
    return _pipeline_request(payload) if engine == "pipeline" else payload


def tool_definition(name, model, description, *, partial=False):
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    if partial:
        schema["properties"].pop("engine", None)

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {key: inline(value) for key, value in node.items()
                    if key not in ("title", "examples", "example", "additionalProperties", "default")
                    and (key != "required" or not partial)}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    return {"type": "function", "function": {"name": name, "description": description, "parameters": inline(schema)}}


DOMAIN_TOOLS = [
    *(tool_definition(name, model, description, partial=True)
      for name, (_, model, _, description) in CALCULATIONS.items()),
    *(tool_definition(name, model, description, partial=True)
      for name, (model, _, _, description) in REFERENCES.items()),
]
DOMAIN_TOOL_NAMES = CALCULATIONS.keys() | REFERENCES.keys()
