from typing import Callable, Optional, Union

from pydantic import ValidationError

from backend.calculation_models import CalculationRequest, CalculationResponse

from .schemas import (
    AssistantTurn,
    CalculationReply,
    ChatRequest,
    ChatResponse,
    ClarificationReply,
    ClarificationTurn,
    DeliveryDraft,
    DeliveryTurn,
    InformationReply,
    InformationTurn,
)


TurnPlanner = Callable[[ChatRequest], AssistantTurn]
Calculate = Callable[[CalculationRequest], Union[dict, CalculationResponse]]

FIELD_LABELS = {
    "origin": "город отправления",
    "destination": "город назначения",
    "cargo_type": "вид груза",
    "weight_tonnes": "вес одной партии",
    "max_delivery_hours": "максимальный срок доставки",
    "splittable": "можно ли разделить партию",
    "capacity_basis": "режим проверки вместимости",
}

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
    missing = [(field, label) for field, label in FIELD_LABELS.items() if getattr(draft, field) is None]
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
    lines.extend(result.warnings)
    return "\n\n".join(lines)


def handle_chat(
    request: ChatRequest,
    *,
    plan_turn: Optional[TurnPlanner],
    dataset_id: str,
    calculate: Calculate,
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
            kind="information", reply=turn.reply, draft=request.draft, sources=turn.sources
        )
    if isinstance(turn, ClarificationTurn):
        return ClarificationReply(
            kind="clarification", reply=turn.reply, draft=turn.draft, missing_fields=turn.missing_fields
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
        )

    try:
        payload = _calculation_request(draft, dataset_id)
    except ValidationError as exc:
        raise InvalidDeliveryDraft(exc) from exc
    result = CalculationResponse.model_validate(calculate(payload))
    return CalculationReply(kind="calculation", reply=_summary(result, draft), draft=draft, calculation=result)
