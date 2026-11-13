"""Settlement-level planning over a versioned national observation graph.

Observations, physical references, published services and assumed freight rules
remain distinct. Only a bounded candidate subgraph is materialized per request.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import heapq
import itertools
import json
from functools import cached_property
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import Field, model_validator

from backend.calculation_models import InputModel, Text, Schedule, Hours, NonnegativeHours
from backend.network.air_tariff import YAKUTSK, bundled_air_tariff, latest_air_snapshot, read_air_snapshot, yakutsk_today
from backend.network.cargo import normalized_name
from backend.network.coverage_catalog import merge_settlements, settlement_catalog
from backend.network.engine import calculate_network
from backend.network.graph import FreightNetwork
from backend.network.models import NetworkCargo, NetworkRequest, Mode
from backend.network.published_land import calculate_published, index_tariffs
from backend.network.provider_tariffs import (
    AIR_SOURCE, LOGISTIC_AVTO_SOURCE, RAIL_SOURCE, ROAD_SOURCE, SEA_EQUIPMENT_SOURCE, SEA_SOURCE,
    SIBIR_RAIL_SOURCE, logistic_avto_corridors, logistic_avto_tariffs, observations, rate_evidence, sibir_rail_tariffs,
    source as tariff_source,
)
from backend.network.river_tariffs import river_catalog
from backend.network.tlm_tariff import (
    MOSCOW, ROAD_URL, bundled_road_tariff, bundled_tariff, latest_snapshot, moscow_today, read_snapshot, snapshot_dir,
)
from backend.schedule import build_schedule

ROOT = Path(__file__).resolve().parents[1]
ID = 'russia-freight-2026-v1'
SCENARIO = 'russia-freight-assumptions-v1'
START = '2026-01-01T00:00:00+00:00'
END = '2027-01-01T00:00:00+00:00'
MODES = ('road','rail','sea','river','air','pipeline')


def ev(status='synthetic', sources=None):
    return dict(source_ids=sources or [SCENARIO], status=status, observed_on='2026-11-08', valid_from=START, valid_until=END)


def quote(component, rate, basis='per_tonne', evidence=None, tax_basis='excluding_vat', tax_rate=.22):
    return dict(component=component, basis=basis, rate_rub=rate, tax_basis=tax_basis, tax_rate=tax_rate, evidence=evidence or ev())


class SettlementStop(InputModel):
    settlement: Text
    action: Optional[Literal['visit','pickup','delivery','pickup_delivery']] = None
    pickup_cargo_ids: List[Text] = Field(default_factory=list)
    delivery_cargo_ids: List[Text] = Field(default_factory=list)
    dwell_hours: NonnegativeHours = 0
    no_reload: bool = False

    @model_validator(mode='after')
    def correct_action(self):
        if self.no_reload and self.action not in (None, 'visit'):
            raise ValueError('Остановка без перегрузки несовместима с забором или выгрузкой груза')
        if self.action is None:
            return self
        pickup, delivery = bool(self.pickup_cargo_ids), bool(self.delivery_cargo_ids)
        if ((self.action == 'visit' and (pickup or delivery)) or
            (self.action == 'pickup' and (not pickup or delivery)) or
            (self.action == 'delivery' and (not delivery or pickup)) or
            (self.action == 'pickup_delivery' and not (pickup and delivery))):
            raise ValueError('Действие остановки должно соответствовать спискам погружаемых и выгружаемых позиций')
        return self


class RussiaRequest(InputModel):
    engine: Literal['russia'] = 'russia'
    origin: Text
    destination: Text
    stops: Annotated[List[SettlementStop], Field(max_length=8)] = Field(default_factory=list)
    cargoes: Annotated[List[NetworkCargo], Field(min_length=1, max_length=20)]
    schedule: Schedule
    max_delivery_hours: Hours
    mass_basis: Optional[Literal['gross','net']] = None
    capacity_basis: Literal['mass_only','mass_and_volume'] = 'mass_and_volume'
    cost_basis: Literal['including_vat','excluding_vat'] = 'including_vat'
    data_policy: Literal['verified_only','allow_assumptions'] = 'allow_assumptions'
    allowed_modes: Annotated[List[Mode], Field(min_length=1, max_length=6)] = Field(default_factory=lambda: list(MODES))
    max_legs: Annotated[int, Field(strict=True, ge=1, le=12)] = 8
    max_candidates: Annotated[int, Field(strict=True, ge=1, le=16)] = 8

    @model_validator(mode='after')
    def identities(self):
        if len({c.id for c in self.cargoes}) != len(self.cargoes):
            raise ValueError('ID грузовых позиций должны быть уникальными')
        if len(set(self.allowed_modes)) != len(self.allowed_modes):
            raise ValueError('Повтор вида транспорта')
        if any(c.pickup_hub_id or c.delivery_hub_id for c in self.cargoes):
            raise ValueError('Для расчёта по населённым пунктам задавайте погрузку/выгрузку в stops; хабы выбирает сервер')
        return self



class RussiaResponse(InputModel):
    schema_version: Literal['1.0']
    engine: Literal['russia']
    network_id: str
    network_fingerprint: str
    request: dict
    coverage: dict
    settlements: List[Optional[str]]
    clarifications: List[dict]
    candidates: List[dict]
    recommendation: Optional[str]
    sources: List[dict]
    input_status: Literal['ready','needs_clarification']
    warnings: List[str]
    shipments: int
    status: str
    search: dict
    stops: List[dict]
    selected_subgraph: Optional[dict]
    published_coverage: Optional[dict] = None


def digest(data):
    fragments = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(',', ':')).iterencode(data)
    checksum = hashlib.sha256()
    while chunk := ''.join(itertools.islice(fragments, 8192)):
        checksum.update(chunk.encode())
    return checksum.hexdigest()


class RussiaNetwork:
    def __init__(self, snapshot=None, *, tlm_tariff=None, tlm_road_tariff=None, air_tariff=None, registry=None):
        self.tlm_tariff = tlm_tariff if tlm_tariff is not None else bundled_tariff()
        self.tlm_road_tariff = tlm_road_tariff if tlm_road_tariff is not None else bundled_road_tariff()
        self.air_tariff = air_tariff if air_tariff is not None else bundled_air_tariff()
        self.snapshot = snapshot or json.loads((ROOT/'data_sources/russia/connectivity.json').read_text())
        self.settlements = {c['id']:dict(c, provenance='source-01') for c in self.snapshot['settlements']}
        self.gar_aliases = {}
        self.by_name = defaultdict(list)
        for city in self.settlements.values():
            self.by_name[normalized_name(city['name'])].append(city['id'])
        self.hubs, self.edges, self.outgoing = {}, {}, defaultdict(list)
        self.sources = {self.snapshot['source']['id']: self.snapshot['source'], SCENARIO:{'id':SCENARIO,'status':'synthetic',
            'roles':['hub','handling','transfer','freight_service','cargo_properties','tariff'],
            'name':'Явная модель грузовой перевозки поверх наблюдений о связности',
            'parameters':{'road':{'vehicle_tonnes':20,'vehicle_m3':82,'rub_per_vehicle_km':90,'minimum_vehicle_rub':5000,'passenger_time_multiplier':1.5},
                          'rail':{'wagon_tonnes':60,'wagon_m3':120,'rub_per_tonne_km':3,'minimum_rub':15000,'passenger_time_multiplier':1.8,'dispatch_interval_hours':24},
                          'air_observation':{'unit_tonnes':1,'unit_m3':6,'rub_per_kg':450,'minimum_rub':10000,'interval_hours':24},
                          'local_road_connector':{'distance_km':10,'travel_hours':1,'status':'synthetic'},
                          'handling':{'fixed_hours':1,'tonnes_per_hour':20,'rub_per_tonne':200,'lanes':2},
                          'storage':{'tonnes':10000,'m3':50000,'maximum_hours':720,'rub_per_tonne_hour':1},
                          'transfer':{'same_mode_fixed_hours':1,'different_mode_fixed_hours':4,'rub_per_tonne':300},
                          'capacity':'8 units per departure or fleet; independent model, not bookable capacity',
                          'price_scope':'scenario includes modelled movement/handling/storage; no carrier quote',
                          'vat':'.22 scenario assumption except published inclusive ChukotAVIA and river tariffs'},
            'notes':['ТЛЦ/склад и его ёмкость — сценарные, а не подтверждение существования комплекса.',
                     'Связность пассажирских поездок не подтверждает грузовое сообщение.',
                     'Нет новых морских/речных соединений по одной лишь близости портов.']}}
        self.cargo_types = {
            'flour':{'name':'Мука','aliases':['пшеничная мука'],'phase':'solid','hazardous':False,'handling_profile':'dry_food','packagings':['bags','boxes'],'evidence':ev()},
            'dry_general':{'name':'Сухой неопасный груз','aliases':[],'phase':'solid','hazardous':False,'handling_profile':'dry_general','packagings':['boxes','bags'],'evidence':ev()},
            'crude_oil':{'name':'Нефть','aliases':['сырая нефть','товарная нефть'],'phase':'liquid','hazardous':True,'handling_profile':'crude_oil','packagings':['bulk_liquid'],'evidence':ev()},
        }
        # Standard packaged goods for scenario estimates. Match only exact names and aliases.
        for identifier, name, aliases, profile, packagings in [
            ('sugar', 'Сахар', ['сахарный песок'], 'dry_food', ['bags', 'boxes']),
            ('rice', 'Рис', [], 'dry_food', ['bags', 'boxes']),
            ('pasta', 'Макароны', ['макаронные изделия'], 'dry_food', ['bags', 'boxes']),
            ('paper', 'Бумага', ['офисная бумага'], 'dry_general', ['boxes']),
            ('books', 'Книги', ['книга', 'книг'], 'dry_general', ['boxes']),
            ('pencils', 'Карандаши', ['карандаш', 'карандашей', 'простые карандаши', 'цветные карандаши'], 'dry_general', ['boxes']),
            ('condoms', 'Презервативы', ['презерватив'], 'dry_general', ['boxes']),
            ('ceramic_vases', 'Керамические вазы', ['керамическая ваза'], 'dry_general', ['boxes']),
        ]:
            self.cargo_types[identifier] = {
                'name':name, 'aliases':aliases, 'phase':'solid', 'hazardous':False,
                'handling_profile':profile, 'packagings':packagings, 'evidence':ev(),
            }
        self.coverage_notes = []
        self.reference_ports = json.loads((ROOT/'data_sources/rosmorport/references.json').read_text())['ports']
        # A co-located road/rail hub is a model assumption. No station is inferred from coordinates.
        for city in self.snapshot['settlements']:
            self.add_hub('tlc:'+city['id'], city['id'], ['road','rail'], 'logistics_hub')
        available, missing = Counter(), Counter()
        for row in self.snapshot['connections']:
            a,b,mode,km,hours,reason = row
            if km is None or hours is None or reason or km <= 0 or hours <= 0:
                missing[mode] += 1
                continue
            available[mode] += 1
            if mode == 'air':
                origin,target = 'air:'+a,'air:'+b
                for identifier,city in ((origin,a),(target,b)):
                    if identifier not in self.hubs:
                        self.add_hub(identifier,city,['road','air'],'airport')
                        self.connector('tlc:'+city,identifier)
            else:
                origin,target='tlc:'+a,'tlc:'+b
            identifier=f'observation:{mode}:{a}:{b}'
            # Compact lazy descriptor; operational Service is created only for selected paths.
            self.add_edge({'id':identifier,'from_hub':origin,'to_hub':target,'mode':mode,'kind':'observation',
                           'distance_km':km,'observed_hours':hours,'travel_hours':hours*({'road':1.5,'rail':1.8,'air':1}[mode]),
                           'reference_metadata':{'source_id':'source-01','observation_kind':'passenger_connectivity','freight_confirmed':False}})
        self.observation_counts = {'usable':dict(available),'missing_or_unusable':dict(missing),'rows':len(self.snapshot['connections'])}
        self.load_river()
        self.load_aviation()
        self.load_yakutia_tariffs()
        self.load_fesco()
        self.load_pipeline()
        self.load_land_tariffs()
        self.legacy_names = {name: tuple(ids) for name, ids in self.by_name.items()}
        self.registry = registry
        if registry is not None:
            self.gar_aliases = merge_settlements(self.settlements, registry["settlements"])
            for city in registry["settlements"]:
                if city["id"] not in self.gar_aliases:
                    self.by_name[normalized_name(city["name"])].append(city["id"])
            self.sources[registry["source"]["id"]] = registry["source"]
            if registry.get("census"):
                self.sources[registry["census"]["id"]] = registry["census"]
        self._settlement_search = sorted(
            ((normalized_name(city["name"]), city) for city in self.settlements.values()),
            key=lambda row: (row[0], row[1]["id"]),
        )
        self.incoming = defaultdict(list)
        for edge in self.edges.values():self.incoming[edge["to_hub"]].append(edge)
        self.fingerprint = digest({'model_revision':'2026-11-08.1','snapshot':self.snapshot,'edges':self.edges,'hubs':self.hubs,'sources':self.sources,'pipeline':self.pipeline,'reference_ports':self.reference_ports,'registry_regions':registry["regions"] if registry else None})
        for edges in self.outgoing.values():
            edges.sort(key=lambda edge:edge['id'])

    def add_settlement(self, name, source='provider_reference'):
        matches=self.by_name.get(normalized_name(name),[])
        if len(matches)==1:return matches[0]
        identifier='ref-'+hashlib.sha256(normalized_name(name).encode()).hexdigest()[:14]
        if identifier not in self.settlements:
            self.settlements[identifier]={'id':identifier,'name':name,'region':None,'latitude':None,'longitude':None,'provenance':source,'registry_verification':'provider_reference_not_municipal_registry'}
            self.by_name[normalized_name(name)].append(identifier)
        return identifier

    def add_hub(self, identifier, settlement, modes, kind, name=None, profiles=None, hazardous=False, reference_ids=None):
        if identifier in self.hubs:return
        city=self.settlements[settlement] if settlement is not None else {'name':'Населённый пункт не установлен'}
        self.hubs[identifier]={'id':identifier,'settlement_id':settlement,'name':name or ('Сценарный '+('ТЛЦ авто/ж/д' if kind=='logistics_hub' else kind)+' — '+city['name']),
            'city':city['name'],'kind':kind,'identity_status':'scenario','reference_ids':reference_ids or [],'available':True,
            'modes':modes,'rail_gauges_mm':[1520] if 'rail' in modes else None,'cargo_types':['crude_oil'] if hazardous else ['flour','dry_general'],
            'cargo_profiles':profiles or (['crude_oil'] if hazardous else ['dry_food','dry_general']),
            'packagings':['bulk_liquid'] if hazardous else ['bags','boxes'],'hazardous_allowed':hazardous,
            'windows':[{'start':START,'end':END}],'storage_tonnes':10000,'storage_m3':50000,'max_storage_hours':720,
            'storage_quote':quote('storage',1,'per_tonne_hour'), 'evidence':ev()}
        for side in ('entry','exit'):
            self.hubs[identifier][side]={'fixed_hours':1,'tonnes_per_hour':20,'resource_id':identifier+':'+side,'lanes':2,'quote':quote('handling',200),'evidence':ev()}

    def add_edge(self, edge):
        if edge['id'] in self.edges:raise ValueError('Duplicate connection ID: '+edge['id'])
        if edge['from_hub'] not in self.hubs or edge['to_hub'] not in self.hubs:raise ValueError('Connection without hubs')
        self.edges[edge['id']]=edge;self.outgoing[edge['from_hub']].append(edge)

    def connector(self, left, right):
        # Same-locality drayage is an explicit assumption, never a free teleport.
        if self.hubs[left]['settlement_id'] != self.hubs[right]['settlement_id']:raise ValueError('Local connector crosses settlements')
        for a,b in ((left,right),(right,left)):
            self.add_edge({'id':'connector:'+a+':'+b,'from_hub':a,'to_hub':b,'mode':'road','kind':'connector','distance_km':10,'travel_hours':1})

    def ensure_tlc(self, settlement):
        identifier='tlc:'+settlement
        self.add_hub(identifier,settlement,['road','rail'],'logistics_hub')
        return identifier

    def register_source(self, source, roles=None):
        value=deepcopy(source)
        if roles:value['roles']=roles
        existing=self.sources.get(value['id'])
        if existing is not None and existing != value:raise ValueError('Source ID collision')
        self.sources[value['id']]=value

    def load_river(self):
        data=river_catalog()
        for source in data['sources']:self.register_source(source)
        for hub in data['hubs']:
            city=self.add_settlement(hub['city'],'river-terminal-reference')
            copy=deepcopy(hub);copy['settlement_id']=city;copy['identity_status']='scenario'
            copy['cargo_types']=['flour','dry_general'];copy['cargo_profiles']=['dry_general','dry_food']
            self.hubs[copy['id']]=copy;self.connector(self.ensure_tlc(city),copy['id'])
        for service in data['services']:
            service=deepcopy(service);service['cargo_types']=['flour','dry_general'];service['cargo_profiles']=['dry_general','dry_food']
            self.add_edge(dict(service,kind='service',service=service))

    def load_aviation(self):
        from backend.network.chukotavia import ChukotaviaData, normalized
        provider=ChukotaviaData()
        for source in provider.sources.values():self.register_source(source,['tariff','schedule_reference'])
        names={normalized(name):name for row in provider.data['tariffs'] for name in (row['origin'],row['destination'])}
        hubs={}
        for key,name in names.items():
            if key=='окружной':
                identifier='air-point:okruzhnoy'
                self.add_hub(identifier,None,['air'],'airport',name='Посадочная площадка «Окружной»: привязка к населённому пункту не подтверждена',reference_ids=['chukotavia-51'])
                hubs[key]=identifier
                continue
            city=self.add_settlement(name,'chukotavia-locality-reference');identifier='air:'+city
            self.add_hub(identifier,city,['road','air'],'airport',reference_ids=['chukotavia-51'])
            if not any(e['to_hub']==identifier for e in self.outgoing[self.ensure_tlc(city)]):self.connector('tlc:'+city,identifier)
            hubs[key]=identifier
        for pair,flights in provider.flights.items():
            if pair[0] not in hubs or pair[1] not in hubs:continue
            # One service per dated flight: duration/tariff changes cannot be merged.
            for flight in flights:
                dep=datetime.fromisoformat(flight['departure_at']);arr=datetime.fromisoformat(flight['arrival_at'])
                tariff=provider.tariff(pair,dep.date().isoformat())
                if tariff is None:continue
                evidence=ev('fact',[tariff['source_id']]);evidence['valid_from']=tariff['valid_from']+'T00:00:00+12:00'
                evidence['valid_until']=(tariff['valid_until_exclusive'] or '2027-01-01')+'T00:00:00+12:00'
                self.add_edge({'id':'chukotavia:'+flight['id'],'from_hub':hubs[pair[0]],'to_hub':hubs[pair[1]],'mode':'air','kind':'chukotavia',
                    'travel_hours':(arr-dep).total_seconds()/3600,'departure_at':dep.isoformat(),
                    'quote':quote('transport',float(tariff['rate_including_vat_rub_per_kg']),'per_kg',evidence,'including_vat',.22),
                    'reference_metadata':{'flight':flight,'tariff_id':tariff.get('id'),'booking_confirmed':False},
                    'cost_scope':'published_component','missing_cost_components':['mandatory_airport_fees','carrier_admission','available_cargo_capacity']})

    def load_fesco(self):
        path=ROOT/'data_sources/russia/fesco-voyages.json'
        data=json.loads(path.read_text())
        for source in data['sources']:self.register_source(source)
        for hub in data['hubs']:
            city=self.add_settlement(hub['settlement_name'],'fesco-terminal-reference')
            self.add_hub(hub['id'],city,['road','sea'],'seaport',name=hub['name']+' — сценарные операции',reference_ids=[hub['source_id']])
            self.connector(self.ensure_tlc(city),hub['id'])
        collected_on, records, documents, _ = observations()
        tariff = records['fesco-fpkl-container-cy-cy']
        self.register_source(tariff_source(SEA_SOURCE, 'FESCO ФПКЛ — тариф CY–CY', documents[tariff['source']]))
        self.register_source({'id':SEA_EQUIPMENT_SOURCE, 'name':'FESCO — характеристики контейнера 20’DC',
                              'url':documents['sea-fesco-equipment.html'], 'status':'fact', 'roles':['equipment_reference']})
        published_rate = next(row[1] for row in tariff['rows'] if row[0] == 'carrier_20_dry')
        published = quote('transport', published_rate, 'per_unit', rate_evidence(
            SEA_SOURCE, collected_on, tariff['valid_from']+'T00:00:00+10:00', END), 'including_vat')
        for row in data['voyages']:
            if row['id'].startswith('fesco-fpkl:') and row['departure_at'][:10] >= tariff['valid_from']:
                row.update(quote=published, terminal_handling_included=True)
                row['reference_metadata'].update(movement_tariff='published', tariff_source_id=SEA_SOURCE,
                                                 container_size_ft=20)
            dep=datetime.fromisoformat(row['departure_at']);arr=datetime.fromisoformat(row['arrival_at'])
            self.add_edge(dict(row,mode='sea',kind='fesco',travel_hours=(arr-dep).total_seconds()/3600))
        self.coverage_notes.extend(data['limitations'])

    def load_yakutia_tariffs(self):
        data = self.air_tariff
        self.register_source(tariff_source(AIR_SOURCE, 'Якутия — внутренние грузовые тарифы',
                                           data['source_url']))
        evidence = rate_evidence(AIR_SOURCE, data['observed_on'],
                                 data['valid_from']+'T00:00:00+09:00', data['valid_until']+'T00:00:00+09:00')
        aliases = {'С.-Петербург':'Санкт-Петербург','Мин. Воды':'Минеральные Воды','Улан–Удэ':'Улан-Удэ'}
        for origin, destination, rates in data['federal_rows']:
            start, end = (self.resolve(aliases.get(name, name)) for name in
                          (origin.rsplit(' (',1)[0], destination.rsplit(' (',1)[0]))
            base = self.edges.get(f'observation:air:{start}:{end}')
            if base is None:
                continue
            published = quote('transport', None, 'per_kg', evidence, 'including_vat', None)
            published.update(fixed_rub=data['awb_rub'], volumetric_kg_per_m3=1_000_000/data['cubic_cm_per_kg'],
                             weight_tariff={'minimum_kg':data['minimum_weight_kg'],
                                 'actual_weight_step_kg':data['actual_weight_step_kg'],
                                 'bands':dict(zip(data['breakpoints_kg'],rates))})
            self.add_edge(dict(base,id=f'yakutia-air:{start}:{end}',kind='provider_air',quote=published,
                cost_scope='published_component',missing_cost_components=['agent_fee','other_terminal_services'],
                reference_metadata={**base['reference_metadata'],'tariff_source_id':AIR_SOURCE,
                                    'published_airports':[origin,destination],'scope':'airline_and_awb_only',
                                    'tariff_valid_until':evidence['valid_until'],
                                    'tariff_version':data['version'], 'tariff_observed_on':data['observed_on']}))

    def load_land_tariffs(self):
        _collected_on, _records, documents, _road = observations()
        road = self.tlm_road_tariff
        road_tariffs = (
            (ROAD_SOURCE, 'ТЛМ — цены на выделенную фуру из Москвы',
             road | {'url': ROAD_URL, 'origin': 'Москва', 'rub_per_vehicle': dict(road['rows'])}),
            (LOGISTIC_AVTO_SOURCE, 'Логистик Авто — ориентир за автомобиль 20 т', logistic_avto_tariffs()),
        )
        road_sources = [
            (source_id, title, tariff, [(tariff['origin'], destination, rate)
                                       for destination, rate in tariff['rub_per_vehicle'].items()])
            for source_id, title, tariff in road_tariffs
        ]
        corridors = logistic_avto_corridors()
        road_sources.extend(
            (source['id'], 'Логистик Авто — ориентир за автомобиль 20 т', corridors | {'url':source['url']}, source['rows'])
            for source in corridors['sources']
        )
        for source_id, title, tariff, rows in road_sources:
            self.register_source(tariff_source(source_id, title, tariff['url'], 'market_reference'))
            start, end = START, END
            if source_id == ROAD_SOURCE:
                # The publisher supplies a year. The snapshot selection separately
                # prevents using a revision before the day it was observed.
                start = tariff['valid_from'] + 'T00:00:00+00:00'
                end = f"{int(tariff['valid_from'][:4])+1}-01-01T00:00:00+00:00"
            road_evidence = rate_evidence(source_id, tariff['observed_on'], start, end, 'market_reference')
            for origin_name, destination, rate in rows:
                origin = self.resolve(origin_name)
                city = self.resolve(destination)
                edge = self.edges.get(f'observation:road:{origin}:{city}')
                if edge is not None and 'quote' not in edge:
                    edge['quote'] = quote('transport', rate, 'per_unit', road_evidence, 'including_vat',
                                          tariff['vat_percent'] / 100)
                    edge['reference_metadata'].update(movement_tariff='market_reference', tariff_source_id=source_id)
                    if source_id == ROAD_SOURCE:
                        edge['reference_metadata'].update(
                            tariff_version=tariff['version'], tariff_observed_on=tariff['observed_on'],
                            tariff_date_precision='year')

        origin = self.resolve('Москва')

        rail = self.tlm_tariff
        self.register_source(tariff_source(RAIL_SOURCE, 'ТЛМ — ж/д тарифы от станции до станции',
                                    rail.get('source_url') or documents[rail['source']]))
        rail_evidence = rate_evidence(RAIL_SOURCE, rail['observed_on'],
                                      rail['valid_from']+'T00:00:00+03:00',
                                      f"{int(rail['valid_from'][:4])+1}-01-01T00:00:00+00:00")
        for destination, rate20, rate40, guard20, guard40, days in rail['rows']:
            if days.endswith('+'):  # The island service has an unverified non-rail leg.
                continue
            city = self.resolve('Улан-Удэ' if destination == 'Тальцы (Улан-Удэ)' else destination)
            if city is None:
                continue
            base = self.edges.get(f'observation:rail:{origin}:{city}')
            for size, rate, guard, capacity, volume in (
                (20, rate20, guard20, 18, 28), (40, rate40, guard40, 24, 58),
            ):
                identifier = f'tlm-rail:{origin}:{city}:{size}'
                # The carrier publishes the station-to-station rate and transit time.
                # Equipment limits and frequency retain conservative scenario values.
                self.add_edge({'id':identifier, 'from_hub':'tlc:'+origin, 'to_hub':'tlc:'+city,
                               'mode':'rail', 'kind':'provider_rail', 'travel_hours':float(days)*24,
                               'distance_km':base['distance_km'] if base else None,
                               'quote':quote('transport', rate, 'per_unit', rail_evidence, 'including_vat', rail['vat_percent']/100),
                               'unit_tonnes':capacity, 'unit_m3':volume, 'terminal_handling_included':True,
                               'reference_metadata':{'tariff_source_id':RAIL_SOURCE, 'container_size_ft':size,
                                   'station_destination':destination, 'optional_security_rub_per_container':guard,
                                   'scope':'station_to_station', 'transit_days':float(days),
                                   'tariff_version':rail['version'], 'tariff_observed_on':rail['observed_on']}})
        self.load_sibir_rail_tariffs()

    def load_sibir_rail_tariffs(self):
        tariff = sibir_rail_tariffs()
        self.register_source(tariff_source(SIBIR_RAIL_SOURCE, 'Сибирь Контейнер — ставки контейнерной перевозки',
                                           tariff['url'], 'market_reference'))
        evidence = rate_evidence(SIBIR_RAIL_SOURCE, tariff['observed_on'], START, END, 'market_reference')
        for origin_name, destination, rate20, rate40, min_days, max_days in tariff['rows']:
            origin, city = self.resolve(origin_name), self.resolve(destination)
            start, end = 'tlc:'+str(origin), 'tlc:'+str(city)
            if start not in self.hubs or end not in self.hubs:
                continue
            base = self.edges.get(f'observation:rail:{origin}:{city}')
            for size, rate, capacity, volume in ((20, rate20, 18, 28), (40, rate40, 24, 58)):
                if rate is None:
                    continue
                self.add_edge({'id':f'sibir-rail:{origin}:{city}:{size}', 'from_hub':start, 'to_hub':end,
                               'mode':'rail', 'kind':'provider_rail', 'travel_hours':max_days*24,
                               'distance_km':base['distance_km'] if base else None,
                               'quote':quote('transport', rate, 'per_unit', evidence, 'including_vat', None),
                               'unit_tonnes':capacity, 'unit_m3':volume,
                               'reference_metadata':{'tariff_source_id':SIBIR_RAIL_SOURCE, 'container_size_ft':size,
                                   'scope':'container_linehaul', 'transit_days_range':[min_days, max_days]}})

    def load_pipeline(self):
        self.pipeline=json.loads((ROOT/'data_sources/pipeline/tariffs.json').read_text())
        self.pipeline['destination_point_reference']=json.loads((ROOT/'data_sources/pipeline/document_review.json').read_text())
        for source in self.pipeline['sources']:self.register_source(source)
        # Isolated commercial points are retained even when locality mapping is unresolved.
        for point in self.pipeline['points']:
            city=self.add_settlement(point['settlement_name'], 'pipeline-point-reference') if point['settlement_name'] else None
            self.add_hub('pipe:'+point['id'],city,['pipeline'],'pipeline_terminal',name=point['name']+' — сценарные операции',hazardous=True,reference_ids=['pipeline-contract-2026'])
        lengths={(s.get('origin'),s.get('destination')):s['distance_km'] for s in self.pipeline['physical_sections']}
        for row in self.pipeline['routes']:
            self.add_edge({'id':row['id'],'from_hub':'pipe:'+row['origin'],'to_hub':'pipe:'+row['destination'],'mode':'pipeline','kind':'pipeline',
                'distance_km':lengths.get((row['origin'],row['destination'])),'travel_hours':24,
                'quote':quote('transport',float(row['rate_rub']),'per_tonne',ev('fact',[row['source_id']]),tax_rate=None),
                'cost_scope':'published_component','missing_cost_components':row['missing_components'],
                'reference_metadata':{'tariff':row,'physical_distance_is_not_tariff_distance':True,'flow_tonnes_per_hour_status':'synthetic'}})

    def resolve(self, value):
        if value in self.settlements:return value
        if value in self.gar_aliases:return self.gar_aliases[value]
        name, separator, territory = value.partition(",")
        matches = self.by_name.get(normalized_name(name), [])
        if separator and normalized_name(territory):
            qualifier = normalized_name(territory)
            matches = [identifier for identifier in matches if any(
                qualifier in normalized_name(self.settlements[identifier].get(field) or "")
                for field in ("region", "district"))]
        if not separator and len(matches) > 1:
            cities = [identifier for identifier in matches if self.settlements[identifier].get("kind") == "г."]
            if len(cities) == 1:
                return cities[0]
        return matches[0] if len(matches)==1 else None

    def options(self, query='', offset=0, limit=50):
        name, _, territory = query.partition(",")
        key, qualifier = normalized_name(name), normalized_name(territory)
        rows = [city for search, city in self._settlement_search if key in search and (
            not qualifier or any(qualifier in normalized_name(city.get(field) or "") for field in ("region", "district")))]
        if key:
            rows.sort(key=lambda city: (normalized_name(city["name"]) != key,
                                        not normalized_name(city["name"]).startswith(key),
                                        -city.get("population", 0)))
        return {'network_id':ID,'fingerprint':self.fingerprint,'total':len(rows),'offset':offset,'limit':limit,'settlements':rows[offset:offset+limit],
                'coverage':self.coverage(),'stop_actions':['visit','pickup','delivery','pickup_delivery'],'arbitrary_addresses_supported':False}

    @cached_property
    def published_tariffs(self):
        return index_tariffs(self)

    def coverage(self):
        registry = None
        if self.registry is not None:
            registry = {'source': self.registry['source']['id'], 'gar_localities': len(self.registry['settlements']),
                        'region_files_with_localities': sum(bool(row['records']) for row in self.registry['regions'])}
        return {'settlements':len(self.settlements),'observed_settlements':len(self.snapshot['settlements']), 'hubs':len(self.hubs),
                'connections_by_mode':dict(Counter(e['mode'] for e in self.edges.values())), 'observations':self.observation_counts,
                'ports_reference_only':len(self.reference_ports),'operational_connections_confirmed':False,
                'settlement_registry':registry,
                'geography':('Поиск по реестру населённых пунктов ГАР. Расчётные связи и тарифы имеют отдельное покрытие.'
                             if registry else 'Все пригодные направленные связи предоставленного набора; не все населённые пункты РФ'),
                'model':'Сценарная грузовая модель поверх исходной связности; реальные тарифы/даты провайдеров сохраняются отдельно',
                'limitations':self.coverage_notes+['Отсутствующие связи не достраиваются по прямой линии.','Общие объёмы АСОП/Росстата не становятся тарифом или свободной мощностью.']}

    def service(self, edge):
        if edge['kind']=='service':return deepcopy(edge['service'])
        mode=edge['mode'];identifier=edge['id'];pipe=mode=='pipeline'
        service={'id':identifier,'from_hub':edge['from_hub'],'to_hub':edge['to_hub'],'mode':mode,'available':True,
            'cargo_types':['crude_oil'] if pipe else ['flour','dry_general'],'cargo_profiles':['crude_oil'] if pipe else ['dry_food','dry_general'],
            'packagings':['bulk_liquid'] if pipe else ['bags','boxes'],'hazardous_allowed':pipe,
            'windows':[{'start':START,'end':END}], 'timetable':{'kind':'on_demand'} if mode=='road' or pipe else {'kind':'interval','anchor':START,'every_hours':24},
            'travel_hours':edge['travel_hours'],'distance_km':edge.get('distance_km'),'cutoff_hours':0 if pipe or mode=='road' else 6,
            'booking':'flow' if pipe else 'dedicated','capacity_scope':'fleet' if mode=='road' else 'departure',
            'resource_id':identifier,'available_units':None if pipe else 8,'evidence':ev(),
            'connection_status':'observation_based_scenario' if edge['kind'] in ('observation','connector','provider_rail','provider_air') else 'scenario',
            'reference_metadata':edge.get('reference_metadata',{}),
            'terminal_handling_included':edge.get('terminal_handling_included',False),
            'allowed_phases':['liquid'] if pipe else ['solid'],
            'cost_scope':edge.get('cost_scope','complete_scenario'),'missing_cost_components':edge.get('missing_cost_components',[])}
        if mode=='road':
            service.update(unit_tonnes=20,unit_m3=82,turnaround_hours=max(2,edge['travel_hours']*2+2),quote=edge.get('quote') or quote('transport',max(5000,edge['distance_km']*90),'per_unit'))
        elif mode=='rail':
            service.update(unit_tonnes=edge.get('unit_tonnes',60),unit_m3=edge.get('unit_m3',120),
                           rail_gauge_mm=1520,quote=edge.get('quote') or dict(quote('transport',3,'per_tonne_km'),minimum_rub=15000))
            service['require_volume'] = edge['kind']=='provider_rail'
        elif mode=='air':
            service.update(unit_tonnes=1,unit_m3=6,max_piece_tonnes=.079,max_piece_dimensions_m=[1.5,1.2,1.0],allow_rotation=True,require_volume=True,
                           quote=deepcopy(edge.get('quote')) or quote('transport',450,'per_kg'))
            service['quote'].setdefault('volumetric_kg_per_m3',1000000/6000)
            if edge['kind']=='observation':service['quote']['minimum_rub']=10000
        elif mode=='sea':
            service.update(unit_tonnes=18,unit_m3=28,max_piece_tonnes=2,max_piece_dimensions_m=[2,2,2],require_volume=True,allow_rotation=True,
                vessel_draft_m=5,max_draft_m=8,ice_class_required=False,quote=edge.get('quote') or quote('transport',200000,'per_unit'))
            if schedule_source := service['reference_metadata'].get('source_id'):
                service['evidence'] = ev(sources=[SCENARIO,schedule_source])
            if edge.get('quote'):
                service.update(unit_tonnes=28.26, unit_m3=33.2, gross_limit_tonnes=30, tare_tonnes=2.22,
                               max_piece_tonnes=None, max_piece_dimensions_m=[5.898,2.34,2.28])
                service['evidence']['source_ids'].append(SEA_EQUIPMENT_SOURCE)
        elif pipe:
            service.update(commodity_ids=['crude_oil'],flow_tonnes_per_hour=100,quote=edge['quote'],allowed_phases=['liquid'])
        if edge.get('cutoff_at'):
            service['cutoff_hours']=(datetime.fromisoformat(edge['departure_at'])-datetime.fromisoformat(edge['cutoff_at'])).total_seconds()/3600
        if edge.get('departure_at'):
            service['timetable']={'kind':'explicit','departures':[edge['departure_at']]}
        return service

    def subgraph(self, paths, *, project_catalog=None):
        edges={edge['id']:edge for path in paths for edge in path}
        services=[self.service(edge) for edge in edges.values()]
        hub_ids={key for s in services for key in (s['from_hub'],s['to_hub'])}
        transfers=[];pairs=set()
        for path in paths:
            for a,b in zip(path,path[1:]):
                key=(a['to_hub'],a['mode'],b['mode'])
                if key in pairs:continue
                pairs.add(key);hub,ma,mb=key
                transfers.append({'id':'transfer:'+':'.join(key),'hub_id':hub,'from_mode':ma,'to_mode':mb,
                    'cargo_types':['crude_oil'] if ma==mb=='pipeline' else ['flour','dry_general'],
                    'cargo_profiles':['crude_oil'] if ma==mb=='pipeline' else ['dry_food','dry_general'],
                    'packagings':['bulk_liquid'] if ma==mb=='pipeline' else ['boxes','bags'],'hazardous_allowed':ma==mb=='pipeline',
                    'operation':{'fixed_hours':1 if ma==mb else 4,'tonnes_per_hour':20,'resource_id':'transfer:'+hub,'lanes':2,'quote':quote('handling',300),'evidence':ev()},'evidence':ev()})
        catalog = {'id':ID,'coverage':self.coverage()['geography'],'sources':list(self.sources.values()),'cargo_types':self.cargo_types,
                   'compatibility':{'dry_general|flour':True},'hubs':[self.hubs[h] for h in sorted(hub_ids)],'services':services,'transfers':transfers}
        return FreightNetwork(project_catalog(catalog) if project_catalog else catalog)

    def paths(self, starts, targets, via, request, events, *, price_weight=None, include_dated=True):
        counter=itertools.count();found=[];seen_paths=set();stats={'expansions':0,'truncated':False,'scope':'bounded_candidate_search_not_global_optimum'}
        last_ready=max(events)+timedelta(hours=request.max_delivery_hours)
        first_ready=min(events)
        seeds=[]
        replaced_direct=set()
        forecast=price_weight is not None
        priced_air=forecast and 'air' in request.allowed_modes
        air_volume=all(cargo.volume_m3 is not None for cargo in request.cargoes)
        continuous_hubs={hub for group,stop in zip(via,request.stops) if stop.no_reload for hub in group}

        def compatible(left, right):
            if left['to_hub'] not in continuous_hubs:
                return True
            return (left['mode']==right['mode'] and left['kind']!='connector' and right['kind']!='connector'
                    and left.get('reference_metadata',{}).get('container_size_ft')
                    ==right.get('reference_metadata',{}).get('container_size_ft'))
        def edge_active(edge):
            if (edge['kind']=='provider_rail' and request.cost_basis=='excluding_vat'
                    and edge['quote']['tax_rate'] is None):
                return False
            if forecast and edge['mode']=='air' and edge['kind']!='provider_air':
                return False
            if forecast and edge['kind']=='provider_air':
                return air_volume
            if forecast and via and edge['kind']=='provider_rail':
                return air_volume
            if edge['kind'] in ('provider_rail','provider_air'):
                evidence = edge['quote']['evidence']
                direct = not via and edge['from_hub'] in starts and edge['to_hub'] in targets
                in_period = (datetime.fromisoformat(evidence['valid_from']) <= first_ready
                             and max(events) < datetime.fromisoformat(evidence['valid_until']))
                return direct and air_volume and (in_period or forecast)
            departure = edge.get('departure_at')
            return not departure or include_dated and first_ready <= datetime.fromisoformat(departure) <= last_ready

        def seed(path):
            if priced_air and not via and len(path)>1 and any(edge['mode']=='air' for edge in path) and not has_intercity_land(path):
                return
            ids=tuple(e['id'] for e in path)
            if ids not in seen_paths and len(path)<=request.max_legs:
                seeds.append(path);seen_paths.add(ids)

        def has_intercity_land(path):
            return any(edge['mode'] in ('road','rail') and edge['kind']!='connector' for edge in path)

        def seed_air_land():
            if request.max_legs < 3 or 'road' not in request.allowed_modes:
                return
            land_modes={'road','rail'} & set(request.allowed_modes)
            options=[]
            for endpoint_hubs, opposite_hubs, outward in ((starts,targets,True),(targets,set(starts),False)):
                adjacent = self.outgoing if outward else self.incoming
                far = 'to_hub' if outward else 'from_hub'
                for hub in endpoint_hubs:
                    for flight in adjacent[hub]:
                        if flight['kind']!='provider_air' or flight[far] in opposite_hubs:
                            continue
                        for connector in adjacent[flight[far]]:
                            if connector['kind']!='connector':
                                continue
                            for land in adjacent[connector[far]]:
                                if (land['kind']!='connector' and land['mode'] in land_modes and land[far] in opposite_hubs
                                        and (land['kind']=='provider_rail' or edge_active(land))):
                                    options.append([flight,connector,land] if outward else [land,connector,flight])
            selected={}
            for path in sorted(options,key=lambda p:sum(price_weight(edge) for edge in p)):
                selected.setdefault(tuple(edge['mode'] for edge in path if edge['kind']!='connector'),path)
            for path in selected.values():
                seed(path)
        # Always preserve direct alternatives. Two-leg mixed joins avoid losing modal
        # diversity in the dense observation graph before the bounded search completes.
        if via:
            # Preserve direct road/rail chains through user stops, including mixed
            # chains. Each stop is a set of admissible hubs, not a forced warehouse.
            groups=[set(starts),*via,set(targets)]
            stage_options=[]
            for left,right in zip(groups,groups[1:]):
                options=[]
                for start in left:
                    options.extend(e for e in self.outgoing[start] if e['to_hub'] in right and e['mode'] in request.allowed_modes
                                   and edge_active(e))
                published_rail = {(edge['from_hub'], edge['to_hub']) for edge in options if edge['kind']=='provider_rail'}
                options = [edge for edge in options if not (
                    edge['kind']=='observation' and edge['mode']=='rail'
                    and (edge['from_hub'], edge['to_hub']) in published_rail
                )]
                options.sort(key=price_weight or (lambda e:e['travel_hours']))
                selected={}
                for edge in options:
                    key=edge['mode']
                    if continuous_hubs:
                        key=(key,edge['from_hub'],edge['to_hub'],edge.get('reference_metadata',{}).get('container_size_ft'))
                    selected.setdefault(key,edge)
                stage_options.append(list(selected.values()))
            chains=[]
            for number,path in enumerate(itertools.product(*stage_options)):
                if number>=4096:break
                if all(a['to_hub']==b['from_hub'] and compatible(a,b) for a,b in zip(path,path[1:])):
                    chains.append(list(path))
            chains.sort(key=lambda p:sum(price_weight(e) if price_weight else e['travel_hours'] for e in p))
            for mode in request.allowed_modes:
                matches=[p for p in chains if {e['mode'] for e in p}=={mode}]
                if continuous_hubs:
                    variants={}
                    for path in matches:
                        sizes=tuple(e.get('reference_metadata',{}).get('container_size_ft') for e in path)
                        variants.setdefault(sizes,path)
                    for path in variants.values():seed(path)
                elif matches:seed(matches[0])
            for path in [p for p in chains if len({e['mode'] for e in p})>1][:2]:seed(path)
        if not via:
            for start in starts:
                direct = [edge for edge in self.outgoing[start] if edge['to_hub'] in targets
                          and edge['mode'] in request.allowed_modes and edge_active(edge)]
                provider_destinations = {(edge['mode'],edge['to_hub']) for edge in direct
                                         if edge['kind'] in ('provider_rail','provider_air')}
                for edge in direct:
                    if edge['kind']=='observation' and (edge['mode'],edge['to_hub']) in provider_destinations:
                        replaced_direct.add(edge['id'])
                        continue
                    seed([edge])
            mixed=[]
            for target in targets:
                for right in self.incoming[target]:
                    if right['mode'] not in request.allowed_modes:continue
                    for left in self.incoming[right['from_hub']]:
                        if not edge_active(left) or not edge_active(right):continue
                        if left['from_hub'] in starts and left['mode'] in request.allowed_modes and left['mode']!=right['mode'] and right['from_hub'] not in starts and right['from_hub'] not in targets:
                            mixed.append([left,right])
            ordered_mixed=sorted(mixed,key=lambda p:sum(price_weight(e) if price_weight else e['travel_hours'] for e in p))
            if priced_air:
                for modes in (('road','rail'),('rail','road')):
                    match=next((p for p in ordered_mixed if tuple(edge['mode'] for edge in p)==modes),None)
                    if match:seed(match)
            for path in ordered_mixed[:2]:seed(path)
            if priced_air and air_volume:
                seed_air_land()
        # Search per physical mode and mixed, so dense road observations cannot hide all rail/sea options.
        searches=[{m} for m in request.allowed_modes]+[set(request.allowed_modes)]
        seed_groups=defaultdict(list)
        for path in seeds:seed_groups[tuple((e['mode'],e['kind']) for e in path)].append(path)
        def seed_rank(path):
            if price_weight:
                return sum(price_weight(edge) for edge in path)
            wait=sum(max(0,(datetime.fromisoformat(e['departure_at'])-first_ready).total_seconds()/3600) for e in path if e.get('departure_at'))
            return sum(e['travel_hours'] for e in path)+wait
        buckets=[sorted(bucket,key=seed_rank) for bucket in seed_groups.values()]
        for allowed in searches:
            heap=[];labels=Counter()
            for start in starts:heapq.heappush(heap,(0,next(counter),start,(),frozenset([start]),0,''))
            accepted=0;expanded=0;bucket=[]
            mixed=len(allowed)>1
            while heap and accepted < max(2,request.max_candidates//2):
                if expanded>=6000:
                    stats['truncated']=True;break
                score,_,node,path,visited,progress,last_mode=heapq.heappop(heap);expanded+=1
                if progress<len(via) and node in via[progress]:progress+=1
                if node in targets and progress==len(via) and path:
                    ids=tuple(e['id'] for e in path)
                    if mixed and len({e["mode"] for e in path})<2:continue
                    if priced_air and mixed and any(e['mode']=='air' for e in path) and not has_intercity_land(path):continue
                    if ids not in seen_paths:bucket.append(list(path));seen_paths.add(ids);accepted+=1
                    continue
                if len(path)>=request.max_legs:continue
                label=(node,progress,last_mode,frozenset(e['mode'] for e in path),
                       path[-1]['kind'] if path and node in continuous_hubs else None)
                if labels[label]>=3:continue
                labels[label]+=1
                for edge in self.outgoing.get(node,[]):
                    if edge['kind']=='provider_rail' or (edge['kind']=='provider_air' and not priced_air) or not path and edge['id'] in replaced_direct:
                        continue
                    if edge['kind']=='provider_air' and 'air' in label[3]:
                        continue
                    if edge['mode'] not in allowed or edge['to_hub'] in visited:continue
                    destination=edge['to_hub']
                    # Cannot pass a future mandatory stop before its predecessor.
                    if any(destination in group for group in via[progress+1:]):continue
                    if not edge_active(edge):continue
                    if path and not compatible(path[-1],edge):continue
                    penalty=4 if last_mode and last_mode!=edge['mode'] else 0
                    weight=(price_weight(edge) if price_weight else edge['travel_hours'])+penalty+1
                    heapq.heappush(heap,(score+weight,next(counter),destination,path+(edge,),visited|{destination},progress,edge['mode']))
            stats['expansions']+=expanded
            buckets.append(bucket)
        for round_index in range(max((len(b) for b in buckets),default=0)):
            for bucket in buckets:
                if round_index<len(bucket):found.append(bucket[round_index])
        # Fair interleaving of physical-mode and mixed alternatives, capped by caller.
        if len(found)>request.max_candidates:stats['truncated']=True
        return found[:request.max_candidates],stats

    def calculate(self, payload, *, project_catalog=None, price_weight=None, include_dated=True,
                  published_forecast_year=None, indices=None):
        request=payload if isinstance(payload,RussiaRequest) else RussiaRequest.model_validate(payload)
        events,_,_=build_schedule(request.schedule)
        if len(events)>64:raise ValueError('Не более 64 отправок за один сетевой расчёт')
        clarifications=[]
        locations=[request.origin,*[s.settlement for s in request.stops],request.destination]
        resolved=[self.resolve(value) for value in locations]
        for index,(value,identifier) in enumerate(zip(locations,resolved)):
            if identifier is None:clarifications.append({'code':'unknown_or_ambiguous_settlement','field':f'locations.{index}', 'message':'Выберите населённый пункт из справочника: '+value})
        for i,stop in enumerate(request.stops):
            if stop.action is None:clarifications.append({'code':'missing_stop_action','field':f'stops.{i}.action','message':'Что сделать в '+stop.settlement+': забор груза, выгрузка, оба действия или обязательное посещение?'})
        base={'schema_version':'1.0','engine':'russia','network_id':ID,'network_fingerprint':self.fingerprint,'request':request.model_dump(mode='json'),
              'coverage':self.coverage(),'settlements':resolved,'clarifications':clarifications,'candidates':[],'recommendation':None,'sources':list(self.sources.values()),
              'input_status':'needs_clarification' if clarifications else 'ready','warnings':[], 'shipments':len(events),
              'status':'needs_clarification' if clarifications else 'ready','search':{'expansions':0,'truncated':False,'scope':'awaiting_input'},'stops':[],'selected_subgraph':None}
        if clarifications:return base
        if len(set(resolved))!=len(resolved):raise ValueError('Начало, конец и обязательные остановки должны различаться; циклические туры пока не поддерживаются')
        stops=[dict(stop.model_dump(),settlement_id=city) for stop,city in zip(request.stops,resolved[1:-1])]
        pickups,deliveries={},{}
        ids={c.id for c in request.cargoes}
        for stop_index,stop in enumerate(stops):
            for target,mapping in (('pickup_cargo_ids',pickups),('delivery_cargo_ids',deliveries)):
                for cargo_id in stop[target]:
                    if cargo_id not in ids:raise ValueError('Остановка ссылается на неизвестную позицию: '+cargo_id)
                    if cargo_id in mapping:raise ValueError('Повтор погрузки/выгрузки одной позиции; частичную выгрузку задайте отдельными позициями')
                    mapping[cargo_id]=stop_index
        starts=[h['id'] for h in self.hubs.values() if h['settlement_id']==resolved[0]]
        targets={h['id'] for h in self.hubs.values() if h['settlement_id']==resolved[-1]}
        via=[{h['id'] for h in self.hubs.values() if h['settlement_id']==city} for city in resolved[1:-1]]
        for cargo in request.cargoes:
            pi=pickups.get(cargo.id,-1);di=deliveries.get(cargo.id,len(via))
            if pi>=di:raise ValueError('Погрузка позиции должна предшествовать её выгрузке: '+cargo.id)
        if request.data_policy == 'verified_only' and {'road', 'rail'}.intersection(request.allowed_modes):
            return calculate_published(self, base | {"stops": stops}, request, events, published_forecast_year, indices)
        paths,search=self.paths(starts,targets,via,request,events,price_weight=price_weight,include_dated=include_dated)
        base['search']=search;base['stops']=stops
        if not paths:
            if self.registry and {'road', 'rail'}.intersection(request.allowed_modes) and any(
                    city.startswith("gar:") for city in resolved):
                return calculate_published(self, base, request, events, published_forecast_year, indices)
            base['status']='no_route_in_dataset';base['warnings'].append('Нет цепочки в выбранных видах транспорта, датах и пределах поиска; это не доказательство отсутствия перевозки в РФ.')
            return base
        if any(e['mode']=='pipeline' for path in paths for e in path) and request.mass_basis!='net':
            base['input_status']='needs_clarification';base['status']='needs_clarification'
            base['clarifications'].append({'code':'pipeline_net_mass_required','field':'mass_basis','message':'Тариф нефти считается по массе нетто в начале маршрута. Укажите net и массу без балласта.'})
            return base
        graph=self.subgraph(paths,project_catalog=project_catalog);candidates=[];questions=[]
        for path in paths:
            origin,destination=path[0]['from_hub'],path[-1]['to_hub']
            nodes=[origin,*[e['to_hub'] for e in path]]
            selected_via=[next(node for node in nodes if node in group) for group in via]
            cargoes=[c.model_copy(update={'pickup_hub_id':selected_via[pickups[c.id]] if c.id in pickups else origin,
                                         'delivery_hub_id':selected_via[deliveries[c.id]] if c.id in deliveries else destination}) for c in request.cargoes]
            # Validate endpoints/order against the selected itinerary, then reuse the physical/resource ledger.
            plan=NetworkRequest(engine='network',network_id=ID,origin_hub_id=origin,destination_hub_id=destination,cargoes=cargoes,
                schedule=request.schedule,max_delivery_hours=request.max_delivery_hours,capacity_basis=request.capacity_basis,cost_basis=request.cost_basis,
                data_policy=request.data_policy,service_ids=[e['id'] for e in path],via_hub_ids=selected_via,max_legs=request.max_legs,
                stop_dwell_hours={hub:s['dwell_hours'] for hub,s in zip(selected_via,stops)})
            result=calculate_network(plan,graph,include_pricing=project_catalog is not None);questions.extend(result['clarifications'])
            for row in result['candidates']:
                row['stop_actions']=[dict(stop,hub_id=hub) for stop,hub in zip(stops,selected_via)]
                row['route_hubs']=[{'id':identifier,'settlement_id':self.hubs[identifier]['settlement_id'],'name':self.hubs[identifier]['name'],
                                   'identity_status':self.hubs[identifier]['identity_status']} for identifier in [origin,*[e['to_hub'] for e in path]]]
                row['connections']=[{'id':e['id'],'mode':e['mode'],'kind':e['kind'],'distance_km':e.get('distance_km'),
                                     'reference_metadata':e.get('reference_metadata',{}),'operational_status':'scenario'} for e in path]
                if any(stop['no_reload'] for stop in stops):
                    published={e['id'] for e in path if e.get('reference_metadata',{}).get('tariff_source_id')}
                    row['cost_items']=[item for item in row['cost_items']
                                       if item['kind']=='transport' and item['object_id'] in published]
                    if row['status'] != 'infeasible':
                        row.update(status='insufficient_data',complete=False,total_cost_rub=None,cost_rub_per_tonne=None,
                                   max_delivery_hours=None)
                        row['issues'].append({'code':'unconfirmed_stop_continuity','fields':['stops'],
                            'message':'Нет сквозного подтверждения, что груз останется в том же контейнере или машине. '
                                      'Время стоянки и возможные сборы нужно согласовать с перевозчиком.'})
                pipeline_edges=[e for e in path if e['kind']=='pipeline']
                if pipeline_edges:
                    subtotal=Decimal(0)
                    for edge in pipeline_edges:
                        stage=nodes.index(edge['from_hub'])
                        mass=sum(Decimal(str(c.weight_tonnes)) for c in cargoes if nodes.index(c.pickup_hub_id)<=stage<nodes.index(c.delivery_hub_id))*len(events)
                        subtotal+=mass*Decimal(str(edge['quote']['rate_rub']))
                    row['published_tariff_estimate']={'amount_excluding_vat_rub':float(subtotal.quantize(Decimal('.01'))),
                        'mass_basis':'net_at_route_origin_assumed_from_input','scope':'route_components_only_not_total_price',
                        'missing_components':sorted({f for e in pipeline_edges for f in e['missing_cost_components']}),
                        'automatic_addition_of_dispatch_or_handling':False}
                row['calculation_mode']='scenario';row['booking_confirmed']=False;candidates.append(row)
        if questions:
            base['clarifications']=list({json.dumps(q,sort_keys=True):q for q in questions}.values());base['input_status']='needs_clarification'
        eligible=[c for c in candidates if c['status']=='feasible']
        best=min(eligible,key=lambda c:(c['total_cost_rub'],c['max_delivery_hours'])) if eligible else None
        base.update(candidates=candidates,recommendation=best['id'] if best else None,status='calculated' if eligible else 'not_confirmed',
                    selected_subgraph={'hubs':len(graph.hubs),'services':len(graph.services),'transfers':len(graph.catalog.transfers)},
                    warnings=['Цены/ресурсы сценарной модели явно заданы в источнике '+SCENARIO+'. Это не бронирование перевозки.',
                              'Подтверждённые тарифы авиации/трубопровода могут покрывать только часть затрат; они не выдаются за полную цену.',
                              'Поиск ограничен числом кандидатов и этапов; глобальный минимум не доказан.'])
        return base


from functools import lru_cache

@lru_cache(maxsize=2)
def _load_russia(tlm_snapshot, road_snapshot, air_snapshot):
    return RussiaNetwork(tlm_tariff=read_snapshot(tlm_snapshot) if tlm_snapshot else None,
                         tlm_road_tariff=read_snapshot(road_snapshot) if road_snapshot else None,
                         air_tariff=read_air_snapshot(air_snapshot) if air_snapshot else None,
                         registry=settlement_catalog())


def load_russia(*, tariff_date=None, air_tariff_date=None):
    today = moscow_today()
    cutoff = min(today, tariff_date) if tariff_date is not None else today
    directory = snapshot_dir()
    air_today = yakutsk_today()
    air_date = air_tariff_date or tariff_date or air_today
    return _load_russia(latest_snapshot(directory, cutoff), latest_snapshot(directory, cutoff, road=True),
                        latest_air_snapshot(directory, min(air_today, air_date)))


def calculate_russia(payload):
    request = payload if isinstance(payload, RussiaRequest) else RussiaRequest.model_validate(payload)
    events, _, _ = build_schedule(request.schedule)
    ready = min(events)
    return load_russia(tariff_date=ready.astimezone(MOSCOW).date(),
                       air_tariff_date=ready.astimezone(YAKUTSK).date()).calculate(request)
