import json
from pathlib import Path
import unittest

from backend import test_calculation_api as helpers


class NetworkApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def payload(self):
        return json.loads((Path(__file__).parent/'examples/network-request.json').read_text())

    def test_network_auth_and_options(self):
        options=self.client.get('/network/options')
        self.assertEqual(options.status_code,200)
        self.assertEqual(len(options.json()['categories']),7)
        self.assertEqual(len(options.json()['hubs']),6)
        self.assertEqual(options.json()['observations']['sources'],[])
        sources=self.client.get('/network/sources').json()
        self.assertEqual(len(sources['entries']),31)
        self.client.cookies.clear()
        for path in ('/network/options','/network/sources'):
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.post('/network/calculations',json=self.payload()).status_code,401)

    def test_new_endpoint_and_compatible_dispatch(self):
        outputs=[]
        for path in ('/network/calculations','/calculations'):
            response=self.client.post(path,json=self.payload())
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers['cache-control'],'no-store')
            outputs.append(response.json())
        self.assertEqual(outputs[0],outputs[1])
        self.assertEqual(outputs[0]['candidates'][0]['total_cost_rub'],100071.75)
        legacy=self.client.post('/calculations',json=self.request)
        self.assertEqual(legacy.status_code,200)
        self.assertEqual(legacy.json()['recommendation'],'contract_road')

    def test_security_and_validation(self):
        self.assertEqual(self.client.post('/network/calculations',json=self.payload(),headers={'Origin':'https://foreign.example'}).status_code,403)
        self.assertEqual(self.client.post('/network/calculations',content='{}',headers={'Content-Type':'text/plain'}).status_code,415)
        self.assertEqual(self.client.post('/network/calculations',json=dict(self.payload(),tariff=1)).status_code,422)
        self.assertEqual(self.client.post('/network/calculations',json=dict(self.payload(),network_id='unknown')).status_code,422)
        invalid=self.payload();invalid['max_delivery_hours']=0.1
        response=self.client.post('/network/calculations',json=invalid)
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()['recommendation'])

    def test_openapi_and_shared_rate_limit(self):
        spec=self.client.get('/openapi.json').json()
        self.assertIn('/network/calculations',spec['paths'])
        self.assertFalse(spec['components']['schemas']['NetworkRequest']['additionalProperties'])
        for i in range(30):
            endpoint='/calculations' if i%2 else '/network/calculations'
            self.assertEqual(self.client.post(endpoint,json=self.payload()).status_code,200)
        self.assertEqual(self.client.post('/network/calculations',json=self.payload()).status_code,429)
