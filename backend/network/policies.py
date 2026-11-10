"""Правила шести физических видов. Коэффициенты — из услуг, а не константы рынка."""

from decimal import Decimal

from backend.transport_calculator import ceil_ratio, dec, money
from backend.network.models import Hub, Service
from backend.network.resources import PlanningProblem


def need(value, field, object_id):
    if value is None:
        raise PlanningProblem("missing_data", "Не хватает параметра: " + field, "insufficient_data", object_id, [field])
    return value


class Policies:
    def __init__(self, network, request):
        self.network, self.request = network, request
        self.source_ids, self.evidence_levels = set(), set()

    def evidence(self, evidence, at, object_id, until=None, role=None):
        self.source_ids.update(evidence.source_ids)
        self.evidence_levels.add(evidence.status)
        if evidence.status == "unknown":
            raise PlanningProblem("unverified_data", "Сведения не подтверждены", "insufficient_data", object_id)
        if self.request.data_policy == "verified_only" and evidence.status != "fact":
            raise PlanningProblem("evidence_policy", "Для verified_only нужны фактические данные, без подстановки предположений", "insufficient_data", object_id)
        if role and evidence.status != "synthetic":
            sources = {source["id"]: source for source in self.network.catalog.sources}
            if not any(role in sources[identifier].get("roles", []) for identifier in evidence.source_ids):
                raise PlanningProblem("source_scope", "Источник не подтверждает этот тип сведений: " + role, "insufficient_data", object_id, [role])
        if at < evidence.valid_from or at >= evidence.valid_until or (until is not None and until > evidence.valid_until):
            raise PlanningProblem("expired_evidence", "Сведения не действуют на нужную дату", "insufficient_data", object_id)

    def compatible(self, items):
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                a, b = left.spec.cargo_type, right.spec.cargo_type
                if a == b:
                    continue
                value = self.network.catalog.compatibility.get("|".join(sorted((a, b))))
                if value is None:
                    raise PlanningProblem("unknown_compatibility", "Нужно подтвердить совместимость грузов: «" + (left.spec.name or a) + "» и «" + (right.spec.name or b) + "»", "insufficient_data", fields=[left.spec.id, right.spec.id])
                if value is False:
                    raise PlanningProblem("incompatible_cargoes", "Нельзя объединить эти грузы в общую загрузку; раздельная перевозка здесь не оптимизируется", fields=[a, b])

    def acceptance(self, obj, items, at):
        role = "hub" if isinstance(obj, Hub) else ("freight_service" if isinstance(obj, Service) else "transfer")
        self.evidence(obj.evidence, at, obj.id, role=role)
        if hasattr(obj, "available"):
            available = need(obj.available, "available", obj.id)
            if not available:
                raise PlanningProblem("unavailable", "Хаб или услуга недоступны", object_id=obj.id)
        for item in items:
            spec = item.spec
            cargo_type = self.network.catalog.cargo_types[spec.cargo_type]
            self.evidence(cargo_type.evidence, at, spec.cargo_type, role="cargo_properties")
            if spec.packaging not in cargo_type.packagings:
                raise PlanningProblem("cargo_packaging_mismatch", "Упаковка не соответствует типу груза", object_id=spec.id)
            if spec.packaging not in obj.packagings:
                raise PlanningProblem("cargo_not_accepted", "Упаковка груза не принимается", object_id=obj.id)
            if spec.cargo_type not in obj.cargo_types and cargo_type.handling_profile not in obj.cargo_profiles:
                if spec.cargo_type.startswith("declared-"):
                    raise PlanningProblem("cargo_admission_unconfirmed", "Для введённого груза не подтверждён приём хабом/услугой; нужны сведения оператора", "insufficient_data", obj.id, ["cargo_types", "cargo_profiles"])
                raise PlanningProblem("cargo_not_accepted", "Этот тип груза не принимается", object_id=obj.id)
            if cargo_type.hazardous:
                if obj.hazardous_allowed is False:
                    raise PlanningProblem("hazardous_not_allowed", "Опасный груз не принимается", object_id=obj.id)
                raise PlanningProblem("dangerous_goods_documents", "Нужна отдельная проверка документов, упаковки и допуска опасного груза; текущая версия её не выполняет", "insufficient_data", obj.id)
            ranges = [value for value in (spec.temperature_c, cargo_type.required_temperature_c) if value is not None]
            required = (max(value[0] for value in ranges), min(value[1] for value in ranges)) if ranges else None
            if required is not None and required[0] > required[1]:
                raise PlanningProblem("conflicting_temperature", "Требования пользователя и типа груза по температуре несовместимы", object_id=spec.id)
            if required is not None:
                actual = need(getattr(obj, "temperature_c", None), "guaranteed_temperature_c", obj.id)
                if actual[0] < required[0] or actual[1] > required[1]:
                    raise PlanningProblem("temperature_mismatch", "Гарантируемый температурный диапазон не укладывается в требования груза", object_id=obj.id)

    def payload_capacity(self, service):
        capacity = dec(need(service.unit_tonnes, "unit_tonnes", service.id))
        if service.gross_limit_tonnes is not None:
            tare = dec(need(service.tare_tonnes, "tare_tonnes", service.id))
            capacity = min(capacity, dec(service.gross_limit_tonnes) - tare)
            if capacity <= 0:
                raise PlanningProblem("no_payload", "Полная масса и тара не оставляют грузоподъёмности", object_id=service.id)
        return capacity

    def units(self, service, items):
        mass = sum((item.mass for item in items), Decimal(0))
        volume = sum((item.volume for item in items), Decimal(0)) if all(item.volume is not None for item in items) else None
        if service.mode == "pipeline":
            return 1, mass, volume
        if service.require_volume:
            need(volume, "volume_m3 for container", service.id)
        capacity = self.payload_capacity(service)
        units = max(1, ceil_ratio(mass, capacity))
        if self.request.capacity_basis == "mass_and_volume" or (volume is not None and service.unit_m3 is not None):
            cap_volume = dec(need(service.unit_m3, "unit_m3", service.id))
            volume = need(volume, "volume_m3", service.id)
            units = max(units, ceil_ratio(volume, cap_volume))
        for item in items:
            if not item.spec.splittable:
                if item.mass > capacity or (item.volume is not None and service.unit_m3 is not None and item.volume > dec(service.unit_m3)):
                    raise PlanningProblem("oversized_indivisible", "Неделимый груз превышает вместимость транспортной единицы", object_id=item.spec.id)
                if units > 1:
                    raise PlanningProblem("load_plan_required", "Для неделимых мест в нескольких единицах нужен план укладки", "insufficient_data", service.id)
            if service.max_piece_tonnes is not None:
                piece = need(item.spec.max_piece_tonnes, "max_piece_tonnes", item.spec.id)
                if piece > service.max_piece_tonnes:
                    raise PlanningProblem("piece_too_heavy", "Отдельное грузовое место слишком тяжёлое", object_id=service.id)
            if service.max_piece_dimensions_m is not None:
                size = need(item.spec.max_piece_dimensions_m, "max_piece_dimensions_m", item.spec.id)
                limit = service.max_piece_dimensions_m
                if service.allow_rotation:
                    size, limit = sorted(size), sorted(limit)
                if any(value > maximum for value, maximum in zip(size, limit)):
                    raise PlanningProblem("piece_too_large", "Грузовое место не проходит по габаритам", object_id=service.id)
        return units, mass, volume

    def mode(self, service, items, at):
        self.acceptance(service, items, at)
        if service.allowed_phases and any(self.network.catalog.cargo_types[item.spec.cargo_type].phase not in service.allowed_phases for item in items):
            raise PlanningProblem("cargo_phase_not_allowed", "Физическое состояние груза не принимается этой услугой", object_id=service.id)
        self.compatible(items)
        mass = sum((item.mass for item in items), Decimal(0))
        if mass < dec(service.min_batch_tonnes):
            raise PlanningProblem("minimum_batch", "Недостаточная партия для этапа; остаток не исключается из расчёта", object_id=service.id)
        if service.mode == "rail":
            gauge = need(service.rail_gauge_mm, "rail_gauge_mm", service.id)
            for identifier in (service.from_hub, service.to_hub):
                gauges = need(self.network.hubs[identifier].rail_gauges_mm, "rail_gauges_mm", identifier)
                if gauge not in gauges:
                    raise PlanningProblem("gauge_mismatch", "Ж/д услуга несовместима с колеёй терминала", object_id=identifier)
        elif service.mode in ("sea", "river"):
            draft = need(service.vessel_draft_m, "vessel_draft_m", service.id)
            limit = need(service.max_draft_m, "max_draft_m", service.id)
            if draft > limit:
                raise PlanningProblem("draft_exceeded", "Осадка судна превышает подтверждённое ограничение маршрута/причалов", object_id=service.id)
            if service.max_air_draft_m is not None and need(service.vessel_air_draft_m, "vessel_air_draft_m", service.id) > service.max_air_draft_m:
                raise PlanningProblem("bridge_clearance", "Надводный габарит превышает ограничение", object_id=service.id)
            ice = need(service.ice_class_required, "ice_class_required", service.id)
            if ice and not need(service.ice_class_confirmed, "ice_class_confirmed", service.id):
                raise PlanningProblem("ice_class", "Не подтверждён требуемый ледовый допуск судна", object_id=service.id)
        elif service.mode == "air":
            need(service.unit_m3, "unit_m3 for air", service.id)
            need(service.max_piece_tonnes, "max_piece_tonnes", service.id)
            need(service.max_piece_dimensions_m, "max_piece_dimensions_m", service.id)
            for item in items:
                need(item.volume, "volume_m3 for air", item.spec.id)
        elif service.mode == "pipeline":
            commodity = {item.spec.cargo_type for item in items}
            if len(commodity) != 1 or not commodity.issubset(service.commodity_ids):
                raise PlanningProblem("pipeline_commodity", "Трубопровод не принимает этот продукт; разные продукты не смешиваются автоматически", object_id=service.id)
            for item in items:
                phase = self.network.catalog.cargo_types[item.spec.cargo_type].phase
                if phase not in ("liquid", "gas") or item.spec.packaging not in ("bulk_liquid", "bulk_gas"):
                    raise PlanningProblem("pipeline_form", "Нужен подходящий наливной/газовый продукт, а не упакованный твёрдый груз", object_id=service.id)
            need(service.flow_tonnes_per_hour, "flow_tonnes_per_hour", service.id)

    def price(self, quote, items, at, object_id, units=1, distance=None, storage_tonne_hours=None, mode=None, component="transport", storage_unit_days=None, breakdown=None):
        quote = need(quote, "quote", object_id)
        if quote.component != component:
            raise PlanningProblem("bundled_quote", "Нужен разложенный тариф без повторного начисления включённых услуг", "insufficient_data", object_id, ["quote.component"])
        self.evidence(quote.evidence, at, object_id + ":quote", role="tariff")
        mass = sum((item.mass for item in items), Decimal(0))
        volume = sum((item.volume for item in items), Decimal(0)) if all(item.volume is not None for item in items) else None
        if quote.basis == "per_unit":
            quantity = dec(units)
        elif quote.basis == "per_tonne":
            quantity = mass
        elif quote.basis == "per_100_tonnes":
            quantity = mass / 100
        elif quote.basis == "per_100_tonne_km":
            quantity = mass * dec(need(distance, "distance_km", object_id)) / 100
        elif quote.basis == "per_tonne_km":
            quantity = mass * dec(need(distance, "distance_km", object_id))
        elif quote.basis == "per_tonne_hour":
            quantity = dec(need(storage_tonne_hours, "storage_tonne_hours", object_id))
        elif quote.basis == "per_unit_day":
            quantity = dec(need(storage_unit_days, "storage_unit_days", object_id))
        elif quote.basis == "per_kg":
            quantity = mass * 1000
            if quote.weight_tariff is not None:
                step = dec(quote.weight_tariff.actual_weight_step_kg)
                quantity = ceil_ratio(quantity, step) * step
            if mode == "air" or quote.volumetric_kg_per_m3 is not None:
                coefficient = dec(need(quote.volumetric_kg_per_m3, "volumetric_kg_per_m3", object_id))
                quantity = max(quantity, dec(need(volume, "volume_m3", object_id)) * coefficient)
        else:
            coefficient = dec(need(quote.tonnes_per_m3, "tonnes_per_m3", object_id))
            quantity = max(mass, dec(need(volume, "volume_m3", object_id)) * coefficient)
        if quote.weight_tariff is None:
            rate = dec(need(quote.rate_rub, "rate_rub", object_id))
        else:
            bands = quote.weight_tariff.bands
            rate = dec(bands[max(weight for weight in bands if dec(weight) <= quantity)])
            quantity = max(quantity, dec(quote.weight_tariff.minimum_kg))
        result = max(dec(quote.minimum_rub), rate * quantity) + dec(quote.fixed_rub)
        if quote.tax_basis == "unknown":
            raise PlanningProblem("unknown_tax_basis", "Не определён состав налога в тарифе", "insufficient_data", object_id)
        if quote.tax_basis != self.request.cost_basis:
            tax = dec(need(quote.tax_rate, "tax_rate", object_id))
            result = result * (1 + tax) if self.request.cost_basis == "including_vat" else result / (1 + tax)
        if breakdown is not None:
            breakdown.update(
                category=mode if component == "transport" else component,
                basis=quote.basis, quantity=float(quantity), rate_rub=float(rate),
                minimum_rub=quote.minimum_rub, fixed_rub=quote.fixed_rub,
                rate_tax_basis=quote.tax_basis, tax_rate=quote.tax_rate,
                base_year=quote.evidence.valid_from.year,
                valid_from=quote.evidence.valid_from.isoformat(),
                valid_until=quote.evidence.valid_until.isoformat(),
                source_ids=list(quote.evidence.source_ids),
            )
        return money(result)
