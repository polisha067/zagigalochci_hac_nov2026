import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from backend.network.asop import SNAPSHOT, import_asop, load_snapshot, read_asop
from backend.network.observations import coverage
from tests import test_calculation_api as helpers


class AsopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'observations.sqlite3'
        self.snapshot = load_snapshot()

    def test_all_pdf_sources_and_rows_have_provenance(self):
        self.assertEqual(len(self.snapshot['source_files']), 27)
        self.assertEqual(len(self.snapshot['observations']), 620)
        self.assertEqual(len(self.snapshot['issues']), 43)
        self.assertEqual(sum(bool(r['quality_flags']) for r in self.snapshot['observations']), 152)

    def test_idempotent_import_and_separate_periods(self):
        first = import_asop(SNAPSHOT, self.db)
        self.assertEqual(first, import_asop(SNAPSHOT, self.db))
        self.assertEqual(len(coverage(self.db)['sources']), 1)
        self.assertEqual(len(read_asop(self.db, year=2025, include_flagged=True)['rows']), 0)
        quarter = read_asop(self.db, year=2025, period_kind='Q1', include_flagged=True)['rows']
        self.assertEqual(len(quarter), 30)
        self.assertTrue(all(r['period_end_exclusive']=='2025-04-01' for r in quarter))
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM asop_observations').fetchone()[0],620)

    def test_million_tonnes_precision_and_unknown_units(self):
        import_asop(SNAPSHOT, self.db)
        rows = read_asop(self.db, period_kind='Q1', include_flagged=True)['rows']
        row = next(r for r in rows if r['metric']=='port_outbound' and r['category_id']=='river' and r['year']==2026)
        self.assertEqual(row['value_tonnes'],10000)
        self.assertEqual(row['rounding_half_step_source'],'0.005')
        unknown = [r for r in self.snapshot['observations'] if r['source_unit']=='unknown']
        self.assertEqual(len(unknown),20)
        self.assertTrue(all(r['value_tonnes'] is None for r in unknown))

    def test_flags_are_excluded_by_default_and_vintages_preserved(self):
        import_asop(SNAPSHOT, self.db)
        clean = read_asop(self.db)['rows']
        self.assertTrue(all(not r['quality_flags'] for r in clean))
        rows = read_asop(self.db, metric='basin', year=2023, include_flagged=True)['rows']
        baltic = [r for r in rows if r['category_id']=='baltic']
        self.assertEqual({r['source_value'] for r in baltic},{'248.6','271.3'})
        self.assertTrue(all('cross_publication_difference' in r['quality_flags'] for r in baltic))
        self.assertEqual(len({r['source_file_id'] for r in baltic}),2)

    def test_source_selection_and_sql_parameters(self):
        import_asop(SNAPSHOT,self.db)
        file = next(s for s in self.snapshot['source_files'] if s['filename']=='basseyny_i_napravleniya_1.pdf')
        rows=read_asop(self.db,year=2024,source_file_id=file['id'],include_flagged=True)['rows']
        self.assertEqual(len(rows),9)
        self.assertTrue(all(r['source_file_id']==file['id'] for r in rows))
        self.assertEqual(read_asop(self.db,source_file_id="' OR 1=1 --")['rows'],[])

    def test_invalid_snapshot_cannot_replace_existing_import(self):
        original=import_asop(SNAPSHOT,self.db)
        for mutation in ('units','period','duplicate'):
            data=copy.deepcopy(self.snapshot)
            if mutation=='units':data['observations'][0]['value_tonnes']=1
            elif mutation=='period':data['observations'][0]['period_kind']='year'
            else:data['observations'].append(data['observations'][0])
            path=self.root/'bad.json';path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):import_asop(path,self.db)
            self.assertEqual(coverage(self.db)['sources'][0],original)

    def test_missing_database_returns_explicit_empty_result(self):
        self.assertEqual(read_asop(self.db)['rows'],[])
        self.assertFalse(self.db.exists())


class AsopApiTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def test_endpoint_requires_auth_and_separates_quarter(self):
        db=Path(self.temp.name)/'network-observations.sqlite3'
        import_asop(SNAPSHOT,db)
        response=self.client.get('/network/statistics/asop',params={'year':2025})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['rows'],[])
        response=self.client.get('/network/statistics/asop',params={'year':2025,'period_kind':'Q1','include_flagged':'true'})
        self.assertEqual(len(response.json()['rows']),30)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get('/network/statistics/asop?period_kind=month').status_code,422)
        self.assertEqual(self.client.get('/network/options').json()['observations']['sources'][0]['files'],27)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/network/statistics/asop').status_code,200)
