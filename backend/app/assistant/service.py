from typing import Callable, Optional, Union

from pydantic import ValidationError

from backend.calculation_models import CalculationRequest, CalculationResponse
from backend.network.chukotavia import ChukotaviaRequest
from backend.network.models import NetworkRequest
from backend.network.pipeline import PipelineRequest
from backend.network.russia import RussiaRequest

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
    "max_delivery_hours": "максимальный срок доставки",
    "splittable": "можно ли разделить партию",
    "capacity_basis": "режим проверки вместимости",
    "origin_hub_id": "хаб отправления",
    "destination_hub_id": "хаб назначения",
    "cargoes": "груз и вес каждой позиции",
    "name": "название груза",
    "packaging": "упаковка",
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
}
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
        missing.append(("schedule.mode", "доставка разовая или регулярная"))
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


def _validation_question(error):
    fields = {}
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        if item["type"] == "extra_forbidden":
            raise InvalidAssistantTurn from error
        parts = [str(part) for part in item["loc"] if part not in ("once", "regular", "explicit")]
        field = ".".join(parts) or "parameters"
        if not parts:
            label = item["msg"].removeprefix("Value error, ")
        else:
            label = FIELD_LABELS.get(parts[-1], field)
            if item["type"] == "timezone_aware":
                label += " с часовым поясом"
        fields[field] = label
    labels = ", ".join(dict.fromkeys(fields.values()))
    return ToolQuestion(list(fields), f"Уточните параметры — {labels}.")


def _calculation_questions(result):
    fields = []
    messages = []
    positions = {cargo["id"]: index for index, cargo in enumerate(result.request.get("cargoes", []))}
    for question in result.clarifications:
        messages.append(question["message"])
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


def _handle_tool(turn, request, calculate, aviation, forecast):
    name = turn.tool
    arguments = turn.arguments
    context = {"tool": name, "draft": request.draft}
    if "engine" in arguments:
        raise InvalidAssistantTurn
    try:
        if name in CALCULATIONS:
            engine, _, response_model, _ = CALCULATIONS[name]
            context["engine"] = engine
            payload = calculation_request(name, arguments)
        elif name in REFERENCES:
            model, handler, dependency, _ = REFERENCES[name]
            query = model.model_validate(arguments)
        else:
            raise InvalidAssistantTurn
    except ValidationError as exc:
        question = _validation_question(exc)
    except ToolQuestion as exc:
        question = exc
    else:
        question = None

    if question is not None:
        return ClarificationReply(
            kind="clarification", reply=str(question), pending=turn, missing_fields=question.fields, **context,
        )
    if name in REFERENCES:
        dependencies = {"aviation": aviation, "forecast": forecast}
        info = handler(query, dependencies[dependency]) if dependency else handler(query)
        return InformationReply(kind="information", **info.model_dump(), **context)

    result = response_model.model_validate(calculate(payload))
    sources = calculation_sources(result, engine)
    if getattr(result, "input_status", None) == "needs_clarification":
        fields, question = _calculation_questions(result)
        return ClarificationReply(
            kind="clarification", reply=question, pending=turn, missing_fields=fields,
            calculation=result, sources=sources, **context,
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
        return _handle_tool(turn, request, calculate, aviation, forecast)
    if isinstance(turn, ClarificationTurn):
        return ClarificationReply(
            kind="clarification", reply=turn.reply, draft=turn.draft, missing_fields=turn.missing_fields,
            engine="pilot", tool="compare_delivery",
        )
    if not isinstance(turn, DeliveryTurn):
        raise InvalidAssistantTurn

    draft: DeliveryDraft = turn.draft
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
