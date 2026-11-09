from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
import unittest

from tests import BACKEND_DIR

from backend.network.chukotavia import ChukotaviaData, calculate_chukotavia
from tests import test_calculation_api as helpers


class AviationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = ChukotaviaData()
        cls.payload = json.loads((BACKEND_DIR/'examples/chukotavia-request.json').read_text())

    def request(self):return deepcopy(self.payload)

    def test_complete_tariff_versions_and_provenance(self):
        rows = self.data.data['tariffs']
        self.assertEqual(len(rows),312)
        for row in rows:
            self.assertEqual(Decimal(row['rate_excluding_vat_rub_per_kg'])*Decimal('1.22'),Decimal(row['rate_including_vat_rub_per_kg']))
            self.assertIn(row['source_id'],self.data.sources)
            self.assertTrue(1<=row['page']<=4)
        key=('анадырь','провидения')
        self.assertEqual(self.data.tariff(key,'2026-10-31')['rate_excluding_vat_rub_per_kg'],'474')
        self.assertEqual(self.data.tariff(key,'2026-11-01')['rate_excluding_vat_rub_per_kg'],'622')
        self.assertIsNone(self.data.tariff(key,'2025-12-31'))

    def test_next_published_flight_is_conditional_not_recommendation(self):
        r=calculate_chukotavia(self.request(),self.data);c=r['shipments'][0]['candidates'][0]
        self.assertEqual(c['departure_at'],'2026-11-10T10:00:00+12:00')
        self.assertEqual(c['arrival_at'],'2026-11-10T12:00:00+12:00')
        self.assertEqual(c['transport_component_estimate_rub'],75884)
        self.assertIsNone(c['total_cost_rub']);self.assertFalse(c['booking_confirmed'])
        self.assertIsNone(c['scenario_within_deadline']);self.assertIsNone(r['recommendation'])
        self.assertFalse(c['arrival_is_cargo_delivery'])

    def test_exact_volume_divisor_not_rounded_167(self):
        p=self.request();p['cargoes'][0].update(weight_tonnes=.01,volume_m3=1)
        r=calculate_chukotavia(p,self.data)
        c=r['shipments'][0]['candidates'][0]
        self.assertEqual(c['transport_component_estimate_rub'],126473.33)
        self.assertNotEqual(c['transport_component_estimate_rub'],758.84*167)

    def test_tariff_date_is_departure_not_cargo_ready_date(self):
        p=self.request();p['schedule']['ready_at']='2026-10-31T09:00:00+12:00'
        r=calculate_chukotavia(p,self.data)
        self.assertEqual(r['shipments'][0]['candidates'][0]['published_tariff']['source_id'],'chukotavia-463')

    def test_no_schedule_no_invented_departures_or_all_in_price(self):
        p=self.request();p['schedule']['ready_at']='2027-01-01T09:00:00+12:00'
        r=calculate_chukotavia(p,self.data)
        self.assertEqual(r['shipments'][0]['candidates'],[])
        self.assertEqual(r['status'],'insufficient_data')
        p=self.request();p.update(origin='Билибино',destination='Певек')
        self.assertEqual(calculate_chukotavia(p,self.data)['status'],'insufficient_data')

    def test_special_cargo_and_heavy_piece_not_priced_as_ordinary(self):
        for extra in ({'hazardous':True},{'temperature_control_required':True},{'max_piece_weight_kg':80}):
            p=self.request();p['cargoes'][0].update(extra)
            c=calculate_chukotavia(p,self.data)['shipments'][0]['candidates'][0]
            self.assertEqual(c['status'],'requires_special_handling')
            self.assertIsNone(c['transport_component_estimate_rub'])

    def test_user_scenario_cutoff_and_deadline_remain_conditional(self):
        p=self.request();p['schedule']['ready_at']='2026-11-10T09:00:00+12:00'
        p['scenario']={'cutoff_hours':3,'origin_processing_hours':2,'destination_processing_hours':4}
        c=calculate_chukotavia(p,self.data)['shipments'][0]['candidates'][0]
        self.assertEqual(c['departure_at'],'2026-11-24T10:00:00+12:00')
        self.assertFalse(c['scenario_within_deadline']);self.assertFalse(c['deadline_confirmed'])

    def test_same_flight_stops_and_seasonal_hours(self):
        flights=[f for fs in self.data.flights.values() for f in fs]
        jan=next(f for f in flights if f['origin']=='Эгвекинот' and f['destination']=='Лаврентия' and f['date']=='2026-01-21')
        feb=next(f for f in flights if f['origin']=='Эгвекинот' and f['destination']=='Лаврентия' and f['date']=='2026-02-03')
        self.assertIn('T09:00',jan['departure_at']);self.assertIn('T10:00',feb['departure_at'])
        p=self.request();p.update(destination='Окружной',via=['Усть-Белая','Снежное'])
        p['schedule']['ready_at']='2026-01-01T00:00:00+12:00'
        c=calculate_chukotavia(p,self.data)['shipments'][0]['candidates'][0]
        self.assertEqual(c['via'],['Усть-Белая','Снежное'])
        self.assertIn('tariff-442-5',c['published_tariff']['id'])

    def test_regular_multiple_cargo_and_no_shared_capacity_claim(self):
        p=self.request();p['cargoes'].append(dict(p['cargoes'][0],id='second',weight_tonnes=.1))
        p['schedule']={'mode':'regular','start_at':'2026-11-08T00:00:00+12:00','end_at':'2026-11-10T00:00:00+12:00','frequency':{'count':1,'every':1,'unit':'day'}}
        r=calculate_chukotavia(p,self.data)
        self.assertEqual(r['overview']['shipment_count'],2)
        self.assertEqual(r['overview']['planned_volume_tonnes'],.4)
        self.assertIsNone(r['overview']['total_cost_rub'])
        self.assertFalse(r['overview']['all_candidates_booking_confirmed'])

    def test_all_flight_matches_are_ordered_unflagged_and_within_2026(self):
        for fs in self.data.flights.values():
            for f in fs:
                self.assertFalse(f['flags'])
                self.assertLess(datetime.fromisoformat(f['departure_at']),datetime.fromisoformat(f['arrival_at']))
        self.assertEqual(len(self.data.data['issues']),6)
        self.assertEqual(self.data.options()['excluded_providers'],['aerogruz'])


class AviationApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def payload(self):return json.loads((BACKEND_DIR/'examples/chukotavia-request.json').read_text())

    def test_auth_security_and_dispatch(self):
        p=self.payload()
        for path in ['/network/aviation/calculations','/calculations']:
            r=self.client.post(path,json=p)
            self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['engine'],'chukotavia')
            self.assertIsNone(r.json()['recommendation'])
            self.assertEqual(r.headers['cache-control'],'no-store')
        self.assertEqual(self.client.post('/network/aviation/calculations',json=p,headers={'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post('/network/aviation/calculations',content='{}',headers={'Content-Type':'text/plain'}).status_code,415)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/network/aviation/options').status_code,401)
        self.assertEqual(self.client.post('/network/aviation/calculations',json=p).status_code,401)

    def test_options_and_input_errors(self):
        o=self.client.get('/network/aviation/options').json()
        self.assertEqual(len(o['routes']),156)
        self.assertEqual(self.client.get('/network/options').json()['aviation_provider']['id'],'chukotavia')
        for extra in [{'tariff':1},{'engine':'aerogruz'}]:
            self.assertEqual(self.client.post('/network/aviation/calculations',json=dict(self.payload(),**extra)).status_code,422)
        p=self.payload();p['cargoes'][0]['volume_m3']=0
        self.assertEqual(self.client.post('/network/aviation/calculations',json=p).status_code,422)
        p=self.payload();p['schedule']['ready_at']='2026-11-08T10:00:00'
        self.assertEqual(self.client.post('/network/aviation/calculations',json=p).status_code,422)
