"""Расписание: реальные события вместо умножения частоты на округлённый период."""

import calendar
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from backend.calculation_models import MAX_SHIPMENTS


def add_months(anchor, months):
    absolute = anchor.year * 12 + anchor.month - 1 + months
    year, month_zero = divmod(absolute, 12)
    if not 1 <= year <= 9999:
        raise ValueError("Дата расписания выходит за допустимый диапазон")
    month = month_zero + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return anchor.replace(year=year, month=month, day=day)


def build_schedule(schedule):
    if schedule.mode == "once":
        events = [schedule.ready_at]
        start, end = events[0], events[0] + timedelta(microseconds=1)
    elif schedule.mode == "explicit":
        events = list(schedule.ready_at)
        start, end = events[0], events[-1] + timedelta(microseconds=1)
    else:
        start, end = schedule.start_at, schedule.end_at
        frequency = schedule.frequency
        events, index = [], 0
        while True:
            if frequency.unit == "month":
                left = add_months(start, index * frequency.every)
                right = add_months(start, (index + 1) * frequency.every)
            elif frequency.unit in ("hour", "minute"):
                step = timedelta(minutes=frequency.every) if frequency.unit == "minute" else timedelta(hours=frequency.every)
                left, right = start + index * step, start + (index + 1) * step
            else:
                days = frequency.every * (7 if frequency.unit == "week" else 1)
                left = start + timedelta(days=index * days)
                right = start + timedelta(days=(index + 1) * days)
            if left >= end:
                break
            for position in range(frequency.count):
                ready = left + (right - left) * (position / frequency.count)
                if ready < end:
                    events.append(ready)
                    if len(events) > MAX_SHIPMENTS:
                        raise ValueError(f"Не более {MAX_SHIPMENTS} отправок за расчёт: сократите период или частоту")
            index += 1
    if not events:
        raise ValueError("В выбранном периоде нет отправок")
    return [value.astimezone(timezone.utc) for value in events], start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def week_key(moment, zone):
    value = moment.astimezone(zone)
    return (value - timedelta(days=value.weekday())).date().isoformat()


def billing_weeks(start, end, timezone_name):
    zone = ZoneInfo(timezone_name)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    cursor = (local_start - timedelta(days=local_start.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    weeks = []
    while cursor < local_end:
        weeks.append(cursor.date().isoformat())
        cursor += timedelta(days=7)
    return weeks, zone
