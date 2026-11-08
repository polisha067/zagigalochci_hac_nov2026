import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from fastapi.testclient import TestClient
from backend import test_calculation_api as helpers


class ProxyTests(unittest.TestCase):
    setUp=helpers.CalculationApiTests.setUp

    def test_api_prefix_keeps_browser_protection_and_docs(self):
        with TestClient(self.app,root_path='/api') as client:
            response=client.get('/api/auth/me')
            self.assertEqual(response.status_code,401)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.post('/api/auth/register',json={},headers={'Origin':'https://foreign.example'}).status_code,403)
            self.assertEqual(client.post('/api/auth/register',content='{}',headers={'Content-Type':'text/plain'}).status_code,415)
            self.assertEqual(client.post('/api/auth/logout').status_code,204)
            spec=client.get('/api/openapi.json').json()
            self.assertIn({'url':'/api'},spec['servers'])


class BackupTests(unittest.TestCase):
    def test_wal_backup_is_consistent(self):
        path=Path(__file__).resolve().parents[1]/'deploy/backup.py'
        spec=importlib.util.spec_from_file_location('deployment_backup',path)
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);db=sqlite3.connect(root/'auth.sqlite3')
            self.addCleanup(db.close)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE users (name TEXT)');db.execute("INSERT INTO users VALUES ('test')");db.commit()
            output=root/'backup'
            self.assertEqual(helper.backup_databases(root,output),1)
            with sqlite3.connect(output/'auth.sqlite3') as saved:
                self.assertEqual(saved.execute('SELECT name FROM users').fetchone()[0],'test')
            with self.assertRaises(FileExistsError):helper.backup_databases(root,output)
