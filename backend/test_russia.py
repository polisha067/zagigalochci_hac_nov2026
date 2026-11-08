from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import unittest

from backend.network.russia import load_russia, RussiaRequest, ID
from backend.network.models import Quote
from backend.network.policies import Policies
from backend.network.engine import Item
from backend import test_calculation_api as helpers


class RussiaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network=load_russia()

    def payload(self):
        return json.loads((Path(__file__).parent/'examples/russia-request.json').read_text())

    def test_all_observed_routes_are_directional_and_nulls_not_invented(self):
        n=self.network
        usable=0
        for a,b,mode,km,hours,reason in n.snapshot['connections']:
            key=f'observation:{mode}:{a}:{b}'
            valid=km is not None and hours is not None and not reason and km>0 and hours>0
            self.assertEqual(key in n.edges,valid)
            if valid:
                usable+=1;self.assertEqual(n.edges[key]['distance_km'],km)
        self.assertEqual(usable,64430)
        self.assertEqual(len(n.snapshot['settlements']),180)
        self.assertEqual(set(n.coverage()['connections_by_mode']),{'road','rail','sea','river','air','pipeline'})
        self.assertFalse(n.coverage()['operational_connections_confirmed'])

    def test_direct_and_multimodal_candidates_have_full_balance_and_tlc(self):
        r=self.network.calculate(self.payload())
        self.assertEqual(r['status'],'calculated');self.assertIsNotNone(r['recommendation'])
        self.assertTrue(any(c['modes']==['road'] for c in r['candidates']))
        self.assertTrue(any(c['modes']==['rail'] for c in r['candidates']))
        self.assertTrue(any(c['multimodal'] for c in r['candidates']))
        for candidate in r['candidates']:
            if candidate['status']=='feasible':
                self.assertEqual(sum(d['weight_tonnes'] for d in candidate['deliveries']),10)
                self.assertEqual(round(sum(c['amount_rub'] for c in candidate['cost_items']),2),candidate['total_cost_rub'])
                self.assertFalse(candidate['booking_confirmed'])
        self.assertTrue(all(h['identity_status']=='scenario' for c in r['candidates'] for h in c['route_hubs']))

    def test_stop_action_is_clarified_without_silent_visit(self):
        q=self.payload();q['stops']=[{'settlement':'Калуга'}]
        r=self.network.calculate(q)
        self.assertEqual(r['input_status'],'needs_clarification');self.assertFalse(r['candidates'])
        self.assertEqual(r['clarifications'][0]['code'],'missing_stop_action')

    def test_address_and_unknown_locality_do_not_become_coordinates(self):
        q=self.payload();q['origin']='Москва, улица Ленина, дом 1'
        r=self.network.calculate(q)
        self.assertEqual(r['input_status'],'needs_clarification')
        self.assertFalse(r['candidates'])

    def test_pickup_delivery_and_dwell_recalculate_load_at_each_stop(self):
        q=self.payload();q['allowed_modes']=['road'];q['max_delivery_hours']=240
        q['cargoes'] += [dict(q['cargoes'][0],id='pickup',weight_tonnes=5,volume_m3=10),dict(q['cargoes'][0],id='drop',weight_tonnes=2,volume_m3=4)]
        q['stops']=[{'settlement':'Калуга','action':'pickup_delivery','pickup_cargo_ids':['pickup'],'delivery_cargo_ids':['drop'],'dwell_hours':3}]
        r=self.network.calculate(q)
        candidate=next(c for c in r['candidates'] if c['status']=='feasible')
        steps=[s for s in candidate['steps'] if s['kind']=='transport']
        self.assertEqual(steps[0]['weight_tonnes'],12)
        self.assertEqual(steps[-1]['weight_tonnes'],15)
        self.assertEqual(sum(d['weight_tonnes'] for d in candidate['deliveries']),17)
        self.assertTrue(any(s['kind']=='mandatory_stop' and s['dwell_hours']==3 for s in candidate['steps']))
        selected=candidate['stop_actions'][0]['hub_id']
        self.assertTrue(all(e['to_hub']==selected or e['from_hub']==selected for e in steps[:2]))

    def test_invalid_stop_orders_and_partial_unload_require_separate_positions(self):
        q=self.payload();q['stops']=[{'settlement':'Калуга','action':'pickup_delivery','pickup_cargo_ids':['flour'],'delivery_cargo_ids':['flour']}]
        with self.assertRaisesRegex(ValueError,'предшествовать'):self.network.calculate(q)
        q['stops']=[{'settlement':'Калуга','action':'pickup','pickup_cargo_ids':['missing']}]
        with self.assertRaisesRegex(ValueError,'неизвестную'):self.network.calculate(q)
        with self.assertRaises(ValueError):RussiaRequest.model_validate(dict(self.payload(),stops=[{'settlement':'Калуга','action':'visit','pickup_cargo_ids':['flour']}]))

    def test_verified_policy_does_not_promote_passenger_observations_to_freight(self):
        q=self.payload();q['data_policy']='verified_only'
        r=self.network.calculate(q)
        self.assertIsNone(r['recommendation'])
        self.assertTrue(all(c['status']=='insufficient_data' for c in r['candidates']))
        self.assertTrue(all(c['issues'][0]['code']=='evidence_policy' for c in r['candidates']))

    def test_pipeline_component_is_not_full_price_or_distance_multiplied(self):
        q=self.payload();q.update(origin='Тайшет',destination='Ангарск',allowed_modes=['pipeline'],capacity_basis='mass_only',cost_basis='excluding_vat')
        q['cargoes']=[{'id':'oil','name':'Нефть','weight_tonnes':1000,'packaging':'bulk_liquid','splittable':True}]
        r=self.network.calculate(q)
        self.assertEqual(r['clarifications'][0]['code'],'pipeline_net_mass_required')
        q['mass_basis']='net';r=self.network.calculate(q);c=r['candidates'][0]
        self.assertEqual(c['published_tariff_estimate']['amount_excluding_vat_rub'],507280)
        self.assertIsNone(c['total_cost_rub']);self.assertIsNone(r['recommendation'])
        self.assertEqual(c['issues'][0]['code'],'dangerous_goods_documents')
        self.assertFalse(c['published_tariff_estimate']['automatic_addition_of_dispatch_or_handling'])
        self.assertFalse(any(e['id']=='pipeline-ankhk-taishet' for e in self.network.edges.values()))

    def test_native_per_100_units(self):
        q=self.payload();r=self.network.calculate(q)
        path=[self.network.edges[r['candidates'][0]['service_ids'][0]]]
        graph=self.network.subgraph([path])
        class Request:
            data_policy='allow_assumptions';cost_basis='excluding_vat'
        policy=Policies(graph,Request())
        from backend.network.russia import ev
        cargo=RussiaRequest.model_validate(q).cargoes[0]
        item=Item('cargo',cargo,None,0)
        tariff=Quote.model_validate({'component':'transport','basis':'per_100_tonnes','rate_rub':2646.52,'tax_basis':'excluding_vat','evidence':ev()})
        self.assertEqual(policy.price(tariff,[item],tariff.evidence.valid_from,'test'),Decimal('264.65'))
        tariff=tariff.model_copy(update={'basis':'per_100_tonne_km','rate_rub':50.7193})
        self.assertEqual(policy.price(tariff,[item],tariff.evidence.valid_from,'test',distance=200),Decimal('1014.39'))

    def test_sea_voyages_respect_terminal_and_no_return_invented(self):
        edges=[e for e in self.network.edges.values() if e['kind']=='fesco']
        self.assertEqual(len(edges),20)
        self.assertTrue(all(e['mode']=='sea' and e.get('departure_at') for e in edges))
        self.assertFalse(any(e['id']=='fesco-fpkl:2640PK' for e in edges))
        q=self.payload();q.update(origin='Владивосток',destination='Петропавловск-Камчатский',allowed_modes=['sea'],max_delivery_hours=400)
        r=self.network.calculate(q)
        self.assertTrue(any(c['status']=='feasible' for c in r['candidates']))
        self.assertTrue(all(c['connections'][0]['reference_metadata']['source_id']=='fesco-fpkl-2' for c in r['candidates']))
        q['schedule']['ready_at']='2027-01-09T08:00:00+03:00'
        self.assertEqual(self.network.calculate(q)['status'],'no_route_in_dataset')

    def test_published_aviation_is_partial_and_river_is_seasonal(self):
        q=self.payload();q.update(origin='Анадырь',destination='Провидения',allowed_modes=['air'],max_delivery_hours=240)
        q['cargoes'][0].update(weight_tonnes=.1,volume_m3=.5)
        r=self.network.calculate(q)
        published=[c for c in r['candidates'] if c['service_ids'][0].startswith('chukotavia:')]
        self.assertTrue(published)
        self.assertTrue(all(c['total_cost_rub'] is None for c in published))
        self.assertTrue(any(c['issues'][0]['code']=='partial_tariff' for c in published))
        q=self.payload();q.update(origin='Красноярск',destination='Дудинка',allowed_modes=['river'],max_delivery_hours=480)
        q['schedule']['ready_at']='2026-06-08T08:00:00+07:00'
        self.assertTrue(any(c['status']=='feasible' for c in self.network.calculate(q)['candidates']))
        q['schedule']['ready_at']='2026-11-09T08:00:00+07:00'
        self.assertFalse(any(c['status']=='feasible' for c in self.network.calculate(q)['candidates']))


    def test_route_planning_does_not_mutate_cached_network(self):
        from backend.network.russia import digest
        before=digest(self.network.edges)
        q=self.payload();q.update(origin='Анадырь',destination='Провидения',allowed_modes=['air'],max_delivery_hours=240)
        q['cargoes'][0].update(weight_tonnes=.1,volume_m3=.5)
        self.network.calculate(q)
        self.assertEqual(digest(self.network.edges),before)

    def test_pipeline_component_endpoint_works_for_unmapped_points(self):
        from backend.network.pipeline import calculate_pipeline, PipelineResponse
        q={'route_id':'pipeline-zapolyarye-purpe','net_weight_tonnes':1000,'shipment_at':'2026-11-09T08:00:00+03:00'}
        r=calculate_pipeline(q);PipelineResponse.model_validate(r)
        self.assertEqual(r['known_components'][0]['amount_excluding_vat_rub'],632000)
        self.assertIsNone(r['total_cost_rub'])
        self.assertFalse(r['dispatch_and_terminal_operations_added'])
        self.assertIsNone(self.network.hubs['pipe:zapolyarye']['settlement_id'])
        self.assertIsNone(self.network.resolve('Пункт ГНПС «Заполярье»'))
        with self.assertRaises(ValueError):calculate_pipeline(dict(q,route_id='imaginary-reverse'))

    def test_pipeline_snapshot_boundary_uses_moscow_calendar(self):
        from backend.network.pipeline import calculate_pipeline
        q={'route_id':'pipeline-taishet-ankhk','net_weight_tonnes':1,'shipment_at':'2025-12-31T22:00:00+00:00'}
        self.assertEqual(calculate_pipeline(q)['status'],'partial_tariff')
        q['shipment_at']='2026-12-31T22:00:00+00:00'
        self.assertEqual(calculate_pipeline(q)['status'],'outside_snapshot_validity')

    def test_local_truck_minimum_is_per_vehicle_and_helipad_not_a_new_city(self):
        edge=next(e for e in self.network.edges.values() if e['kind']=='connector')
        quote=self.network.service(edge)['quote']
        self.assertEqual(quote['rate_rub'],5000)
        # Two trucks pay two minima, not one minimum for the combined shipment.
        q=Quote.model_validate(quote)
        class Request:
            data_policy='allow_assumptions';cost_basis='excluding_vat'
        graph=self.network.subgraph([[edge]])
        cargo=RussiaRequest.model_validate(self.payload()).cargoes[0]
        amount=Policies(graph,Request()).price(q,[Item('cargo',cargo,None,0)],q.evidence.valid_from,'connector',units=2)
        self.assertEqual(amount,Decimal('10000.00'))
        self.assertIsNone(self.network.resolve('Окружной'))
        self.assertIsNone(self.network.hubs['air-point:okruzhnoy']['settlement_id'])

    def test_all_reference_ports_kept_without_invented_shipping_lines(self):
        self.assertEqual(len(self.network.reference_ports),54)
        sea=[e for e in self.network.edges.values() if e['mode']=='sea']
        self.assertEqual(len(sea),20)
        self.assertTrue(all(e['kind']=='fesco' for e in sea))
        self.assertEqual(len(self.network.pipeline['pumping_by_operator']),12)
        self.assertTrue(self.network.pipeline['destination_point_reference']['destination_table_raw_cells'])


class RussiaApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def test_authenticated_options_hubs_pipeline_and_dispatch(self):
        q=RussiaTests.payload(self)
        result=self.client.get('/network/russia/options',params={'query':'Москва'}).json()
        self.assertEqual(result['settlements'][0]['name'],'Москва')
        self.assertFalse(result['arbitrary_addresses_supported'])
        self.assertEqual(self.client.get('/network/russia/options?limit=1000').status_code,422)
        hubs=self.client.get('/network/russia/hubs?settlement=Москва').json()['hubs']
        self.assertTrue(any('rail' in h['modes'] and 'road' in h['modes'] for h in hubs))
        self.assertEqual(self.client.get('/network/pipeline/options').status_code,200)
        self.assertEqual(len(self.client.get('/network/russia/reference-ports').json()['ports']),54)
        pipe={'engine':'pipeline','route_id':'pipeline-taishet-ankhk','net_weight_tonnes':1000,'shipment_at':'2026-11-09T08:00:00+03:00'}
        p=[self.client.post(url,json=pipe) for url in ('/network/pipeline/calculations','/calculations')]
        self.assertTrue(all(r.status_code==200 for r in p),[r.text for r in p])
        self.assertEqual(p[0].json(),p[1].json())
        self.assertEqual(p[0].json()['known_components'][0]['amount_excluding_vat_rub'],507280)
        results=[self.client.post(url,json=q) for url in ('/network/russia/calculations','/calculations')]
        self.assertTrue(all(r.status_code==200 for r in results),[r.text for r in results])
        self.assertEqual(results[0].json(),results[1].json())
        self.assertEqual(results[0].headers['cache-control'],'no-store')
        self.client.cookies.clear()
        for url in ('/network/russia/options','/network/russia/hubs?settlement=Москва','/network/pipeline/options'):
            self.assertEqual(self.client.get(url).status_code,401)
        self.assertEqual(self.client.post('/network/russia/calculations',json=q).status_code,401)
