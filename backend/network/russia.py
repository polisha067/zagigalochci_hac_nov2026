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
from pathlib import Path
from typing import Annotated, List, Literal, Optional

from pydantic import Field, model_validator

from backend.calculation_models import InputModel, Text, Schedule, Hours, NonnegativeHours
from backend.network.cargo import normalized_name
from backend.network.engine import calculate_network
from backend.network.graph import FreightNetwork
from backend.network.models import NetworkCargo, NetworkRequest, Mode
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

    @model_validator(mode='after')
    def correct_action(self):
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


def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class RussiaNetwork:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or json.loads((ROOT/'data_sources/russia/connectivity.json').read_text())
        self.settlements = {c['id']:dict(c, provenance='source-01') for c in self.snapshot['settlements']}
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
            'flour':{'name':'Мука','aliases':['пшеничная мука'],'phase':'solid','hazardous':False,'handling_profile':'dry_food','packagings':['bags'],'evidence':ev()},
            'dry_general':{'name':'Сухой неопасный груз','aliases':[],'phase':'solid','hazardous':False,'handling_profile':'dry_general','packagings':['boxes','bags'],'evidence':ev()},
            'crude_oil':{'name':'Нефть','aliases':['сырая нефть','товарная нефть'],'phase':'liquid','hazardous':True,'handling_profile':'crude_oil','packagings':['bulk_liquid'],'evidence':ev()},
        }
        # Standard packaged goods for scenario estimates. Match only exact names and aliases.
        for identifier, name, aliases, profile, packagings in [
            ('sugar', 'Сахар', ['сахарный песок'], 'dry_food', ['bags', 'boxes']),
            ('rice', 'Рис', [], 'dry_food', ['bags', 'boxes']),
            ('pasta', 'Макароны', ['макаронные изделия'], 'dry_food', ['bags', 'boxes']),
            ('paper', 'Бумага', ['офисная бумага'], 'dry_general', ['boxes']),
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
        self.load_fesco()
        self.load_pipeline()
        self.incoming = defaultdict(list)
        for edge in self.edges.values():self.incoming[edge["to_hub"]].append(edge)
        self.fingerprint = digest({'model_revision':'2026-11-08.1','snapshot':self.snapshot,'edges':self.edges,'hubs':self.hubs,'sources':self.sources,'pipeline':self.pipeline,'reference_ports':self.reference_ports})
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
        data=json.loads((ROOT/'data_sources/river/network.json').read_text())
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
        for row in data['voyages']:
            dep=datetime.fromisoformat(row['departure_at']);arr=datetime.fromisoformat(row['arrival_at'])
            self.add_edge(dict(row,mode='sea',kind='fesco',travel_hours=(arr-dep).total_seconds()/3600))
        self.coverage_notes.extend(data['limitations'])

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
        matches=self.by_name.get(normalized_name(value),[])
        return matches[0] if len(matches)==1 else None

    def options(self, query='', offset=0, limit=50):
        rows=[c for c in self.settlements.values() if normalized_name(query) in normalized_name(c['name'])]
        rows.sort(key=lambda c:(c['name'],c['id']))
        return {'network_id':ID,'fingerprint':self.fingerprint,'total':len(rows),'offset':offset,'limit':limit,'settlements':rows[offset:offset+limit],
                'coverage':self.coverage(),'stop_actions':['visit','pickup','delivery','pickup_delivery'],'arbitrary_addresses_supported':False}

    def coverage(self):
        return {'settlements':len(self.settlements),'observed_settlements':len(self.snapshot['settlements']), 'hubs':len(self.hubs),
                'connections_by_mode':dict(Counter(e['mode'] for e in self.edges.values())), 'observations':self.observation_counts,
                'ports_reference_only':len(self.reference_ports),'operational_connections_confirmed':False,
                'geography':'Все пригодные направленные связи предоставленного набора; не все населённые пункты РФ',
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
            'connection_status':'observation_based_scenario' if edge['kind']=='observation' else 'scenario',
            'reference_metadata':edge.get('reference_metadata',{}),
            'allowed_phases':['liquid'] if pipe else ['solid'],
            'cost_scope':edge.get('cost_scope','complete_scenario'),'missing_cost_components':edge.get('missing_cost_components',[])}
        if mode=='road':
            service.update(unit_tonnes=20,unit_m3=82,turnaround_hours=max(2,edge['travel_hours']*2+2),quote=quote('transport',max(5000,edge['distance_km']*90),'per_unit'))
        elif mode=='rail':
            service.update(unit_tonnes=60,unit_m3=120,rail_gauge_mm=1520,quote=quote('transport',3,'per_tonne_km'))
            service['quote']['minimum_rub']=15000
        elif mode=='air':
            service.update(unit_tonnes=1,unit_m3=6,max_piece_tonnes=.079,max_piece_dimensions_m=[1.5,1.2,1.0],allow_rotation=True,require_volume=True,
                           quote=deepcopy(edge.get('quote')) or quote('transport',450,'per_kg'))
            service['quote']['volumetric_kg_per_m3']=1000000/6000
            if edge['kind']=='observation':service['quote']['minimum_rub']=10000
        elif mode=='sea':
            service.update(unit_tonnes=18,unit_m3=28,max_piece_tonnes=2,max_piece_dimensions_m=[2,2,2],require_volume=True,allow_rotation=True,
                vessel_draft_m=5,max_draft_m=8,ice_class_required=False,quote=quote('transport',200000,'per_unit'))
        elif pipe:
            service.update(commodity_ids=['crude_oil'],flow_tonnes_per_hour=100,quote=edge['quote'],allowed_phases=['liquid'])
        if edge.get('cutoff_at'):
            service['cutoff_hours']=(datetime.fromisoformat(edge['departure_at'])-datetime.fromisoformat(edge['cutoff_at'])).total_seconds()/3600
        if edge.get('departure_at'):
            service['timetable']={'kind':'explicit','departures':[edge['departure_at']]}
        return service

    def subgraph(self, paths):
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
        return FreightNetwork({'id':ID,'coverage':self.coverage()['geography'],'sources':list(self.sources.values()),'cargo_types':self.cargo_types,
                               'compatibility':{'dry_general|flour':True},'hubs':[self.hubs[h] for h in sorted(hub_ids)],'services':services,'transfers':transfers})

    def paths(self, starts, targets, via, request, events):
        counter=itertools.count();found=[];seen_paths=set();stats={'expansions':0,'truncated':False,'scope':'bounded_candidate_search_not_global_optimum'}
        last_ready=max(events)+timedelta(hours=request.max_delivery_hours)
        first_ready=min(events)
        seeds=[]
        def seed(path):
            ids=tuple(e['id'] for e in path)
            if ids not in seen_paths and len(path)<=request.max_legs:
                seeds.append(path);seen_paths.add(ids)
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
                                   and (not e.get('departure_at') or first_ready<=datetime.fromisoformat(e['departure_at'])<=last_ready))
                options.sort(key=lambda e:e['travel_hours'])
                # At most one direct service per physical mode for seeding.
                selected={}
                for edge in options:selected.setdefault(edge['mode'],edge)
                stage_options.append(list(selected.values()))
            chains=[]
            for number,path in enumerate(itertools.product(*stage_options)):
                if number>=4096:break
                if all(a['to_hub']==b['from_hub'] for a,b in zip(path,path[1:])):
                    chains.append(list(path))
            chains.sort(key=lambda p:sum(e['travel_hours'] for e in p))
            for mode in request.allowed_modes:
                match=next((p for p in chains if {e['mode'] for e in p}=={mode}),None)
                if match:seed(match)
            for path in [p for p in chains if len({e['mode'] for e in p})>1][:2]:seed(path)
        if not via:
            for start in starts:
                for edge in self.outgoing[start]:
                    if edge['to_hub'] in targets and edge['mode'] in request.allowed_modes:
                        if edge.get('departure_at') and not first_ready<=datetime.fromisoformat(edge['departure_at'])<=last_ready:continue
                        seed([edge])
            mixed=[]
            for target in targets:
                for right in self.incoming[target]:
                    if right['mode'] not in request.allowed_modes:continue
                    for left in self.incoming[right['from_hub']]:
                        if any(e.get("departure_at") and not first_ready<=datetime.fromisoformat(e["departure_at"])<=last_ready for e in (left,right)):continue
                        if left['from_hub'] in starts and left['mode'] in request.allowed_modes and left['mode']!=right['mode'] and right['from_hub'] not in starts and right['from_hub'] not in targets:
                            mixed.append([left,right])
            for path in sorted(mixed,key=lambda p:sum(e['travel_hours'] for e in p))[:2]:seed(path)
        # Search per physical mode and mixed, so dense road observations cannot hide all rail/sea options.
        searches=[{m} for m in request.allowed_modes]+[set(request.allowed_modes)]
        seed_groups=defaultdict(list)
        for path in seeds:seed_groups[tuple((e['mode'],e['kind']) for e in path)].append(path)
        def seed_rank(path):
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
                    if ids not in seen_paths:bucket.append(list(path));seen_paths.add(ids);accepted+=1
                    continue
                if len(path)>=request.max_legs:continue
                label=(node,progress,last_mode,frozenset(e['mode'] for e in path))
                if labels[label]>=3:continue
                labels[label]+=1
                for edge in self.outgoing.get(node,[]):
                    if edge['mode'] not in allowed or edge['to_hub'] in visited:continue
                    destination=edge['to_hub']
                    # Cannot pass a future mandatory stop before its predecessor.
                    if any(destination in group for group in via[progress+1:]):continue
                    if edge.get('departure_at'):
                        dep=datetime.fromisoformat(edge['departure_at'])
                        if dep<first_ready or dep>last_ready:continue
                    penalty=4 if last_mode and last_mode!=edge['mode'] else 0
                    weight=edge['travel_hours']+penalty+1
                    heapq.heappush(heap,(score+weight,next(counter),destination,path+(edge,),visited|{destination},progress,edge['mode']))
            stats['expansions']+=expanded
            buckets.append(bucket)
        for round_index in range(max((len(b) for b in buckets),default=0)):
            for bucket in buckets:
                if round_index<len(bucket):found.append(bucket[round_index])
        # Fair interleaving of physical-mode and mixed alternatives, capped by caller.
        if len(found)>request.max_candidates:stats['truncated']=True
        return found[:request.max_candidates],stats

    def calculate(self, payload):
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
        paths,search=self.paths(starts,targets,via,request,events)
        base['search']=search;base['stops']=stops
        if not paths:
            base['status']='no_route_in_dataset';base['warnings'].append('Нет цепочки в выбранных видах транспорта, датах и пределах поиска; это не доказательство отсутствия перевозки в РФ.')
            return base
        if any(e['mode']=='pipeline' for path in paths for e in path) and request.mass_basis!='net':
            base['input_status']='needs_clarification';base['status']='needs_clarification'
            base['clarifications'].append({'code':'pipeline_net_mass_required','field':'mass_basis','message':'Тариф нефти считается по массе нетто в начале маршрута. Укажите net и массу без балласта.'})
            return base
        graph=self.subgraph(paths);candidates=[];questions=[]
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
            result=calculate_network(plan,graph);questions.extend(result['clarifications'])
            for row in result['candidates']:
                row['stop_actions']=[dict(stop,hub_id=hub) for stop,hub in zip(stops,selected_via)]
                row['route_hubs']=[{'id':identifier,'settlement_id':self.hubs[identifier]['settlement_id'],'name':self.hubs[identifier]['name'],
                                   'identity_status':self.hubs[identifier]['identity_status']} for identifier in [origin,*[e['to_hub'] for e in path]]]
                row['connections']=[{'id':e['id'],'mode':e['mode'],'kind':e['kind'],'distance_km':e.get('distance_km'),
                                     'reference_metadata':e.get('reference_metadata',{}),'operational_status':'scenario'} for e in path]
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

@lru_cache(maxsize=1)
def load_russia():
    return RussiaNetwork()
