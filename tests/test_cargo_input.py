import copy
import json
import unittest

from tests import BACKEND_DIR

from backend.network.engine import calculate_network
from backend.network.graph import FreightNetwork
from backend.network.models import NetworkResponse
from backend.schedule import build_schedule
from tests import test_calculation_api as helpers


class CargoInputTests(unittest.TestCase):
    def setUp(self):
        root=BACKEND_DIR/'examples'
        self.catalog=json.loads((root/'network.json').read_text())
        self.request=json.loads((root/'network-request.json').read_text())
        self.request['cargoes'][0].pop('cargo_type',None)
        self.request['cargoes'][0]['name']='Мука'
        self.request['required_modes']=['road']

    def result(self):
        return calculate_network(self.request,FreightNetwork(self.catalog))

    def test_known_name_and_explicit_alias_preserve_price(self):
        for name in ['Мука','  МУКА  ','пшеничная мука']:
            self.request['cargoes'][0]['name']=name
            result=self.result();NetworkResponse.model_validate(result)
            self.assertEqual(result['input_status'],'ready')
            self.assertEqual(result['cargo_resolution'][0]['resolved_type_id'],'flour')
            self.assertEqual(result['candidates'][0]['status'],'feasible')

    def test_unknown_name_requests_properties_without_substitution(self):
        self.request['cargoes'][0]['name']='Керамические вазы'
        result=self.result()
        self.assertEqual(result['input_status'],'needs_clarification')
        self.assertEqual(result['candidates'],[])
        self.assertIsNone(result['recommendation'])
        self.assertEqual(result['cargo_resolution'][0]['resolution'],'needs_clarification')
        self.assertIn('properties.hazardous',result['clarifications'][0]['fields'])

    def declare(self,profile='dry_food'):
        self.request['cargoes'][0].update(name='Новая пищевая крупа',properties={
            'phase':'solid','hazardous':False,'handling_profile':profile,'temperature_control_required':False})

    def test_declared_cargo_requires_explicit_profile_admission(self):
        self.declare()
        result=self.result()
        self.assertEqual(result['candidates'][0]['status'],'feasible')
        self.assertIn('assumption',result['candidates'][0]['evidence_levels'])
        self.assertTrue(result['cargo_resolution'][0]['resolved_type_id'].startswith('declared-'))
        self.assertIn('Новая пищевая крупа',json.dumps(result,ensure_ascii=False))
        for obj in self.catalog['hubs']+self.catalog['services']:
            obj['cargo_profiles']=[]
        result=self.result()
        self.assertEqual(result['candidates'][0]['status'],'insufficient_data')
        self.assertEqual(result['candidates'][0]['issues'][0]['code'],'cargo_admission_unconfirmed')

    def test_no_alias_guessing_and_ambiguous_alias(self):
        self.request['cargoes'][0]['name']='мука с опасной добавкой'
        self.assertEqual(self.result()['input_status'],'needs_clarification')
        self.catalog['cargo_types']['sugar']['aliases']=['Мука']
        self.request['cargoes'][0]['name']='Мука'
        self.assertEqual(self.result()['clarifications'][0]['code'],'ambiguous_cargo_name')

    def test_name_and_legacy_id_must_agree(self):
        self.request['cargoes'][0].update(name='Лак',cargo_type='flour')
        self.assertEqual(self.result()['input_status'],'needs_clarification')
        self.request['cargoes'][0].update(name='Сахар',cargo_type='flour')
        self.assertEqual(self.result()['clarifications'][0]['code'],'conflicting_cargo_identity')

    def test_missing_packaging_divisibility_and_temperature_are_questions(self):
        self.request['cargoes'][0].pop('packaging')
        self.request['cargoes'][0].pop('splittable')
        self.assertEqual(len(self.result()['clarifications']),2)
        self.declare();self.request['cargoes'][0]['properties']['temperature_control_required']=True
        self.assertTrue(any(q['code']=='missing_temperature_range' for q in self.result()['clarifications']))

    def test_dangerous_declaration_and_pipeline_not_bypassed(self):
        self.declare();self.request['cargoes'][0]['properties']['hazardous']=True
        self.assertNotEqual(self.result()['candidates'][0]['status'],'feasible')
        self.declare();self.request['required_modes']=['pipeline']
        self.assertNotEqual(self.result()['candidates'][0]['status'],'feasible')

    def test_verified_only_rejects_user_declaration_and_no_catalog_mutation(self):
        self.declare();before=copy.deepcopy(self.catalog)
        self.request['data_policy']='verified_only'
        self.assertNotEqual(self.result()['candidates'][0]['status'],'feasible')
        self.assertEqual(before,self.catalog)
        self.request['data_policy']='allow_assumptions'
        self.assertEqual(self.result(),self.result())

    def test_multiple_names_do_not_imply_cargo_compatibility(self):
        self.declare()
        self.request['cargoes'].append(dict(self.request['cargoes'][0],id='other',name='Другая крупа'))
        result=self.result()
        self.assertEqual(result['candidates'][0]['status'],'insufficient_data')
        self.assertEqual(result['candidates'][0]['issues'][0]['code'],'unknown_compatibility')

    def test_arbitrary_period_and_multiple_intermediate_points(self):
        self.request['schedule']={'mode':'regular','start_at':'2026-11-09T00:00:00+03:00','end_at':'2026-11-15T00:00:00+03:00','frequency':{'count':2,'every':3,'unit':'day'}}
        self.assertEqual(self.result()['shipments'],4)
        self.request['schedule']={'mode':'once','ready_at':'2026-11-09T08:00:00+03:00'}
        self.request['required_modes']=['road','rail','air','sea']
        self.request['via_hub_ids']=['rail_hub','air_hub','sea_hub']
        self.assertEqual(self.result()['candidates'][0]['status'],'feasible')
        self.request['via_hub_ids'].reverse()
        self.assertEqual(self.result()['candidates'],[])

    def test_hourly_and_fractional_hour_period_via_minutes(self):
        self.request['schedule']={'mode':'regular','start_at':'2026-11-09T00:00:00+03:00','end_at':'2026-11-09T03:00:00+03:00','frequency':{'count':2,'every':90,'unit':'minute'}}
        self.assertEqual(self.result()['shipments'],4)
        self.request['schedule']['frequency']={'count':1,'every':2,'unit':'hour'}
        self.assertEqual(self.result()['shipments'],2)


class CargoApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def test_free_name_questions_and_options(self):
        payload=json.loads((self.example_dir/'network-request.json').read_text())
        payload['cargoes'][0].pop('cargo_type',None);payload['cargoes'][0]['name']='Неизвестный товар'
        response=self.client.post('/network/calculations',json=payload)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['input_status'],'needs_clarification')
        self.assertFalse(response.json()['candidates'])
        options=self.client.get('/network/options').json()
        self.assertEqual(options['cargo_input']['mode'],'free_text')
        self.assertTrue(options['schedule_options']['every_supported'])

    def test_original_calculation_endpoint_also_accepts_free_names(self):
        self.request['cargoes'][0].pop('cargo_type')
        self.request['cargoes'][0]['name']='Мука'
        response=self.client.post('/calculations',json=self.request)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['recommendation'],'contract_road')
        self.request['cargoes'][0]['name']='Новый неизвестный товар'
        response=self.client.post('/calculations',json=self.request)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['input_status'],'needs_clarification')
        self.assertIsNone(response.json()['recommendation'])
        self.assertTrue(all(s['status']=='insufficient_data' for s in response.json()['schemes']))
