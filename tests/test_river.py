from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
import unittest

from tests import BACKEND_DIR

from backend.network.engine import calculate_network
from backend.network.graph import FreightNetwork
from tests import test_calculation_api as helpers

ROOT=BACKEND_DIR


class RiverTests(unittest.TestCase):
    def setUp(self):
        self.catalog=json.loads((ROOT/'data_sources/river/network.json').read_text())
        self.payload=json.loads((ROOT/'examples/river-request.json').read_text())

    def calc(self):return calculate_network(self.payload,FreightNetwork(self.catalog))
    def first(self):return self.calc()['candidates'][0]
    def service(self,id='river-20-down'):return next(s for s in self.catalog['services'] if s['id']==id)

    def test_published_prices_synthetic_schedule_and_sources(self):
        r=self.calc();c=r['candidates'][0]
        self.assertEqual(c['total_cost_rub'],166200)
        charges={x['kind']:x['amount_rub'] for x in c['cost_items'] if x['amount_rub']}
        self.assertEqual(charges,{'transport':133000,'origin_container_handling':12000,'destination_container_handling':20000,'storage':1200})
        self.assertEqual(c['evidence_levels'],['assumption','fact'])
        self.assertEqual(r['calculation_mode'],'scenario')
        self.assertIn('синтетические',r['coverage'])
        self.assertEqual(self.service()['quote']['evidence']['status'],'fact')
        self.assertEqual(self.service()['evidence']['status'],'assumption')
        self.assertTrue(any('не подтверждённая' in w for w in r['warnings']))

    def test_two_containers_pay_handling_twice(self):
        self.payload['cargoes'][0].update(weight_tonnes=19,volume_m3=29)
        c=self.first()
        self.assertEqual(next(s['units'] for s in c['steps'] if s['kind']=='transport'),2)
        self.assertEqual(next(x['amount_rub'] for x in c['cost_items'] if x['kind']=='origin_container_handling'),24000)
        self.assertEqual(next(x['amount_rub'] for x in c['cost_items'] if x['kind']=='destination_container_handling'),40000)
        self.assertEqual(sum(x['amount_rub'] for x in c['cost_items'] if x['kind']=='storage'),2400)

    def test_volume_capacity_and_size_selection(self):
        self.payload['cargoes'][0].update(weight_tonnes=10,volume_m3=29)
        r=self.calc();twenty,forty=r['candidates']
        self.assertEqual(next(s['units'] for s in twenty['steps'] if s['kind']=='transport'),2)
        self.assertEqual(next(s['units'] for s in forty['steps'] if s['kind']=='transport'),1)
        self.assertEqual(r['recommendation'],forty['id'])

    def test_navigation_and_deadline_fail_without_fake_totals(self):
        for date in ['2026-11-08T00:00:00+07:00','2027-06-08T00:00:00+07:00']:
            self.payload['schedule']['ready_at']=date
            r=self.calc();self.assertIsNone(r['recommendation'])
            self.assertTrue(all(c['total_cost_rub'] is None for c in r['candidates']))
        self.payload=json.loads((ROOT/'examples/river-request.json').read_text())
        self.payload['max_delivery_hours']=48
        self.assertIsNone(self.calc()['recommendation'])

    def test_cutoff_uses_completed_handling(self):
        self.payload['schedule']['ready_at']='2026-06-10T08:00:00+07:00'
        # Three hours handling misses the synthetic cutoff of Jun 10, 10:00.
        c=self.first();step=next(s for s in c['steps'] if s['kind']=='transport')
        self.assertIn('2026-06-18',step['departure_at'])

    def test_vat_uses_published_five_percent(self):
        self.payload['cost_basis']='excluding_vat'
        c=self.first()
        transport=next(x['amount_rub'] for x in c['cost_items'] if x['kind']=='transport')
        self.assertEqual(transport,126666.67)
        self.assertNotEqual(transport,round(133000/1.22,2))

    def test_dudinka_free_storage_three_days(self):
        self.catalog['hubs'][1]['exit']['fixed_hours']=80
        c=self.first();storage=[x['amount_rub'] for x in c['cost_items'] if x['kind']=='storage']
        self.assertEqual(storage,[1200,400])
        # Free period ends exactly at 72h, no invented extra full day.
        self.catalog['hubs'][1]['exit']['fixed_hours']=71
        c=self.first();self.assertEqual([x['amount_rub'] for x in c['cost_items'] if x['kind']=='storage'],[1200,0])

    def test_verified_only_and_unknown_cargo(self):
        self.payload['data_policy']='verified_only'
        self.assertIsNone(self.calc()['recommendation'])
        self.payload['data_policy']='allow_assumptions'
        del self.payload['cargoes'][0]['properties']
        self.assertEqual(self.calc()['input_status'],'needs_clarification')

    def test_hazardous_liquid_temperature_and_oversize_rejected(self):
        for props in [{'hazardous':True},{'phase':'liquid'},{'temperature_control_required':True,'required_temperature_c':[2,8]}]:
            p=deepcopy(self.payload);p['cargoes'][0]['properties'].update(props)
            r=calculate_network(p,FreightNetwork(self.catalog));self.assertIsNone(r['recommendation'])
        self.payload['cargoes'][0]['max_piece_dimensions_m']=[3,3,3]
        self.assertIsNone(self.calc()['recommendation'])

    def test_reverse_uses_reverse_fees_and_travel_time(self):
        self.payload['origin_hub_id'],self.payload['destination_hub_id']=self.payload['destination_hub_id'],self.payload['origin_hub_id']
        c=self.first();self.assertEqual(c['service_ids'],['river-20-up'])
        self.assertEqual(next(x['amount_rub'] for x in c['cost_items'] if x['kind']=='origin_container_handling'),20000)
        step=next(s for s in c['steps'] if s['kind']=='transport')
        self.assertEqual((datetime.fromisoformat(step['arrival_at'])-datetime.fromisoformat(step['departure_at'])).total_seconds()/3600,192)

    def test_missing_volume_does_not_pass_mass_only(self):
        self.payload['capacity_basis']='mass_only';del self.payload['cargoes'][0]['volume_m3']
        self.assertIsNone(self.calc()['recommendation'])


class RiverApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp
    def test_catalog_selection_and_calculation_dispatch(self):
        p=json.loads((ROOT/'examples/river-request.json').read_text())
        o=self.client.get('/network/options',params={'network_id':p['network_id']})
        self.assertEqual(o.status_code,200,o.text)
        hubs={hub['id'] for hub in o.json()['hubs']}
        self.assertEqual(hubs,{prefix+hub for prefix in ('','krasrp-2026:','enisey-nord-2026:')
                               for hub in (p['origin_hub_id'],p['destination_hub_id'])})
        for path in ['/network/calculations','/calculations']:
            r=self.client.post(path,json=p);self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(r.json()['candidates'][0]['total_cost_rub'],166200)
        self.assertEqual(self.client.get('/network/options',params={'network_id':'unknown'}).status_code,422)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/network/options',params={'network_id':p['network_id']}).status_code,200)
