import copy
import json
from pathlib import Path
import tempfile
import unittest

from tests import BACKEND_DIR

from backend.network.references import coordinates, import_references, normalize_ports, quantity, read_port_scrape
from backend.network.observations import coverage

ROOT=BACKEND_DIR/'data_sources'


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'observations.sqlite3'

    def test_ports_snapshot_has_only_reference_locations(self):
        data=json.loads((ROOT/'rosmorport/references.json').read_text())
        self.assertEqual(len(data['ports']),54)
        self.assertEqual(sum(p['scrape_status']=='detailed' for p in data['ports']),18)
        self.assertEqual(sum(p['coordinates'] is not None for p in data['ports']),18)
        self.assertTrue(all(p['operational_hub'] is False and p['services']==[] and p['available_capacity'] is None for p in data['ports']))
        self.assertEqual(data['freight_services_created'],0)

    def test_units_are_not_mixed(self):
        self.assertEqual(quantity('4 312,6 тыс. ДФЭ в год;')['value'],'4312600.0')
        self.assertEqual(quantity('4 312,6 тыс. ДФЭ в год;')['unit'],'TEU_per_year')
        self.assertEqual(quantity('111 004,39 тыс. тонн в год, в том числе:')['value'],'111004390.00')
        self.assertEqual(quantity('149,1971 тыс. кв. м;')['unit'],'m2')
        self.assertIsNone(quantity('от 2 до 5 млн тонн'))
        self.assertIsNone(quantity('не указано'))
        self.assertEqual(quantity('0 тонн')['value'],'0')

    def test_coordinate_precision_and_ambiguity(self):
        point=coordinates("59°42'43'' с. ш. 28°23'49'' в.д.")
        self.assertAlmostEqual(point['latitude'],59+42/60+43/3600)
        self.assertEqual(point['role'],'reported_port_location_not_terminal')
        self.assertIsNone(coordinates("91°00' с.ш. 28°00' в.д."))
        self.assertIsNone(coordinates("59°00' с.ш. 60°00' с.ш. 28°00' в.д."))

    def test_context_import_is_idempotent_and_does_not_overwrite_other_sources(self):
        port=ROOT/'rosmorport/references.json';strategy=ROOT/'strategy/context.json'
        first=import_references(port,self.db)
        self.assertEqual(first,import_references(port,self.db))
        import_references(strategy,self.db)
        records=coverage(self.db)['sources'];self.assertEqual(len(records),2)
        self.assertEqual({r['rows'] for r in records},{54,32})
        self.assertTrue(all(r['freight_services_created']==0 for r in records))

    def test_strategy_values_keep_forecast_status(self):
        data=json.loads((ROOT/'strategy/context.json').read_text())
        self.assertEqual(data['document_date'],'2021-11-27')
        self.assertEqual(data['pages'],285)
        self.assertFalse(data['current_revision_verified'])
        rows=data['scenario_values'];self.assertEqual(len(rows),32)
        self.assertTrue(all(not r['is_observed_actual'] for r in rows))
        self.assertTrue(all(r['status']=='scenario_projection_in_2021_document' for r in rows if r['year']==2024))

    def test_reference_import_rejects_operational_promotion(self):
        data=json.loads((ROOT/'rosmorport/references.json').read_text())
        data['ports'][0]['operational_hub']=True
        path=self.root/'bad.json';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):import_references(path,self.db)

    def test_duplicate_ports_and_unexpected_origins_rejected(self):
        record={'id':'one','name':'Порт','basin':'Бассейн','branch':'Филиал','source_url':'https://www.rosmorport.ru/test/','collected_at':'2026-11-08T17:00:00+03:00','status':'directory_only','sections':[],'technical_characteristics':[]}
        path=self.root/'ports.json';path.write_text(json.dumps([record,record]))
        with self.assertRaises(ValueError):read_port_scrape(path)
        record['source_url']='https://foreign.example/test'
        path.write_text(json.dumps([record]))
        with self.assertRaises(ValueError):normalize_ports(path)
