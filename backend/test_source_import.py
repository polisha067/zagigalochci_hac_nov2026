import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile

from backend.network.observations import coverage, import_observations, numeric


class SourceImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=self.root/'observations.sqlite3'
        self.fields=['city_from','city_to','oktmo_from','oktmo_to','transport_type','distance_km','distance_hr','missing_reason']
        self.row=['Город А','Город Б','001','002','road','0','0','']

    def file(self,rows=None):
        path=self.root/'sample.csv'
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream,delimiter=';');writer.writerow(self.fields);writer.writerows(rows if rows is not None else [self.row])
        return path

    def test_zero_is_not_missing_and_codes_keep_leading_zeros(self):
        result=import_observations(self.file(),self.db,'connectivity')
        self.assertEqual(result['cities'],2);self.assertEqual(result['missing_routes'],0)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT from_oktmo,distance_km FROM connectivity_observations').fetchone(),('001',0.0))
        self.assertEqual(result['freight_services_created'],0)
        self.assertIsNone(numeric('ND'));self.assertEqual(numeric('0'),0)
        with self.assertRaises(ValueError):numeric('NaN')

    def test_idempotent_and_transaction_rollback_on_duplicate(self):
        path=self.file();first=import_observations(path,self.db,'connectivity')
        self.assertEqual(import_observations(path,self.db,'connectivity')['rows'],1)
        path=self.file([self.row,self.row])
        with self.assertRaises(sqlite3.IntegrityError):import_observations(path,self.db,'connectivity')
        self.assertEqual(coverage(self.db)['sources'][0]['file_sha256'],first['file_sha256'])
        with sqlite3.connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM connectivity_observations').fetchone()[0],1)

    def test_zip_skips_macos_metadata_without_extracting_paths(self):
        path=self.file();archive=self.root/'source.zip'
        with zipfile.ZipFile(archive,'w') as zipped:
            zipped.write(path,'folder/data.csv');zipped.writestr('__MACOSX/._data.csv','ignored')
        self.assertEqual(import_observations(archive,self.db,'connectivity')['rows'],1)
        self.assertFalse((self.root/'folder').exists())
        with zipfile.ZipFile(archive,'a') as zipped:zipped.writestr('second.csv','unexpected')
        with self.assertRaises(ValueError):import_observations(archive,self.db,'connectivity')

    def test_missing_route_and_schema_validation(self):
        missing=self.row[:];missing[5:]=['ND','ND','no route']
        self.assertEqual(import_observations(self.file([missing]),self.db,'connectivity')['missing_routes'],1)
        bad=self.row[:];bad[6]='ND'
        with self.assertRaises(ValueError):import_observations(self.file([bad]),self.db,'connectivity')
        self.fields=['unexpected']
        with self.assertRaises(ValueError):import_observations(self.file([['1']]),self.db,'connectivity')

    def test_unknown_unit_is_preserved_without_guessing(self):
        self.fields=['indicator_code','indicator_name','object_name','year','indicator_value','indicator_unit']
        rows=[['freight','Грузы','Регион','2024','42',''],['freight','Грузы','Регион','2025','ND','т']]
        result=import_observations(self.file(rows),self.db,'regional')
        self.assertEqual(result['missing_units'],1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT value,unit FROM regional_observations ORDER BY row_number').fetchall(),[(42.0,'unknown'),(None,'т')])

    def test_registry_has_all_31_without_duplicate_ids(self):
        registry=json.loads((Path(__file__).parent/'data_sources/registry.json').read_text())
        entries=registry['entries']
        self.assertEqual(len(entries),31);self.assertEqual(len({r['id'] for r in entries}),31)
        self.assertEqual(len({r['url'] for r in entries}),25)
        self.assertTrue(all(not r['usable_as_freight_tariff'] for r in entries))
