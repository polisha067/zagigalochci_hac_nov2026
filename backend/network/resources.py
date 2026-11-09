"""Сценарные ресурсы: интервалы полуоткрытые, освобождение раньше нового занятия."""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal


class PlanningProblem(Exception):
    def __init__(self, code, message, status="infeasible", object_id=None, fields=()):
        self.issue = {"code": code, "message": message, "status": status, "object_id": object_id, "fields": list(fields)}
        super().__init__(message)


@dataclass
class Reservation:
    start: object
    end: object
    amount: Decimal


class Ledger:
    def __init__(self):
        self.reservations = {}

    def fits(self, resource, start, end, amount, capacity):
        amount, capacity = Decimal(str(amount)), Decimal(str(capacity))
        if amount > capacity:
            return False
        if end <= start or amount == 0:
            return True
        relevant = [r for r in self.reservations.get(resource, []) if r.start < end and r.end > start]
        points = {start}
        points.update(max(start, r.start) for r in relevant)
        return all(amount + sum((r.amount for r in relevant if r.start <= point < r.end), Decimal(0)) <= capacity for point in points)

    def reserve(self, resource, start, end, amount, capacity):
        if not self.fits(resource, start, end, amount, capacity):
            raise PlanningProblem("capacity_exceeded", "Превышена одновременная доступная ёмкость ресурса", object_id=resource)
        if end > start and amount:
            self.reservations.setdefault(resource, []).append(Reservation(start, end, Decimal(str(amount))))

    def releases_after(self, resource, after):
        return sorted({r.end for r in self.reservations.get(resource, []) if r.end > after})

    def operation(self, resource, ready, duration_hours, lanes, windows, latest):
        duration = timedelta(hours=float(duration_hours))
        if lanes == 0:
            raise PlanningProblem("no_handling_capacity", "Нет доступных линий обработки", object_id=resource)
        for window in sorted(windows, key=lambda w: w.start):
            start = max(ready, window.start)
            limit = min(window.end, latest)
            candidates = [start] + [value for value in self.releases_after(resource, start) if value <= limit]
            for candidate in candidates:
                end = candidate + duration
                if end <= limit and self.fits(resource, candidate, end, 1, lanes):
                    self.reserve(resource, candidate, end, 1, lanes)
                    return candidate, end
        raise PlanningProblem("no_handling_window", "Нет окна и мощности для полной обработки груза", object_id=resource)


def in_windows(moment, windows, end=None):
    return any(window.start <= moment < window.end and (end is None or end <= window.end) for window in windows)
