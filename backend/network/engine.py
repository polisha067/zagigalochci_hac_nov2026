"""Единая временная симуляция цепочки; сеть не подменяется городскими расстояниями."""

from dataclasses import dataclass
from datetime import timedelta, timezone
from decimal import Decimal
import hashlib
import math

from backend.network.graph import CATEGORIES, load_network
from backend.network.cargo import prepare_cargoes
from backend.network.models import NetworkRequest
from backend.network.policies import Policies, need
from backend.network.resources import Ledger, PlanningProblem, in_windows
from backend.schedule import build_schedule
from backend.transport_calculator import dec, money


@dataclass
class Item:
    key: str
    spec: object
    ready: object
    shipment: int

    @property
    def mass(self):
        return dec(self.spec.weight_tonnes)

    @property
    def volume(self):
        return dec(self.spec.volume_m3) if self.spec.volume_m3 is not None else None


def _next_timetable(timetable, ready):
    if timetable.kind == "on_demand":
        return ready
    if timetable.kind == "explicit":
        return next((value for value in timetable.departures if value >= ready), None)
    seconds = timetable.every_hours * 3600
    steps = max(0, math.ceil((ready - timetable.anchor).total_seconds() / seconds))
    return timetable.anchor + timedelta(seconds=steps * seconds)


class Planner:
    def __init__(self, network, request, include_pricing=False):
        self.network, self.request = network, request
        self.policies = Policies(network, request)
        self.ledger = Ledger()
        self.costs, self.steps, self.deliveries = [], [], []
        self.stored = {}
        self.include_pricing = include_pricing
        self.published_costs = []

    def charge(self, kind, identifier, at, batch, quote, items, quote_id=None, recorded_at=None, **terms):
        pricing = {} if self.include_pricing else None
        amount = self.policies.price(quote, items, at, quote_id or identifier, breakdown=pricing, **terms)
        row = {"kind": kind, "object_id": identifier, "amount_rub": float(amount), "at": (recorded_at or at).isoformat(), "batch": batch}
        if pricing is not None:
            row["pricing"] = pricing
        self.costs.append(row)
        if quote.evidence.status == "fact":
            self.published_costs.append(amount)

    def operation(self, hub, operation, items, ready, latest, kind, batch, *, included=False):
        operation = need(operation, kind + "_operation", hub.id)
        ready = max(ready, operation.evidence.valid_from)
        if operation.quote is not None:
            ready = max(ready, operation.quote.evidence.valid_from)
        self.policies.evidence(operation.evidence, ready, operation.resource_id, role="handling")
        throughput = dec(need(operation.tonnes_per_hour, "tonnes_per_hour", operation.resource_id))
        duration = dec(operation.fixed_hours) + sum((item.mass for item in items), Decimal(0)) / throughput
        if duration > dec(max(0,(latest-ready).total_seconds()))/3600:
            raise PlanningProblem("handling_deadline", "Обработка не укладывается в предельный срок", object_id=hub.id)
        start, end = self.ledger.operation("handling:" + operation.resource_id, ready, duration, operation.lanes, hub.windows, min(latest,operation.evidence.valid_until,hub.evidence.valid_until))
        self.policies.evidence(operation.evidence, start, operation.resource_id, end, role="handling")
        self.policies.evidence(hub.evidence, start, hub.id, end, role="hub")
        if not included:
            self.charge(kind, hub.id, start, batch, operation.quote, items,
                        quote_id=operation.resource_id, component="handling")
        self.steps.append({"kind": kind, "hub_id": hub.id, "cargo_ids": [item.key for item in items], "start_at": start.isoformat(), "end_at": end.isoformat(), "batch": batch})
        return end

    def service(self, service, items, ready, latest, batch):
        # Проверяем свойства на предполагаемую дату, затем подтверждаем на найденный выезд.
        first_ready = max(ready + timedelta(hours=service.cutoff_hours), service.evidence.valid_from)
        if service.quote is not None:
            first_ready = max(first_ready, service.quote.evidence.valid_from)
        self.policies.mode(service, items, first_ready)
        units, mass, volume = self.policies.units(service, items)
        flow_hours = mass / dec(service.flow_tonnes_per_hour) if service.mode == "pipeline" else Decimal(0)
        if dec(service.travel_hours) + flow_hours > dec(max(0,(latest-first_ready).total_seconds()))/3600:
            raise PlanningProblem("stage_deadline", "Время перевозки/закачки превышает предельный срок", object_id=service.id)
        duration = timedelta(hours=service.travel_hours + float(flow_hours))
        origin, target = self.network.hubs[service.from_hub], self.network.hubs[service.to_hub]
        capacity = need(service.available_units, "available_units", service.id) if service.booking != "flow" else 1
        if service.booking != "flow" and units > capacity:
            raise PlanningProblem("stage_capacity", "Партия превышает мощность одного отправления", object_id=service.id)
        quote = need(service.quote, "quote", service.id)
        cursor = first_ready
        for _ in range(4096):
            departure = _next_timetable(service.timetable, cursor)
            if departure is None or departure > latest:
                break
            arrival = departure + duration
            if arrival > latest:
                break
            self.policies.evidence(service.evidence, departure, service.id, arrival, role="freight_service")
            self.policies.evidence(quote.evidence, departure, service.id + ":quote", role="tariff")
            window_ok = in_windows(departure, service.windows, arrival) and in_windows(departure, origin.windows) and in_windows(arrival, target.windows)
            resource = service.resource_id
            if service.booking == "flow":
                reservation_end = departure + max(timedelta(microseconds=1),timedelta(hours=float(flow_hours)))
                requirements = [("flow:" + resource, 1, 1)]
            elif service.capacity_scope == "fleet":
                reservation_end = arrival + timedelta(hours=service.turnaround_hours)
                requirements = [("units:" + resource, units, capacity)]
            else:
                reservation_end = departure + timedelta(microseconds=1)
                slot = resource + ":" + departure.astimezone(timezone.utc).isoformat()
                if service.booking == "dedicated":
                    requirements = [("units:" + slot, units, capacity)]
                else:
                    requirements = [("tonnes:" + slot, mass, self.policies.payload_capacity(service) * capacity)]
                    if volume is not None and service.unit_m3 is not None:
                        requirements.append(("m3:" + slot, volume, dec(service.unit_m3) * capacity))
            resources_ok = all(self.ledger.fits(key, departure, reservation_end, amount, cap) for key, amount, cap in requirements)
            if window_ok and resources_ok:
                self.policies.mode(service, items, departure)
                for key, amount, cap in requirements:
                    self.ledger.reserve(key, departure, reservation_end, amount, cap)
                self.charge("transport", service.id, departure, batch, quote, items,
                            units=units, distance=service.distance_km, mode=service.mode)
                for charge in service.additional_charges:
                    charge_at = departure if charge.at == "departure" else arrival
                    self.charge(charge.id, service.id, charge_at, batch, charge.quote, items,
                                quote_id=service.id+":"+charge.id, units=units, component="handling")
                self.steps.append({"kind": "transport", "service_id": service.id, "mode": service.mode, "from_hub": service.from_hub, "to_hub": service.to_hub, "cargo_ids": [item.key for item in items], "weight_tonnes": float(mass), "volume_m3": float(volume) if volume is not None else None, "units": units if service.booking != "flow" else None, "flow_hours": float(flow_hours), "ready_at": ready.isoformat(), "departure_at": departure.isoformat(), "arrival_at": arrival.isoformat(), "wait_hours": (departure-ready).total_seconds()/3600, "batch": batch})
                return departure, arrival
            # Для on-demand сдвигаем к фактической границе окна/освобождению ресурса,
            # а не подбираем произвольную задержку с выдуманной скоростью.
            changes = [departure + timedelta(microseconds=1)] if service.timetable.kind != "on_demand" else []
            for window in service.windows + origin.windows:
                if window.start > departure:
                    changes.append(window.start)
            for window in target.windows:
                value = window.start - duration
                if value > departure:
                    changes.append(value)
            for key, _, _ in requirements:
                changes.extend(self.ledger.releases_after(key, departure))
            if not changes:
                break
            cursor = min(value for value in changes if value > departure)
        raise PlanningProblem("no_service_slot", "Нет отправления с подходящим окном приёма и свободной мощностью до предельного срока", object_id=service.id)

    def storage(self, hub, items, starts, end, batch, transport_service=None):
        if not items:
            return
        capacity = need(hub.storage_tonnes, "storage_tonnes", hub.id)
        max_hours = need(hub.max_storage_hours, "max_storage_hours", hub.id)
        tonne_hours = Decimal(0)
        for item in items:
            start = starts[item.key]
            hours = dec((end-start).total_seconds()) / 3600
            if hours < 0:
                raise PlanningProblem("internal_custody_order", "Некорректная последовательность передачи груза", object_id=hub.id)
            if hours > dec(max_hours):
                raise PlanningProblem("storage_duration", "Превышен допустимый срок хранения в хабе", object_id=hub.id)
            self.policies.evidence(hub.evidence, start, hub.id, end, role="hub")
            if end > start:
                for other, other_start, other_end in self.stored.get(hub.id, []):
                    if other_start < end and other_end > start:
                        self.policies.compatible([item, other])
                self.stored.setdefault(hub.id, []).append((item, start, end))
            self.ledger.reserve("storage-tonnes:" + hub.id, start, end, item.mass, capacity)
            if self.request.capacity_basis == "mass_and_volume" or (hub.storage_m3 is not None and item.volume is not None):
                self.ledger.reserve("storage-m3:" + hub.id, start, end, need(item.volume, "volume_m3", item.spec.id), need(hub.storage_m3, "storage_m3", hub.id))
            tonne_hours += item.mass * hours
        storage_start = min(starts[item.key] for item in items)
        selected_quote = transport_service.storage_quotes.get(hub.id) if transport_service is not None else None
        quote = need(selected_quote or hub.storage_quote, "storage_quote", hub.id)
        self.policies.evidence(quote.evidence, storage_start, hub.id + ":storage", end, role="tariff")
        unit_days = None
        if quote.basis == "per_unit_day":
            transport_service = need(transport_service, "container_storage_service", hub.id)
            units, _, _ = self.policies.units(transport_service, items)
            hours = dec((end-storage_start).total_seconds()) / 3600
            payable_hours = max(Decimal(0), hours-dec(hub.storage_free_hours))
            # The started-day convention must be disclosed in storage quote evidence.
            unit_days = units * math.ceil(payable_hours / 24)
        self.charge("storage", hub.id, storage_start, batch, quote, items, quote_id=hub.id + ":storage",
                    recorded_at=end, storage_tonne_hours=tonne_hours, component="storage", storage_unit_days=unit_days)

    def run_batch(self, legs, items, batch):
        nodes = [legs[0].from_hub] + [leg.to_hub for leg in legs]
        pickups = {item.key: item.spec.pickup_hub_id or self.request.origin_hub_id for item in items}
        destinations = {item.key: item.spec.delivery_hub_id or self.request.destination_hub_id for item in items}
        latest = max(item.ready + timedelta(hours=min(item.spec.max_delivery_hours or self.request.max_delivery_hours, item.spec.shelf_life_hours or self.request.max_delivery_hours)) for item in items)
        active = []
        arrival = min(item.ready for item in items if pickups[item.key] == nodes[0])
        previous = None
        for index, identifier in enumerate(nodes):
            hub = self.network.hubs[identifier]
            following = legs[index] if index < len(legs) else None
            delivered = [item for item in active if destinations[item.key] == identifier]
            transit = [item for item in active if destinations[item.key] != identifier]
            collected = [item for item in items if pickups[item.key] == identifier]
            visited = active + collected
            self.policies.acceptance(hub, visited, max(arrival, min((item.ready for item in collected), default=arrival)))
            current = arrival
            if delivered:
                current = self.operation(hub, hub.exit, delivered, current, latest, "delivery_handling", batch,
                                         included=previous.terminal_handling_included)
                self.storage(hub, delivered, {item.key: arrival for item in delivered}, current, batch, previous)
                for item in delivered:
                    hours = (current-item.ready).total_seconds()/3600
                    allowed = min(item.spec.max_delivery_hours or self.request.max_delivery_hours, item.spec.shelf_life_hours or self.request.max_delivery_hours)
                    if hours > allowed + 1e-9:
                        raise PlanningProblem("delivery_deadline", "Превышен срок доставки или годности исходного груза", object_id=item.key)
                    self.deliveries.append({"cargo_instance_id": item.key, "cargo_id": item.spec.id, "shipment": item.shipment, "weight_tonnes": float(item.mass), "ready_at": item.ready.isoformat(), "delivered_at": current.isoformat(), "delivery_hours": round(hours,6), "hub_id": identifier})
            if transit and following is not None:
                transfer = self.network.transfer(identifier, previous, following)
                if transfer is None:
                    raise PlanningProblem("missing_transfer", "Не подтверждён однозначный переход между услугами в этом хабе", "insufficient_data", identifier, [previous.id, following.id])
                self.policies.acceptance(transfer, transit, current)
                transfer_start = current
                current = self.operation(hub, transfer.operation, transit, current, latest, "transfer", batch)
                self.policies.evidence(transfer.evidence, transfer_start, transfer.id, current, role="transfer")
                self.steps[-1]["transfer_id"] = transfer.id
            if collected:
                current = max(current, max(item.ready for item in collected))
                current = self.operation(hub, hub.entry, collected, current, latest, "pickup_handling", batch,
                                         included=following is not None and following.terminal_handling_included)
            dwell = self.request.stop_dwell_hours.get(identifier, 0)
            if dwell:
                end = current + timedelta(hours=dwell)
                self.steps.append({"kind":"mandatory_stop", "hub_id":identifier, "start_at":current.isoformat(), "end_at":end.isoformat(), "dwell_hours":dwell, "batch":batch})
                current = end
            active = transit + collected
            if following is not None:
                if not active:
                    raise PlanningProblem("empty_stage", "Этап без груза требует отдельной модели порожнего перемещения", "insufficient_data", following.id)
                self.policies.compatible(active)
                departure, next_arrival = self.service(following, active, current, latest, batch)
                self.storage(hub, transit, {item.key: arrival for item in transit}, departure, batch, following)
                self.storage(hub, collected, {item.key: item.ready for item in collected}, departure, batch, following)
                previous, arrival = following, next_arrival
        if len([d for d in self.deliveries if d["cargo_instance_id"] in {item.key for item in items}]) != len(items):
            raise PlanningProblem("cargo_balance", "Не все исходные позиции доставлены; частичный объём не выдаётся за полный")


def calculate_network(payload, network=None, *, include_pricing=False):
    request = payload if isinstance(payload, NetworkRequest) else NetworkRequest.model_validate(payload)
    network = network or load_network()
    original_request, base_fingerprint = request, network.fingerprint
    request, network, cargo_resolution, clarifications = prepare_cargoes(request, network)
    events, _, _ = build_schedule(request.schedule)
    if len(events) > 64:
        raise ValueError("Сетевой расчёт: максимум 64 отправки, сократите период/частоту")
    paths, search = ([], {"explicit": False, "truncated": False, "expansions": 0, "scope": "awaiting_cargo_details"}) if clarifications else network.paths(request)
    total_mass = sum((dec(cargo.weight_tonnes) for cargo in request.cargoes), Decimal(0)) * len(events)
    candidates = []
    for legs in paths:
        for group_size in request.group_sizes:
            ids = [leg.id for leg in legs]
            identifier = hashlib.sha256(("|".join(ids) + ":" + str(group_size)).encode()).hexdigest()[:16]
            planner = Planner(network, request, include_pricing)
            result = {"id": identifier, "service_ids": ids, "modes": [leg.mode for leg in legs], "group_size": group_size, "multimodal": len({leg.mode for leg in legs}) > 1}
            try:
                for offset in range(0, len(events), group_size):
                    instances = [Item(f"{index}:{cargo.id}", cargo, events[index] + timedelta(hours=cargo.ready_offset_hours), index) for index in range(offset, min(offset+group_size,len(events))) for cargo in request.cargoes]
                    planner.run_batch(legs, instances, offset//group_size)
                cost = sum((dec(row["amount_rub"]) for row in planner.costs), Decimal(0))
                if sum((dec(row["weight_tonnes"]) for row in planner.deliveries), Decimal(0)) != total_mass:
                    raise PlanningProblem("mass_balance", "Не совпал баланс исходного и доставленного груза")
                if any(leg.cost_scope == "published_component" for leg in legs):
                    result["known_cost_subtotal_rub"] = float(money(sum(planner.published_costs, Decimal(0))))
                    result["missing_cost_components"] = sorted({field for leg in legs for field in leg.missing_cost_components})
                    raise PlanningProblem("partial_tariff", "Опубликован только компонент тарифа; полная стоимость не подтверждена", "insufficient_data", fields=result["missing_cost_components"])
                result.update(status="feasible", total_cost_rub=float(money(cost)), cost_rub_per_tonne=float(money(cost/total_mass)), max_delivery_hours=max(row["delivery_hours"] for row in planner.deliveries), issues=[])
            except PlanningProblem as problem:
                result.update(status=problem.issue["status"], issues=[problem.issue], total_cost_rub=None, cost_rub_per_tonne=None, max_delivery_hours=None)
            result.update(complete=result["status"]=="feasible",steps=planner.steps, deliveries=planner.deliveries, cost_items=planner.costs, source_ids=sorted(planner.policies.source_ids), evidence_levels=sorted(planner.policies.evidence_levels))
            candidates.append(result)
    eligible = [result for result in candidates if result["status"] == "feasible"]
    best = min(eligible, key=lambda row: (row["total_cost_rub"],row["max_delivery_hours"])) if eligible else None
    categories=[]
    for mode,label in CATEGORIES:
        relevant = [row for row in candidates if row["multimodal"]] if mode == "multimodal" else [row for row in candidates if mode in row["modes"]]
        possible = [row for row in relevant if row["status"] == "feasible"]
        preferred = min(possible, key=lambda row: (row["total_cost_rub"],row["max_delivery_hours"])) if possible else None
        categories.append({"id": mode, "name": label, "implemented": True, "candidate_count": len(relevant), "best_candidate_id": preferred["id"] if preferred else None, "status": "feasible" if preferred else "not_confirmed_in_loaded_network"})
    warnings = ["Отсутствие кандидата не доказывает отсутствие перевозки в России: проверена только загруженная сеть и заданные пределы поиска."]
    if request.schedule.mode == "regular":
        warnings.append("Готовность распределена равномерно внутри каждого заданного интервала; для точных дат используйте explicit")
    if request.capacity_basis == "mass_only":
        warnings.append("Недостающие объёмы не проверены; известные ограничения по объёму сохраняются. Авиация требует объём.")
    if any(level != "fact" for row in candidates for level in row["evidence_levels"]):
        warnings.append("В использованных записях есть оценочные/синтетические условия. Это не подтверждённая коммерческая услуга.")
    if search["truncated"]:
        warnings.append("Поиск ограничен: лучшая цена выбрана только среди рассчитанных кандидатов, глобальный оптимум не доказан.")
    return {"schema_version":"0.3","engine":"network","calculation_mode":"scenario","network_id":network.catalog.id,"network_fingerprint":base_fingerprint,"coverage":network.catalog.coverage,"request":original_request.model_dump(mode="json"),"shipments":len(events),"total_volume_tonnes":float(total_mass),"categories":categories,"candidates":candidates,"recommendation":best["id"] if best else None,"search":search,"assumptions":["Это сценарная оценка, не статистический прогноз и не бронирование мест у перевозчика","Остаток консолидации не отбрасывается; все исходные позиции должны быть доставлены","Обработка в одном посещении хаба последовательна: выгрузка, переход, приём нового груза","Ресурсы общие для всех отправок внутри кандидата, но независимы между альтернативами и запросами","Переупаковка и смена физического состояния не выполняются автоматически","Рабочие окна, сезонность, ледовые ограничения и время возврата ресурса берутся из данных, а не выводятся из общей статистики","Погодные и пассажирские показатели не используются как доказательство грузовой услуги"],"warnings":warnings,"sources":network.catalog.sources,"input_status":"needs_clarification" if clarifications else "ready","cargo_resolution":cargo_resolution,"clarifications":clarifications}
