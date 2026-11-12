from copy import deepcopy
from datetime import datetime
import logging
import re

from fastapi import HTTPException
from pydantic import ValidationError

from backend.network.cargo import normalized_name
from backend.network.delivery_forecast import (
    BASE_YEAR, MOSCOW, PROJECTED_MODES, TECHNICAL_HOURS, TOOL as FORECAST_TOOL, forecast_options,
)
from backend.network.russia import RussiaResponse, load_russia

from .dialog_models import DialogOptions, DialogReply, DialogSnapshot, ScenarioReply
from .dialog_planner import (
    COMPARE_YEARS, HELP, NEW_SHIPMENT, SAME_ROUTE, SHIPMENT_TOOLS, YEAR_ONLY, clean, plan_turn, readiness_moment,
    readiness_parts, resolve_city, stop_anchor, visit_only_stop,
)
from .references import MODE_LABELS, number
from .results import VAT
from .schemas import ClarificationReply, InformationReply, ToolState
from .service import InvalidAssistantTurn, RUSSIA_FIRST_YEAR, RUSSIA_LAST_YEAR, _field_label
from .tools import ToolQuestion, merge_arguments


logger = logging.getLogger("uvicorn.error")
PACKAGING = {"bags": "мешки", "boxes": "коробки", "pallets": "паллеты"}
ROLE_FIELDS = {"pickup_at": "pickup_cargo_ids", "delivery_at": "delivery_cargo_ids"}


def info(message):
    return InformationReply(kind="information", draft=None, reply=message)


def question(message, fields=("intent",), **details):
    return ClarificationReply(kind="clarification", draft=None, missing_fields=list(fields), reply=message, **details)


def _city_name(value):
    return load_russia().settlements.get(value, {}).get("name", value)


def _active(snapshot):
    draft = snapshot.draft
    return draft if draft and draft.tool in SHIPMENT_TOOLS else None


def known_conditions(snapshot):
    if not (draft := _active(snapshot)):
        return []
    values = draft.arguments
    known = []
    origin, destination = (_city_name(values[field]) if values.get(field) and field not in draft.missing_fields else None
                           for field in ("origin", "destination"))
    if origin and destination:
        known.append(f"{origin} → {destination}")
    elif origin or destination:
        known.append(f"Отправление — {origin}" if origin else f"Назначение — {destination}")
    if values.get("target_year"):
        known.append(f"{values['target_year']} год")
    schedule = values.get("schedule") or {}
    if schedule.get("mode") == "once" and schedule.get("ready_at"):
        known.append(f"Готовность — {schedule['ready_at']}")
    elif snapshot.readiness:
        clock = snapshot.readiness.get("clock")
        known.append("Дата готовности — " + snapshot.readiness["day"] + (f", {clock}" if clock else ""))
    stops = []
    for index, stop in enumerate(values.get("stops", [])):
        if f"stops.{index}.settlement" in draft.missing_fields or not stop.get("settlement"):
            continue
        name = _city_name(stop["settlement"])
        if stop.get("dwell_hours"):
            name += f" (+{number(stop['dwell_hours'])} ч)"
        if stop.get("no_reload"):
            name += " без перегрузки"
        stops.append(name)
    if stops:
        known.append("Через " + " → ".join(stops))
    cargoes = values.get("cargoes", [])
    for cargo in cargoes:
        if not (name := cargo.get("name") or cargo.get("cargo_type")):
            continue
        parts = [name]
        if cargo.get("weight_tonnes"):
            parts.append(f"{number(cargo['weight_tonnes'])} т")
        if cargo.get("volume_m3"):
            parts.append(f"{number(cargo['volume_m3'])} м³")
        if cargo.get("packaging"):
            parts.append(PACKAGING.get(cargo["packaging"], cargo["packaging"]))
        if cargo.get("splittable") is not None:
            parts.append("делимая партия" if cargo["splittable"] else "неделимая партия")
        if cargo.get("max_piece_tonnes"):
            parts.append(f"место до {number(cargo['max_piece_tonnes'] * 1000)} кг")
        if bounds := cargo.get("temperature_c") or (cargo.get("properties") or {}).get("required_temperature_c"):
            parts.append(f"{bounds[0]:+g}…{bounds[1]:+g} °C")
        known.append(", ".join(parts))
    if values.get("max_delivery_hours"):
        known.append(f"Срок до {number(values['max_delivery_hours'])} ч")
    if snapshot.options.deliver_by:
        known.append(f"Доставить к {snapshot.options.deliver_by.isoformat(timespec='minutes')}")
    return known


def _stop_index(stops, city):
    identifier, _ = resolve_city(city)
    matches = [i for i, stop in enumerate(stops)
               if normalized_name(city) == normalized_name(stop["settlement"])
               or identifier and identifier == resolve_city(stop["settlement"])[0]]
    return matches[0] if len(matches) == 1 else None


def _stop_position(arguments, stops, edit, default):
    anchor = edit.get("after") or edit.get("before")
    if not anchor:
        return default
    city, _ = resolve_city(anchor)
    edge = arguments.get("origin" if edit.get("after") else "destination")
    if edge and city and city == resolve_city(edge)[0]:
        return 0 if edit.get("after") else len(stops)
    index = _stop_index(stops, anchor)
    if index is None:
        return None
    return index + int(bool(edit.get("after")))


def _edit_stops(arguments, edits, pending_remove):
    stops = arguments["stops"]
    existing_count = len(stops)
    for edit in edits:
        city, operation = edit["city"], edit["operation"]
        index = _stop_index(stops, city)
        if operation == "add" and index is None:
            position = _stop_position(arguments, stops, edit, len(stops) if not existing_count else None)
            if position is None:
                return pending_remove, edit
            stops.insert(position, {"settlement": city, "action": edit.get("action", "visit"),
                                    "pickup_cargo_ids": [], "delivery_cargo_ids": [], "dwell_hours": edit.get("dwell_hours", 0),
                                    "no_reload": edit.get("no_reload", False)})
            continue
        if index is None:
            raise ToolQuestion(["stops"], f"Какую остановку изменить? «{city}» нет в текущем маршруте.")
        stop = stops[index]
        if operation == "remove":
            if any(stop.get(field) for field in ROLE_FIELDS.values()):
                pending_remove = stop["settlement"]
            else:
                stops.pop(index)
            continue
        if operation == "replace":
            if not edit.get("replacement"):
                raise ToolQuestion(["stops"], f"Какой город поставить вместо остановки «{_city_name(stop['settlement'])}»?")
            if pending_remove == stop["settlement"]:
                pending_remove = None
            stop["settlement"] = edit["replacement"]
        if operation == "move":
            remaining = stops[:index] + stops[index + 1:]
            position = _stop_position(arguments, remaining, edit, None)
            if position is None:
                return pending_remove, edit
            stops.pop(index)
            stops.insert(position, stop)
        if "dwell_hours" in edit:
            stop["dwell_hours"] = edit["dwell_hours"] or 0
        if edit.get("action"):
            stop["action"] = edit["action"]
            if edit["action"] != "visit":
                stop["no_reload"] = False
        if "no_reload" in edit:
            stop["no_reload"] = edit["no_reload"]
    return pending_remove, None


def _select_cargo(cargoes, row):
    if row.get("id"):
        index = next((i for i, cargo in enumerate(cargoes) if cargo["id"] == row["id"]), None)
        if index is None:
            raise ToolQuestion(["cargoes"], "Какую грузовую позицию нужно изменить? Укажите её название или номер.")
        return index
    if row.get("name"):
        matches = [i for i, cargo in enumerate(cargoes)
                   if normalized_name(row["name"]) == normalized_name(cargo.get("name") or cargo.get("cargo_type") or "")]
        if len(matches) == 1:
            return matches[0]
        if not matches and len(cargoes) <= 1:
            return 0 if cargoes else None
    if len(cargoes) == 1:
        return 0
    if cargoes:
        raise ToolQuestion(["cargoes"], "К какой грузовой позиции относится уточнение? Укажите название или номер.")
    return None


def _set_cargo_point(arguments, cargo_id, field, city):
    stops = arguments["stops"]
    origin, destination = (arguments.get(key) for key in ("origin", "destination"))
    target, _ = resolve_city(city) if city else (None, [])
    endpoint = origin if field == "pickup_at" else destination
    endpoint_id, _ = resolve_city(endpoint) if endpoint else (None, [])
    opposite = destination if field == "pickup_at" else origin
    opposite_id, _ = resolve_city(opposite) if opposite else (None, [])
    key = ROLE_FIELDS[field]
    for stop in stops:
        if cargo_id in stop.get(key, ()):
            stop[key].remove(cargo_id)
            # Derive the action again when a position moves to another city.
            stop["action"] = "visit"
    if city is None or target and target == endpoint_id:
        return
    if target and target == opposite_id:
        raise ToolQuestion(["cargoes"], "Погрузка позиции должна предшествовать её выгрузке.")
    index = _stop_index(stops, city)
    if index is None:
        if stops:
            raise ToolQuestion(["stops"], f"Где в маршруте находится «{city}»? Укажите порядок остановок.")
        stops.append({"settlement": city, "action": "visit", "dwell_hours": 0})
        index = 0
    stops[index].setdefault(key, []).append(cargo_id)


def _edit_cargoes(arguments, edits, replace):
    cargoes = arguments.get("cargoes", [])
    if replace:
        cargoes = []
        for stop in arguments["stops"]:
            stop.update(pickup_cargo_ids=[], delivery_cargo_ids=[], action="visit")
    previous = deepcopy(cargoes)
    new_positions = not previous
    for edit in edits:
        row = dict(edit)
        points = {field: row.pop(field) for field in ROLE_FIELDS if field in row}
        index = _select_cargo(previous, row) if not new_positions else None
        cargo = cargoes[index] if index is not None else {}
        identifier = cargo.get("id")
        if identifier is None:
            used = {item["id"] for item in cargoes}
            identifier = next(f"cargo-{n}" for n in range(1, len(used) + 2) if f"cargo-{n}" not in used)
        row.pop("id", None)
        if row.get("name") and (old_name := cargo.get("name") or cargo.get("cargo_type")) and normalized_name(row["name"]) != normalized_name(old_name):
            cargo = {}
            for stop in arguments["stops"]:
                for key in ROLE_FIELDS.values():
                    if identifier in stop.get(key, []):
                        stop[key].remove(identifier)
                        stop["action"] = "visit"
        patch = merge_arguments(cargo, row) | {"id": identifier}
        if "temperature_c" in row or (row.get("properties") or {}).get("temperature_control_required") is False:
            properties = patch.setdefault("properties", {}) or {}
            bounds = row.get("temperature_c")
            properties["temperature_control_required"] = bounds is not None
            if bounds:
                properties["required_temperature_c"] = bounds
            else:
                patch.pop("temperature_c", None)
                properties.pop("required_temperature_c", None)
            patch["properties"] = properties
        if index is None:
            cargoes.append(patch)
        else:
            cargoes[index] = patch
        for field, city in points.items():
            _set_cargo_point(arguments, identifier, field, city)
    if cargoes:
        arguments["cargoes"] = cargoes


def _route_issue(arguments, pending_remove):
    network = load_russia()
    stops = arguments.get("stops", [])
    if len(stops) > 8:
        raise ToolQuestion(["stops"], "Укажите не более восьми промежуточных остановок.")
    points = [(arguments, "origin", "origin", "город отправления"),
              *((stop, "settlement", f"stops.{i}.settlement", f"остановку №{i + 1}") for i, stop in enumerate(stops)),
              (arguments, "destination", "destination", "город назначения")]
    seen = set()
    for row, key, path, label in points:
        if not row.get(key):
            continue
        city, choices = resolve_city(row[key])
        if city is None:
            names = [f"{network.settlements[c]['name']} ({network.settlements[c].get('region') or c})" for c in choices]
            prompt = f"Уточните {label}. «{row[key]}» не удалось однозначно сопоставить со справочником."
            if names:
                prompt += " Подходящие варианты — " + ", ".join(names) + "."
            raise ToolQuestion([path], prompt)
        if city in seen:
            raise ToolQuestion([path], "Отправление, назначение и промежуточные остановки должны различаться.")
        seen.add(city)
        row[key] = city
    positions = {cargo["id"]: i for i, cargo in enumerate(arguments.get("cargoes", []))}
    pickups, deliveries = {}, {}
    for index, stop in enumerate(stops):
        pickup, delivery = (stop.setdefault(field, []) for field in ROLE_FIELDS.values())
        if pending_remove and _stop_index(stops, pending_remove) == index and (pickup or delivery):
            field, identifier = ("pickup_at", pickup[0]) if pickup else ("delivery_at", delivery[0])
            label = "загрузить" if pickup else "выгрузить"
            path = f"cargoes.{positions[identifier]}.{field}" if identifier in positions else f"stops.{index}.cargoes"
            raise ToolQuestion([path], f"Где {label} грузовую позицию после удаления остановки «{_city_name(stop['settlement'])}»?")
        action = stop.get("action")
        if stop.get("no_reload") and (pickup or delivery):
            raise ToolQuestion([f"stops.{index}"], f"В пункте «{_city_name(stop['settlement'])}» указаны и остановка без перегрузки, и операции с грузом. Что оставить?")
        if (action in ("pickup", "pickup_delivery") and not pickup) or (action in ("delivery", "pickup_delivery") and not delivery):
            raise ToolQuestion([f"stops.{index}.cargoes"], f"Какой груз нужно загрузить или выгрузить в пункте «{_city_name(stop['settlement'])}»? Укажите позицию, массу и при необходимости объём.")
        stop["action"] = "pickup_delivery" if pickup and delivery else "pickup" if pickup else "delivery" if delivery else "visit"
        for ids, mapping in ((pickup, pickups), (delivery, deliveries)):
            for identifier in ids:
                if identifier not in positions or identifier in mapping:
                    raise ToolQuestion([f"stops.{index}.cargoes"], "Уточните позиции для погрузки и выгрузки. У каждой позиции один пункт погрузки и один пункт выгрузки.")
                mapping[identifier] = index
    if positions and (len(pickups) == len(positions) or len(deliveries) == len(positions)):
        raise ToolQuestion(["stops"], "Укажите груз для погрузки в начале маршрута и груз для выгрузки в конечном пункте.")
    for identifier in positions:
        if pickups.get(identifier, -1) >= deliveries.get(identifier, len(stops)):
            raise ToolQuestion(["stops"], "Погрузка каждой позиции должна предшествовать её выгрузке.")


def _tool_for(arguments):
    year = arguments.get("target_year")
    if year is None:
        ready = (arguments.get("schedule") or {}).get("ready_at")
        if isinstance(ready, str):
            moment = datetime.fromisoformat(ready)
            year = moment.astimezone(MOSCOW).year if moment.utcoffset() is not None else moment.year
            if year > BASE_YEAR:
                arguments["target_year"] = year
    return FORECAST_TOOL if year is not None and year > BASE_YEAR else "calculate_russia"


def _apply_changes(current, action, changes, message):
    old_draft = _active(current)
    previous = old_draft.arguments if old_draft else {}
    arguments = deepcopy(previous) if action != "new" else {}
    options = current.options.model_copy(deep=True) if action != "new" else DialogOptions()
    pending_remove = current.pending_remove if action != "new" else None
    pending_stop = current.pending_stop if action != "new" else None
    pending_year = current.pending_year if action != "new" else None
    readiness = current.readiness if action != "new" else {}
    if action == "new" and SAME_ROUTE.search(clean(message)):
        arguments.update({key: previous[key] for key in ("origin", "destination") if key in previous})
        arguments["stops"] = [{"settlement": stop["settlement"], "action": "visit"} for stop in previous.get("stops", [])]
    arguments.setdefault("stops", [])
    changes = dict(changes)
    for field in ("preferred_mode", "objective", "deliver_by"):
        if field in changes:
            value = changes.pop(field)
            setattr(options, field, value if field != "objective" or value else "cheapest")
    edits = changes.pop("stops", [])
    cargoes = changes.pop("cargoes", [])
    replace = changes.pop("replace_cargoes", False)
    if "readiness" in changes:
        readiness = changes.pop("readiness")
        arguments.pop("schedule", None)
        if ready_moment := readiness_moment(readiness):
            changes["ready_at"] = ready_moment
    ready_supplied = "ready_at" in changes
    ready = changes.pop("ready_at", None)
    if ready_supplied:
        pending_year = None
        readiness = {}
        if ready is None:
            arguments.pop("schedule", None)
        else:
            arguments["schedule"] = {"mode": "once", "ready_at": ready.isoformat()}
            if "target_year" not in changes:
                arguments.pop("target_year", None)
    if "target_year" in changes and not ready_supplied and (schedule := arguments.get("schedule")):
        year = changes["target_year"]
        if year != arguments.get("target_year") and not (year and str(schedule.get("ready_at", ""))[:4] == str(year)):
            if re.search(r"без\s+дат|только\s+год|годов\w+\s+оценк", clean(message)):
                arguments.pop("schedule")
                pending_year = None
            else:
                pending_year = changes.pop("target_year")
    for key, value in changes.items():
        if value is None:
            arguments.pop(key, None)
        else:
            arguments[key] = value
    if edits:
        pending_remove, pending_stop = _edit_stops(arguments, edits, pending_remove)
    _edit_cargoes(arguments, cargoes, replace)
    if pending_remove and (index := _stop_index(arguments["stops"], pending_remove)) is not None:
        if not any(arguments["stops"][index].get(field) for field in ROLE_FIELDS.values()):
            arguments["stops"].pop(index)
            pending_remove = None
    tool = _tool_for(arguments)
    draft = ToolState(tool=tool, arguments=arguments)
    return DialogSnapshot(draft=draft, options=options, pending_remove=pending_remove,
                          pending_stop=pending_stop, pending_year=pending_year, readiness=readiness,
                          result=current.result if action != "new" else None,
                          result_revision=current.result_revision if action != "new" else None)


def _deadline_hours(options, arguments):
    deadline = options.deliver_by
    if deadline.utcoffset() is None:
        raise ToolQuestion(["deliver_by"], "Укажите часовой пояс срока доставки.")
    ready_at = (arguments.get("schedule") or {}).get("ready_at")
    if not isinstance(ready_at, str) or (ready := datetime.fromisoformat(ready_at)).utcoffset() is None:
        raise ToolQuestion(["schedule.ready_at"], "Укажите дату, время и часовой пояс готовности, чтобы проверить дедлайн.")
    hours = (deadline - ready).total_seconds() / 3600
    if hours <= 0:
        raise ToolQuestion(["deliver_by"], "Срок доставки должен быть позже готовности груза. Какую дату нужно изменить?")
    return min(hours, arguments.get("max_delivery_hours") or hours)


def _year_range(year):
    limits = forecast_options()
    base = str(RUSSIA_FIRST_YEAR) if RUSSIA_FIRST_YEAR == RUSSIA_LAST_YEAR else f"{RUSSIA_FIRST_YEAR}–{RUSSIA_LAST_YEAR}"
    return (f"Для {year} года оценки по этим данным нет. Расчёт на дату доступен за {base} год, "
            f"годовой прогноз — с {limits['min_year']} по {limits['max_year']} год.")


def _calculate(run_tool, draft, options):
    arguments = deepcopy(draft.arguments)
    forecast = draft.tool == FORECAST_TOOL
    year = arguments.get("target_year")
    if year is not None and (year < RUSSIA_FIRST_YEAR or year > forecast_options()["max_year"]):
        return question(_year_range(year), ["target_year"])
    unsupported_modes = set(arguments.get("allowed_modes", [])) - PROJECTED_MODES if forecast else set()
    if unsupported_modes and not PROJECTED_MODES.intersection(arguments["allowed_modes"]):
        return question("Годовая оценка доступна для авто, ж/д и авиа на направлениях с опубликованной ставкой. Какой транспорт можно использовать?", ["allowed_modes"])
    if (schedule := arguments.get("schedule")) and schedule.get("mode") != "once":
        return question("Для расчёта разовой перевозки укажите дату, время и часовой пояс готовности.", ["schedule.ready_at"])
    if schedule and (ready_at := schedule.get("ready_at")):
        ready = datetime.fromisoformat(ready_at)
        if ready.utcoffset() is not None:
            ready_year = ready.astimezone(MOSCOW).year
            if forecast and ready_year != year:
                return question(f"Укажите готовность в {year} году или попросите оценить перевозку без точной даты.", ["schedule.ready_at"])
            if ready_year < RUSSIA_FIRST_YEAR or ready_year > forecast_options()["max_year"]:
                return question(_year_range(ready_year), ["schedule.ready_at"])
    deadline_question = None
    if options.deliver_by:
        try:
            arguments["max_delivery_hours"] = _deadline_hours(options, arguments)
        except ToolQuestion as exc:
            if not forecast or schedule or exc.fields != ["schedule.ready_at"]:
                return question(str(exc), exc.fields)
            deadline_question = exc
    technical = not forecast and arguments.get("max_delivery_hours") is None
    if technical:
        arguments["max_delivery_hours"] = TECHNICAL_HOURS
    if not forecast:
        arguments.pop("target_year", None)
        arguments.setdefault("max_legs", 12)
    arguments.setdefault("allowed_modes", ["road", "rail", "air", "sea", "river", "pipeline"])
    try:
        answer = run_tool(ToolState(tool=draft.tool, arguments=arguments), "Расчёт перевозки")
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return question(detail.get("message", "Уточните параметры маршрута и груза."))
    if answer.kind == "clarification" and year is None and not schedule and "schedule" in answer.missing_fields:
        lines = answer.reply.splitlines()
        lines = ["• на какой год или дату рассчитать перевозку" if "когда груз будет готов" in line else line for line in lines]
        answer = answer.model_copy(update={"reply": "\n".join(lines)})
    if answer.kind != "calculation":
        return answer
    result = answer.calculation
    lines = [answer.reply]
    if technical and isinstance(result, RussiaResponse):
        result = result.model_copy(update={"request": result.request | {"max_delivery_hours": None}})
        lines.append(f"Поиск выполнен в пределах {TECHNICAL_HOURS // 24} суток от готовности груза.")
    if unsupported_modes:
        lines.append("Для годовой оценки недоступны виды транспорта — " + ", ".join(MODE_LABELS[mode] for mode in sorted(unsupported_modes)) + ".")
    if options.requirements and any(item.get("required", True) and item.get("kind") != "pallets" for item in options.requirements):
        result = result.model_copy(update={"recommendation": None})
        lines.append("Перед выбором маршрута подтвердите ранее заданные дополнительные условия у перевозчика.")
        answer = answer.model_copy(update={"conditional": True})
    candidate = _selected(result, options, answer.conditional)
    if candidate and candidate["id"] != result.recommendation:
        mode = " → ".join(MODE_LABELS[m] for m in candidate["modes"])
        price = number(candidate["total_cost_rub"])
        lines.append(f"По заданному предпочтению — {mode}, {price} ₽ {VAT[result.request['cost_basis']]}, "
                     f"{number(candidate['max_delivery_hours'])} ч.")
    if deadline_question:
        return question(f"{deadline_question}\n\n" + "\n\n".join(lines), deadline_question.fields,
                        calculation=result, sources=answer.sources, tool=answer.tool, engine=answer.engine)
    return answer.model_copy(update={"reply": "\n\n".join(lines)[:8000], "calculation": result})


def _selected(result, options, conditional=False):
    if not isinstance(result, RussiaResponse) or conditional:
        return None
    rows = [row for row in result.candidates if row["status"] == "feasible" and row["total_cost_rub"] is not None]
    preferred = [row for row in rows if options.preferred_mode in row["modes"]]
    if options.objective == "fastest":
        return min(preferred or rows, key=lambda row: (row["max_delivery_hours"], row["total_cost_rub"]), default=None)
    return min(preferred or rows, key=lambda row: (row["total_cost_rub"], row["max_delivery_hours"]), default=None)


def settlement_names(answers):
    locations = set()
    for result in answers:
        if isinstance(result.calculation, RussiaResponse):
            locations.update(identifier for identifier in result.calculation.settlements if identifier)
    return {identifier: _city_name(identifier) for identifier in sorted(locations)}


def _reply(current, revision, answer, previous=(), scenarios=()):
    known = known_conditions(current)
    selected = _selected(answer.calculation, current.options, getattr(answer, "conditional", False))
    return DialogReply(revision=revision, answer=answer, known=known,
                       changes=[item for item in known if item not in previous] if previous else [],
                       scenarios=list(scenarios), selected_candidate=selected["id"] if selected else None,
                       settlement_names=settlement_names([answer, *(row.answer for row in scenarios)]))


def _calculate_snapshot(run_tool, snapshot):
    if snapshot.pending_stop:
        city = _city_name(snapshot.pending_stop["city"])
        return question(f"После какого города или перед каким городом поставить остановку «{city}»?", ["stops"])
    if snapshot.pending_year is not None:
        return question(f"Указать дату готовности в {snapshot.pending_year} году или рассчитать годовую оценку без даты?", ["schedule.ready_at"])
    if snapshot.readiness:
        clock = "clock" in snapshot.readiness
        zone = "offset" in snapshot.readiness
        label = "время и часовой пояс" if not clock and not zone else "время" if not clock else "часовой пояс"
        return question(f"Укажите {label} готовности груза.", ["schedule.ready_at"])
    try:
        _route_issue(snapshot.draft.arguments, snapshot.pending_remove)
        return _calculate(run_tool, snapshot.draft, snapshot.options)
    except ToolQuestion as exc:
        return question(str(exc), exc.fields)


def _save(state, snapshot, answer, revision):
    fields = answer.missing_fields if answer.kind == "clarification" else []
    snapshot.draft = snapshot.draft.model_copy(update={"missing_fields": fields})
    if isinstance(answer.calculation, RussiaResponse) and answer.calculation.input_status == "ready":
        snapshot.result = answer
        snapshot.result_revision = revision
    if state.current != snapshot and state.current.draft:
        state.previous = [*state.previous, state.current][-5:]
    state.current = snapshot
    state.proposed_split = None


def process_dialog(planner, message, state, revision, run_tool):
    current = state.current
    draft = _active(current)
    previous = known_conditions(current)
    text = message.strip()
    single_year = YEAR_ONLY.fullmatch(text)
    comparison = COMPARE_YEARS.fullmatch(text)
    if draft and current.pending_stop and (anchor := stop_anchor(text)):
        edit = {key: value for key, value in current.pending_stop.items() if key not in ("after", "before")}
        action, changes, years = "update", {"stops": [edit | anchor]}, None
    elif draft and current.pending_year is not None and re.fullmatch(r"(?:да,\s*)?(?:без\s+даты|только\s+год)[.!?]*", text, re.I):
        action, changes, years = "update", {"target_year": current.pending_year, "ready_at": None}, None
    elif draft and current.readiness and re.fullmatch(r"(?:в\s+)?(?:\d{1,2}:\d{2})?\s*(?:мск|по\s+москве|utc\s*[+-]\d{1,2}(?::\d{2})?)?[.!?]*", text, re.I):
        action, changes, years = "update", {"readiness": readiness_parts(text, current.readiness)}, None
    elif draft and single_year:
        action, changes, years = "update", {"target_year": int(single_year[1])}, None
        state.comparison_years = None
    elif draft and comparison:
        action, changes, years = "compare", {}, [int(comparison[1]), int(comparison[2])]
    elif draft and (stop := visit_only_stop(text)):
        action, changes, years = "update", {"stops": [stop]}, None
    else:
        try:
            action, changes, years = plan_turn(planner, message, draft)
        except (InvalidAssistantTurn, ValidationError) as exc:
            logger.debug("Invalid shipment interpretation: %s", type(exc).__name__)
            labels = dict.fromkeys(_field_label(field) for field in draft.missing_fields) if draft else {}
            detail = "Уточните, пожалуйста: " + ", ".join(labels) + "." if labels else HELP
            return _reply(current, revision, question(detail, draft.missing_fields if labels else ("intent",)))
        if (draft and current.pending_stop and (anchor := stop_anchor(text, allow_tail=True))
                and action in ("update", "clarify") and not NEW_SHIPMENT.search(clean(text))):
            edit = {key: value for key, value in current.pending_stop.items() if key not in ("after", "before")}
            remaining = [row for row in changes.get("stops", []) if _stop_index(
                [{"settlement": edit["city"]}], row["city"],
            ) is None]
            action, changes = "update", changes | {"stops": [edit | anchor, *remaining]}
    if action == "unsupported":
        return _reply(current, revision, info(HELP))
    if action == "clarify" or action == "update" and not changes:
        detail = "Какое условие текущей перевозки нужно изменить? Укажите параметр и новое значение." if draft else HELP
        return _reply(current, revision, question(detail))
    if action == "compare" and (not years or years[0] == years[1]):
        return _reply(current, revision, question("Укажите два разных года для сравнения.", ["target_year"]))
    if action == "compare" and not draft and not changes:
        return _reply(current, revision, question(HELP))
    try:
        snapshot = _apply_changes(current, action, changes, message)
    except ToolQuestion as exc:
        return _reply(current, revision, question(str(exc), exc.fields))
    if action == "new":
        state.comparison_years = None
    years = years or state.comparison_years
    if not years:
        answer = _calculate_snapshot(run_tool, snapshot)
        _save(state, snapshot, answer, revision)
        return _reply(snapshot, revision, answer, previous if action != "new" else ())
    if snapshot.draft.arguments.get("schedule"):
        return _reply(current, revision, question("Для сравнения годов используйте оценку без точной даты готовности.", ["schedule.ready_at"]))
    try:
        _route_issue(snapshot.draft.arguments, snapshot.pending_remove)
    except ToolQuestion as exc:
        answer = question(str(exc), exc.fields)
        state.comparison_years = years
        _save(state, snapshot, answer, revision)
        return _reply(snapshot, revision, answer, previous)
    scenarios = []
    for year in years:
        draft_for_year = ToolState(tool=FORECAST_TOOL, arguments=snapshot.draft.arguments | {"target_year": year})
        if year <= BASE_YEAR:
            answer = question(_year_range(year) if year < BASE_YEAR else f"Для {year} года требуется дата готовности.", ["target_year"])
        else:
            answer = _calculate(run_tool, draft_for_year, snapshot.options)
        scenarios.append(ScenarioReply(title=f"{year} год", answer=answer))
    pending = next((row.answer for row in scenarios if row.answer.kind == "clarification" and row.answer.missing_fields != ["target_year"]), None)
    answer = pending or info("Оценки для двух лет рассчитаны по одним условиям.")
    state.comparison_years = years if pending else None
    snapshot.scenarios = scenarios
    _save(state, snapshot, answer, revision)
    return _reply(snapshot, revision, answer, previous, scenarios)
