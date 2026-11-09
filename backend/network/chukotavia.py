"""Published ChukotAVIA freight estimates; a timetable is never a cargo booking.

This provider adapter intentionally returns partial cost and conditional schedules.
It does not manufacture the missing admission, cut-off or free cargo capacity that
would be required to turn an observation into a confirmed graph Service.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import Field, StrictBool, model_validator

from backend.calculation_models import InputModel, Text, Positive, NonnegativeHours, Schedule, Hours
from backend.schedule import build_schedule


DATASET_PATH = Path(__file__).resolve().parents[1] / 'data_sources/chukotavia/dataset.json'
LOCAL = timezone(timedelta(hours=12))
LIMITATIONS = [
    'Перевозка дозагрузкой в свободный тоннаж без гарантированной даты; расписание не подтверждает место для груза.',
    'Цена ниже — только опубликованный грузовой тариф. Полная стоимость, обязательные сборы, обработка и автодоставка не подтверждены.',
    'Приём конкретного груза, упаковка, габариты мест и совместимость разных грузов требуют проверки.',
    'Срок от готовности до получения не подтверждён; плановое прибытие самолёта не равно выдаче груза.',
    'Снимок расписания действует только на опубликованные даты 2026 года; отмены и оперативные изменения не отслеживаются.',
]


class AirCargo(InputModel):
    id: Text
    name: Text
    weight_tonnes: Positive
    volume_m3: Positive
    packaging: Text
    max_piece_weight_kg: Optional[Positive] = None
    hazardous: Optional[StrictBool] = None
    temperature_control_required: Optional[StrictBool] = None


class AirScenario(InputModel):
    """Optional user assumptions, never published carrier rules."""
    cutoff_hours: NonnegativeHours
    origin_processing_hours: NonnegativeHours
    destination_processing_hours: NonnegativeHours


class ChukotaviaRequest(InputModel):
    engine: Literal['chukotavia'] = 'chukotavia'
    origin: Text
    destination: Text
    via: Annotated[List[Text], Field(max_length=8)] = Field(default_factory=list)
    cargoes: Annotated[List[AirCargo], Field(min_length=1, max_length=20)]
    schedule: Schedule
    max_delivery_hours: Hours
    cost_basis: Literal['including_vat', 'excluding_vat'] = 'including_vat'
    scenario: Optional[AirScenario] = None

    @model_validator(mode='after')
    def valid_items(self):
        if self.origin.casefold() == self.destination.casefold():
            raise ValueError('Отправление и назначение должны различаться')
        if len({c.id for c in self.cargoes}) != len(self.cargoes):
            raise ValueError('ID грузов должны быть уникальными')
        return self


class ChukotaviaResponse(InputModel):
    schema_version: str
    engine: Literal['chukotavia']
    dataset_fingerprint: str
    status: str
    request: dict
    overview: dict
    shipments: List[dict]
    recommendation: None = None
    sources: List[dict]
    limitations: List[str]


def normalized(value):
    value = ' '.join(value.casefold().replace('ё', 'е').replace('–', '-').replace('—', '-').split())
    return value.replace('усть белая', 'усть-белая')


def money(value):
    return float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def volumetric_weight_kg(volume_m3: Decimal) -> Decimal:
    return volume_m3 * Decimal(1000000) / Decimal(6000)


class ChukotaviaData:
    def __init__(self, path=DATASET_PATH):
        raw = Path(path).read_bytes()
        self.data = json.loads(raw)
        self.fingerprint = hashlib.sha256(raw).hexdigest()
        self.sources = {s['id']: s for s in self.data['sources']}
        self.tariffs = {}
        self.flights = {}
        for row in self.data['tariffs']:
            if row['source_id'] not in self.sources:
                raise ValueError('Unknown tariff source')
            net, gross = Decimal(row['rate_excluding_vat_rub_per_kg']), Decimal(row['rate_including_vat_rub_per_kg'])
            if net <= 0 or gross != net * Decimal('1.22'):
                raise ValueError('Invalid published tariff / VAT cross-check')
            key = (normalized(row['origin']), normalized(row['destination']))
            self.tariffs.setdefault(key, []).append(row)
        for row in self.data['flights']:
            dep, arr = datetime.fromisoformat(row['departure_at']), datetime.fromisoformat(row['arrival_at'])
            if arr <= dep or dep.year != 2026 or arr.year != 2026 or row['source_id'] not in self.sources:
                raise ValueError('Invalid timetable record')
            if row['flags']:
                continue
            key = (normalized(row['origin']), normalized(row['destination']))
            self.flights.setdefault(key, []).append(row)
        for key in self.flights:
            self.flights[key].sort(key=lambda f: (f['departure_at'], f['id']))

    def tariff(self, key, day):
        matches = [r for r in self.tariffs.get(key, []) if r['valid_from'] <= day and
                   (not r['valid_until_exclusive'] or day < r['valid_until_exclusive'])]
        if len(matches) > 1:
            raise ValueError('Пересекающиеся версии тарифа')
        return matches[0] if matches else None

    def options(self):
        routes = []
        for key, rows in sorted(self.tariffs.items()):
            routes.append({'origin': rows[0]['origin'], 'destination': rows[0]['destination'],
                           'published_schedule_segments': len(self.flights.get(key, [])),
                           'booking_confirmed': False})
        return {'engine': 'chukotavia', 'endpoint': '/network/aviation/calculations',
                'fingerprint': self.fingerprint, 'summary': self.data['summary'],
                'routes': routes, 'sources': list(self.sources.values()),
                'excluded_providers': ['aerogruz'], 'limitations': LIMITATIONS}


def calculate_chukotavia(payload, data=None):
    request = payload if isinstance(payload, ChukotaviaRequest) else ChukotaviaRequest.model_validate(payload)
    data = data or ChukotaviaData()
    dates, _, _ = build_schedule(request.schedule)
    if len(dates) > 64:
        raise ValueError('Авиационный адаптер: не более 64 отправок за расчёт')
    key = (normalized(request.origin), normalized(request.destination))
    physical = sum((Decimal(str(c.weight_tonnes)) * 1000 for c in request.cargoes), Decimal(0))
    volume = sum((Decimal(str(c.volume_m3)) for c in request.cargoes), Decimal(0))
    volumetric = volumetric_weight_kg(volume)
    chargeable = max(physical, volumetric)
    special = [c.id for c in request.cargoes if c.hazardous is True or c.temperature_control_required is True
               or (c.max_piece_weight_kg is not None and c.max_piece_weight_kg >= 80)]
    missing_cargo = [c.id for c in request.cargoes if c.hazardous is None or
                     c.temperature_control_required is None or c.max_piece_weight_kg is None]
    used = {'chukotavia-305', 'chukotavia-51'}
    shipments = []
    for n, ready in enumerate(dates, 1):
        candidates = []
        for flight in data.flights.get(key, []):
            dep = datetime.fromisoformat(flight['departure_at'])
            arr = datetime.fromisoformat(flight['arrival_at'])
            if dep <= ready:
                continue
            via = [normalized(v) for v in flight['via']]
            needed = [normalized(v) for v in request.via]
            cursor = 0
            for stop in via:
                if cursor < len(needed) and stop == needed[cursor]:cursor += 1
            if cursor < len(needed):continue
            if request.scenario:
                earliest = ready + timedelta(hours=request.scenario.origin_processing_hours)
                if earliest > dep - timedelta(hours=request.scenario.cutoff_hours):continue
                scenario_delivery = arr + timedelta(hours=request.scenario.destination_processing_hours)
                scenario_hours = (scenario_delivery-ready).total_seconds()/3600
            else:
                scenario_delivery, scenario_hours = None, None
            tariff = data.tariff(key, dep.astimezone(LOCAL).date().isoformat())
            used.add(flight['source_id'])
            rate, cost = None, None
            if tariff:
                used.add(tariff['source_id'])
                field = 'rate_including_vat_rub_per_kg' if request.cost_basis == 'including_vat' else 'rate_excluding_vat_rub_per_kg'
                rate = tariff[field]
                # Special cargo tariff/admission cannot be inferred from a general cargo table.
                if not special:cost = money(chargeable*Decimal(rate))
            candidates.append({'id': flight['id'], 'flight': flight['flight'], 'origin_code': flight['origin_code'],
                               'destination_code': flight['destination_code'], 'via': flight['via'],
                               'departure_at': flight['departure_at'], 'arrival_at': flight['arrival_at'],
                               'travel_minutes': flight['travel_minutes'], 'waiting_hours': (dep-ready).total_seconds()/3600,
                               'published_tariff': tariff, 'rate_rub_per_kg': rate,
                               'transport_component_estimate_rub': cost, 'total_cost_rub': None,
                               'arrival_is_cargo_delivery': False, 'booking_confirmed': False,
                               'capacity_kg': None, 'status': 'requires_special_handling' if special else ('conditional' if tariff else 'missing_tariff'),
                               'scenario_delivery_at': scenario_delivery.isoformat() if scenario_delivery else None,
                               'scenario_delivery_hours': scenario_hours,
                               'scenario_within_deadline': scenario_hours <= request.max_delivery_hours if scenario_hours is not None else None,
                               'deadline_confirmed': False, 'source_ids': [flight['source_id']] + ([tariff['source_id']] if tariff else [])})
            if len(candidates) == 8:break
        if special:status = 'requires_special_handling'
        elif not candidates:status = 'insufficient_data'
        elif all(c['rate_rub_per_kg'] is None for c in candidates):status = 'missing_tariff'
        else:status = 'conditional'
        shipments.append({'number': n, 'ready_at': ready.isoformat(), 'status': status,
                          'candidates': candidates, 'selection': None,
                          'missing': ['cargo_capacity', 'cargo_acceptance', 'mandatory_charge_scope', 'charge_rounding', 'minimum_charge_rule'] +
                                     ([] if request.scenario else ['cargo_cutoff', 'origin_processing', 'destination_processing']),
                          'cargo_details_to_clarify': missing_cargo,
                          'special_cargo_ids': special})
    return {'schema_version': '0.1', 'engine': 'chukotavia', 'dataset_fingerprint': data.fingerprint,
            'status': 'conditional' if any(s['status']=='conditional' for s in shipments) else 'insufficient_data',
            'request': request.model_dump(mode='json'),
            'overview': {'shipment_count': len(dates), 'weight_kg_per_shipment': float(physical),
                         'volume_m3_per_shipment': float(volume), 'volumetric_weight_kg_per_shipment': str(volumetric),
                         'chargeable_weight_kg_per_shipment': str(chargeable),
                         'volume_formula': 'volume_m3 * 1000000 / 6000', 'weight_rounding': 'not_assumed',
                         'planned_volume_tonnes': float(physical*len(dates)/1000),
                         'total_cost_rub': None, 'assumptions': request.scenario.model_dump() if request.scenario else {},
                         'cost_basis': request.cost_basis, 'all_candidates_booking_confirmed': False},
            'shipments': shipments, 'recommendation': None,
            'sources': [data.sources[s] for s in sorted(used)], 'limitations': LIMITATIONS}
