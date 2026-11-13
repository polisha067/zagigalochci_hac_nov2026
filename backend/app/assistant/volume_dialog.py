import re

from backend.a1_forecast import MAX_FORECAST_YEAR, MIN_FORECAST_YEAR, forecast_options

from .dialog_planner import VOLUME, clean, name_in_quote
from .schemas import ToolState


TOOL = "calculate_corridor_forecast"
VOLUME_INTENT = re.compile(
    r"грузопоток|грузооборот|(?:объем\w*|тоннаж\w*)\s+(?:грузоперевоз\w*|перевоз\w*|постав\w*)"
    r"|(?:прогноз\w*|спрогнозиру\w*)[^.!?]{0,60}(?:объем\w*|тоннаж\w*)"
)
DELIVERY_INTENT = re.compile(r"стоимост|сколько\s+стоит|цен[ауые]\b|тариф|достав\w*\s+\d")
FOLLOWUP = re.compile(
    r"(?:а\s+если|а\s+(?:на|в|за|с)\s|(?:а\s+)?теперь\b|по\s+(?:месяц|квартал|год)|"
    r"(?:на\s+|в\s+|за\s+|с\s+)?\d{4}\b)"
)
SHORT_VOLUME_INTENT = re.compile(r"\b(?:объем\w*|тоннаж\w*)\s+грузов\b")
YEAR = re.compile(r"(?<!\d)\d{4}(?!\d)")


def volume_turn(message, previous):
    text = clean(message)
    explicit = VOLUME_INTENT.search(text) or (
        SHORT_VOLUME_INTENT.search(text) and YEAR.search(text)
    )
    if VOLUME.search(text) or DELIVERY_INTENT.search(text) or not (
        explicit or previous and (FOLLOWUP.match(text) or "origin" in previous.missing_fields)
    ):
        return None

    options = forecast_options()
    routes = options["routes"]
    parts = re.split(r"\s*(?:→|->|[–—]|(?<=[а-я])-(?=[а-я]))\s*|\s+", text)
    matches = [
        (route["origin"], route["destination"])
        for route in routes
        if any(
            name_in_quote(route["origin"], " ".join(parts[:i]))
            and name_in_quote(route["destination"], " ".join(parts[i:]))
            for i in range(1, len(parts))
        )
    ]
    values = dict(previous.arguments) if previous else {}
    if len(matches) == 1:
        values["origin"], values["destination"] = matches[0]
    elif len(matches) > 1 or explicit and not FOLLOWUP.match(text):
        values.pop("origin", None)
        values.pop("destination", None)
    years = [int(year) for year in YEAR.findall(text)]
    if years:
        values["start_year"], values["end_year"] = years[0], years[-1]
    if "по месяц" in text or "помесяч" in text:
        values["granularity"] = "month"
    elif "по квартал" in text or "поквартал" in text:
        values["granularity"] = "quarter"
    elif "по год" in text or "за год" in text:
        values["granularity"] = "year"

    fields = []
    prompts = []
    if "origin" not in values:
        fields.extend(("origin", "destination"))
        prompts.append(
            f"Укажите одно из {len(routes)} направлений из вкладки «Объём», например Челябинск → Омск. Для других направлений прогноз объёма пока недоступен."
        )
    start, end = values.get("start_year"), values.get("end_year")
    if start is None or not MIN_FORECAST_YEAR <= start <= end <= MAX_FORECAST_YEAR:
        fields.append("start_year")
        prompts.append(f"Укажите год или период от {MIN_FORECAST_YEAR} до {MAX_FORECAST_YEAR}, от раннего года к позднему.")
    return ToolState(tool=TOOL, arguments=values, missing_fields=fields), " ".join(prompts)
