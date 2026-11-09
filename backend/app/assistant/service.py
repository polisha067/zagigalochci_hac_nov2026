from datetime import datetime, timedelta
import re
from typing import Callable, Optional, Union

from pydantic import ValidationError

from backend.calculation_models import CalculationRequest, CalculationResponse
from backend.network.cargo import normalized_name
from backend.network.chukotavia import ChukotaviaRequest
from backend.network.models import NetworkRequest
from backend.network.pipeline import PipelineRequest
from backend.network.russia import END as RUSSIA_END, START as RUSSIA_START, RussiaRequest, load_russia

from .references import number
from .results import calculation_sources, engine_summary
from .schemas import (
    AssistantTurn,
    CalculationReply,
    ChatRequest,
    ChatResponse,
    ClarificationReply,
    ClarificationTurn,
    DeliveryDraft,
    DeliveryTurn,
    EngineResult,
    InformationReply,
    InformationTurn,
    ToolState,
)
from .tools import CALCULATIONS, REFERENCES, ToolQuestion, calculation_request


TurnPlanner = Callable[[ChatRequest], AssistantTurn]
Calculate = Callable[[Union[CalculationRequest, NetworkRequest, RussiaRequest, ChukotaviaRequest, PipelineRequest]],
                     Union[dict, EngineResult]]

FIELD_LABELS = {
    "origin": "город отправления",
    "destination": "город назначения",
    "cargo_type": "вид груза",
    "weight_tonnes": "вес одной партии",
    "max_delivery_hours": "за сколько времени нужно доставить груз, например за пять дней",
    "splittable": "можно ли разделить партию",
    "capacity_basis": "известен ли объём груза или достаточно оценки по весу",
    "origin_hub_id": "терминал или станция отправления",
    "destination_hub_id": "терминал или станция назначения",
    "cargoes": "что везёте и сколько весит каждый вид груза",
    "name": "название груза",
    "packaging": "как упакован груз, например в мешки или коробки",
    "net_weight_tonnes": "масса нефти нетто в тоннах",
    "shipment_at": "дата отправки нефти",
    "flight_date": "местная календарная дата вылета",
    "year": "год",
    "transport": "вид транспорта",
    "query": "название порта или бассейна",
    "volume_m3": "объём груза в м³",
    "cost_basis": "стоимость с НДС или без НДС",
    "data_policy": "допустимы ли сценарные данные",
    "schedule": "график отправок и время готовности",
    "ready_at": "дата и время готовности груза",
    "start_at": "начало периода отправок",
    "end_at": "конец периода отправок",
    "frequency": "частота отправок",
    "mode": "разовая или регулярная отправка",
    "allowed_modes": "какие виды транспорта можно использовать",
    "required_modes": "какие виды транспорта обязательно включить",
    "stops": "промежуточные пункты и что нужно сделать в каждом",
    "settlement": "название населённого пункта",
    "action": "что нужно сделать в промежуточном пункте",
    "mass_basis": "масса груза без упаковки или вместе с упаковкой",
    "max_piece_tonnes": "вес самого тяжёлого места груза",
    "max_piece_weight_kg": "вес самого тяжёлого места груза в кг",
    "max_piece_dimensions_m": "длина, ширина и высота самого крупного места груза",
    "temperature_c": "допустимая температура перевозки",
    "required_temperature_c": "допустимая температура перевозки",
    "temperature_control_required": "нужно ли поддерживать температуру",
    "hazardous": "относится ли груз к опасным",
    "phase": "груз твёрдый, жидкий или газообразный",
    "handling_profile": "особые условия обращения с грузом",
    "properties": "свойства груза и особые условия перевозки",
    "route_id": "пункты отправления и назначения",
    "network_id": "какую сеть перевозок использовать",
}
SCHEDULE_QUESTION = ("когда груз будет готов к отправке. Если отправок несколько, "
                     "укажите даты или период и частоту")
SHIPMENT_YEAR = re.compile(
    r"\b(?:в|на)\s+(20\d{2})(?:\s+год(?:у|е)?)?\b(?!\s*(?:кг|т|тонн\w*|час\w*|руб\w*)\b)",
    re.IGNORECASE,
)
RUSSIA_FIRST_YEAR = int(RUSSIA_START[:4])
RUSSIA_LAST_YEAR = (datetime.fromisoformat(RUSSIA_END) - timedelta(microseconds=1)).year
RUSSIA_YEARS = (str(RUSSIA_FIRST_YEAR) if RUSSIA_FIRST_YEAR == RUSSIA_LAST_YEAR
                else f"{RUSSIA_FIRST_YEAR}–{RUSSIA_LAST_YEAR}")
PILOT_FIELDS = ("origin", "destination", "cargo_type", "weight_tonnes",
                "max_delivery_hours", "splittable", "capacity_basis")

SCHEME_LABELS = {
    "spot_road": "разовые автозаказы",
    "contract_road": "регулярные авторейсы по договору",
    "road_rail_road": "авто → ж/д → авто",
}


class AssistantUnavailable(Exception):
    """Обработчик сообщений отсутствует или временно недоступен."""


class InvalidAssistantTurn(Exception):
    """Обработчик сообщения вернул неподдерживаемое действие."""


class InvalidDeliveryDraft(Exception):
    """Полный черновик не прошёл проверки расчётного запроса."""

    def __init__(self, validation_error: ValidationError):
        self.fields = validation_error.errors(include_url=False, include_context=False, include_input=False)


def _missing_fields(draft):
    missing = [(field, FIELD_LABELS[field]) for field in PILOT_FIELDS if getattr(draft, field) is None]
    schedule = draft.schedule
    if schedule is None or schedule.mode is None:
        missing.append(("schedule.mode", SCHEDULE_QUESTION))
    elif schedule.mode == "once":
        if schedule.ready_at is None:
            missing.append(("schedule.ready_at", "дата и время готовности груза"))
    else:
        for field, label in (
            ("start_at", "начало периода"),
            ("end_at", "конец периода"),
            ("frequency", "частота отправок"),
        ):
            if getattr(schedule, field) is None:
                missing.append((f"schedule.{field}", label))
    if draft.capacity_basis == "mass_and_volume" and draft.volume_m3 is None:
        missing.append(("volume_m3", "объём одной партии в м³"))
    return missing


def _calculation_request(draft, dataset_id):
    return CalculationRequest.model_validate({
        "dataset_id": dataset_id,
        "route": [{"city": draft.origin}, {"city": draft.destination}],
        "cargoes": [{
            "id": f"{draft.cargo_type[:98]}-1",
            "cargo_type": draft.cargo_type,
            "weight_tonnes": draft.weight_tonnes,
            "volume_m3": draft.volume_m3,
            "splittable": draft.splittable,
            "load_at": 0,
            "unload_at": 1,
        }],
        "schedule": draft.schedule.model_dump(exclude_none=True),
        "max_delivery_hours": draft.max_delivery_hours,
        "capacity_basis": draft.capacity_basis,
    })


def _summary(result, draft):
    lines = [f"Сценарный расчёт. Плановый объём — {result.total_volume_tonnes:g} т. Отправок — {result.shipments}."]
    chosen = next((scheme for scheme in result.schemes if scheme.scheme == result.recommendation), None)
    if chosen is None:
        lines.append("Среди рассчитанных схем подходящий вариант не определён.")
    else:
        name = SCHEME_LABELS[chosen.scheme]
        cost = f"{chosen.total_cost_rub:,.2f}".replace(",", " ").replace(".", ",").removesuffix(",00") if chosen.total_cost_rub is not None else None
        duration = f"{chosen.max_delivery_hours:g} ч" if chosen.max_delivery_hours is not None else "неизвестен"
        lines.append(f"Рекомендация — {name}. Стоимость — {cost + ' ₽' if cost else 'неизвестна'}. Максимальный срок — {duration}.")
    for scheme in result.schemes:
        if scheme.status == "feasible":
            if scheme is not chosen:
                lines.append(f"{SCHEME_LABELS[scheme.scheme].capitalize()} — {number(scheme.total_cost_rub)} ₽, "
                             f"максимальный срок {number(scheme.max_delivery_hours)} ч.")
            continue
        cost_note = " Стоимость неизвестна." if scheme.total_cost_rub is None else ""
        missing = f" Недостающие данные — {', '.join(scheme.missing_fields)}." if scheme.missing_fields else ""
        lines.append(f"{SCHEME_LABELS[scheme.scheme].capitalize()} — {scheme.reason.rstrip('.')}.{cost_note}{missing}")

    dates = (draft.schedule.ready_at, draft.schedule.start_at, draft.schedule.end_at)
    offsets = sorted({date.strftime("%z") for date in dates if date is not None})
    if offsets:
        zones = ", ".join(f"UTC{offset[:3]}:{offset[3:5]}" for offset in offsets)
        lines.append(f"Смещение времени в расписании — {zones}.")
    if result.data_status == "synthetic":
        lines.append("Расчёт выполнен на синтетических данных.")
    lines.extend(result.assumptions)
    lines.extend(result.warnings)
    return "\n\n".join(lines)


def _field_label(field):
    parts = [part for part in field.split(".") if not part.isdecimal()]
    if parts[:2] == ["schedule", "frequency"]:
        return "как часто отправлять груз, например два раза в неделю"
    if parts == ["schedule"] or parts == ["schedule", "mode"]:
        return SCHEDULE_QUESTION
    return FIELD_LABELS.get(parts[-1] if parts else "", "условия перевозки, которые нужно уточнить")


def _validation_questions(error):
    fields = {}
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        if item["type"] == "extra_forbidden":
            raise InvalidAssistantTurn from error
        parts = [str(part) for part in item["loc"] if part not in ("once", "regular", "explicit")]
        field = ".".join(parts) or "parameters"
        label = _field_label(field)
        if item["type"] == "timezone_aware":
            label += " и по какому местному времени"
        fields[field] = label
    return fields


def _cargo_questions(engine, arguments, networks):
    cargoes = arguments.get("cargoes")
    if engine not in ("russia", "network") or not isinstance(cargoes, list):
        return {}
    if engine == "russia":
        catalogue = load_russia().cargo_types
    else:
        network_id = arguments.get("network_id", "synthetic-network-v1")
        graph = networks.get(network_id) if isinstance(network_id, str) else None
        catalogue = {key: cargo.model_dump() for key, cargo in graph.catalog.cargo_types.items()} if graph else {}
    known_names = {
        normalized_name(name) for key, cargo in catalogue.items()
        for name in (key, cargo["name"], *cargo["aliases"])
    }
    fields = {}
    for index, cargo in enumerate(cargoes):
        if not isinstance(cargo, dict):
            continue
        prefix = f"cargoes.{index}"
        missing = [field for field in ("packaging", "splittable") if cargo.get(field) is None]
        if not (cargo.get("name") or cargo.get("cargo_type")):
            missing.append("name")
        if arguments.get("capacity_basis", "mass_and_volume") == "mass_and_volume" and cargo.get("volume_m3") is None:
            missing.append("volume_m3")
        name = cargo.get("name") or cargo.get("cargo_type")
        properties = cargo.get("properties")
        if isinstance(name, str) and known_names and normalized_name(name) not in known_names and properties is None:
            missing.extend(f"properties.{field}" for field in ("phase", "hazardous", "handling_profile", "temperature_control_required"))
        if isinstance(properties, dict) and properties.get("temperature_control_required") is True and properties.get("required_temperature_c") is None:
            missing.append("properties.required_temperature_c")
        for field in missing:
            path = f"{prefix}.{field}"
            label = _field_label(path)
            fields[path] = f"груз №{index + 1} — {label}" if len(cargoes) > 1 else label
    return fields


def _outside_russia_year(message, arguments):
    years = [int(value) for value in SHIPMENT_YEAR.findall(message)]
    schedule = arguments.get("schedule")
    if isinstance(schedule, dict):
        dates = schedule.get("ready_at") or schedule.get("start_at")
        if isinstance(dates, str):
            dates = [dates]
        if isinstance(dates, list):
            for value in dates:
                if isinstance(value, str) and re.match(r"20\d{2}-\d{2}-\d{2}", value):
                    years.append(int(value[:4]))
    return next((year for year in years if not RUSSIA_FIRST_YEAR <= year <= RUSSIA_LAST_YEAR), None)


def _clarification_reply(turn, fields, context, *, introduction="", message=None, **details):
    if message is None:
        lines = "\n".join(f"• {label}" for label in dict.fromkeys(fields.values()))
        message = f"Уточните, пожалуйста, всё, что знаете:\n{lines}"
    return ClarificationReply(
        kind="clarification", reply=introduction + message,
        pending=turn.model_copy(update={"missing_fields": list(fields)}),
        missing_fields=list(fields), **context, **details,
    )


def _calculation_questions(result):
    fields = []
    messages = []
    positions = {cargo["id"]: index for index, cargo in enumerate(result.request.get("cargoes", []))}
    for question in result.clarifications:
        message = question["message"]
        if question["code"] == "pipeline_net_mass_required":
            message = "Укажите массу нефти без балласта."
        elif question["code"] == "unconfirmed_cargo_identity":
            message = "Уточните название груза и особые условия его перевозки."
        messages.append(message)
        if "field" in question:
            field = question["field"]
            if field.startswith("locations."):
                index = int(field.rsplit(".", 1)[1])
                field = ("origin" if index == 0 else "destination" if index == len(result.settlements) - 1
                         else f"stops.{index - 1}.settlement")
            fields.append(field)
        else:
            prefix = f"cargoes.{positions[question['cargo_id']]}"
            fields.extend(f"{prefix}.{field}" for field in question["fields"])
    unique_messages = list(dict.fromkeys(messages))
    return list(dict.fromkeys(fields)), "\n".join(unique_messages[:20])


def _handle_tool(turn, request, calculate, aviation, forecast, networks):
    name = turn.tool
    arguments = turn.arguments
    context = {"tool": name, "draft": request.draft}
    if "engine" in arguments:
        raise InvalidAssistantTurn
    fields = {}
    introduction = ""
    engine = None
    try:
        if name in CALCULATIONS:
            engine, _, response_model, _ = CALCULATIONS[name]
            context["engine"] = engine
            if engine == "russia" and (year := _outside_russia_year(request.message, arguments)):
                arguments = {key: value for key, value in arguments.items() if key != "schedule"}
                turn = turn.model_copy(update={"arguments": arguments})
                introduction = (f"Для {year} года в проекте нет тарифов и расписаний для расчёта этого маршрута. "
                                f"Могу сравнить варианты на дату в {RUSSIA_YEARS} году.\n\n")
            payload = calculation_request(name, arguments)
        elif name in REFERENCES:
            model, handler, dependency, _ = REFERENCES[name]
            query = model.model_validate(arguments)
        else:
            raise InvalidAssistantTurn
    except ValidationError as exc:
        fields.update(_validation_questions(exc))
    except ToolQuestion as exc:
        return _clarification_reply(turn, dict.fromkeys(exc.fields), context, message=str(exc))

    fields.update(_cargo_questions(engine, arguments, networks))
    if engine == "pipeline" and not arguments.get("route_id"):
        for field in ("origin", "destination"):
            if not arguments.get(field):
                fields[field] = _field_label(field)
    if fields:
        return _clarification_reply(turn, fields, context, introduction=introduction)
    if name in REFERENCES:
        dependencies = {"aviation": aviation, "forecast": forecast}
        info = handler(query, dependencies[dependency]) if dependency else handler(query)
        return InformationReply(kind="information", **info.model_dump(), **context)

    result = response_model.model_validate(calculate(payload))
    sources = calculation_sources(result, engine)
    if getattr(result, "input_status", None) == "needs_clarification":
        fields, question = _calculation_questions(result)
        return _clarification_reply(
            turn, dict.fromkeys(fields), context, message=question, calculation=result, sources=sources,
        )
    return CalculationReply(
        kind="calculation", reply=engine_summary(result), calculation=result, sources=sources, **context,
    )


def handle_chat(
    request: ChatRequest,
    *,
    plan_turn: Optional[TurnPlanner],
    dataset_id: str,
    calculate: Calculate,
    aviation=None,
    forecast=None,
    networks=None,
) -> ChatResponse:
    if plan_turn is None:
        raise AssistantUnavailable
    try:
        turn = plan_turn(request)
    except TimeoutError as exc:
        raise AssistantUnavailable from exc
    except ValidationError as exc:
        raise InvalidAssistantTurn from exc
    if isinstance(turn, InformationTurn):
        return InformationReply(
            kind="information", **turn.model_dump(), draft=request.draft, pending=request.pending,
        )
    if isinstance(turn, ToolState):
        return _handle_tool(turn, request, calculate, aviation, forecast, networks or {})
    if isinstance(turn, ClarificationTurn):
        return ClarificationReply(
            kind="clarification", reply=turn.reply, draft=turn.draft, missing_fields=turn.missing_fields,
            engine="pilot", tool="compare_delivery",
        )
    if not isinstance(turn, DeliveryTurn):
        raise InvalidAssistantTurn

    draft: DeliveryDraft = turn.draft
    if draft.capacity_basis is None and draft.volume_m3 is not None:
        draft = draft.model_copy(update={"capacity_basis": "mass_and_volume"})
    missing = _missing_fields(draft)
    if missing:
        labels = ", ".join(label for _, label in missing)
        return ClarificationReply(
            kind="clarification",
            reply=f"Для расчёта уточните следующие параметры — {labels}.",
            draft=draft,
            missing_fields=[field for field, _ in missing],
            engine="pilot",
            tool="compare_delivery",
        )

    try:
        payload = _calculation_request(draft, dataset_id)
    except ValidationError as exc:
        raise InvalidDeliveryDraft(exc) from exc
    result = CalculationResponse.model_validate(calculate(payload))
    return CalculationReply(
        kind="calculation", reply=_summary(result, draft), draft=draft, calculation=result,
        engine="pilot", tool="compare_delivery", sources=calculation_sources(result, "pilot"),
    )
