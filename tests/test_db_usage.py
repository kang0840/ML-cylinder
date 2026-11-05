import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('db_usage', ROOT/'tools/build_db_usage.py')
usage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usage)


class UsageTests(unittest.TestCase):
    def make_db(self, folder, missing=False, wrong_physical=False):
        import runpy
        model = runpy.run_path(str(ROOT/'tools/lifetime_condition_simulation.py'))
        path = Path(folder)/'pico05-analysis.sqlite3'
        db = sqlite3.connect(path)
        db.executescript('CREATE TABLE identity(key TEXT,value TEXT); CREATE TABLE sessions(session_id TEXT PRIMARY KEY,document TEXT); CREATE TABLE raw_data(id INTEGER PRIMARY KEY,session_id TEXT,sequence_id INTEGER,document TEXT);')
        db.execute('INSERT INTO identity VALUES (?,?)', ('cylinder_id', 'cylinder_05'))
        start = datetime(2026,11,3,tzinfo=timezone(timedelta(hours=9)))
        for i, sid in enumerate(sorted(model['CONFIRMED_STOP_SESSIONS'])):
            meta = dict(session_id=sid,cylinder_id='cylinder_05',physical_cylinder_id='OTHER' if wrong_physical else 'CYL-005',ground_truth='SEAL_LEAK')
            db.execute('INSERT INTO sessions VALUES (?,?)',(sid,json.dumps(meta)))
            for j in range(2):
                raw = dict(runtime={'last_received_at':(start+timedelta(seconds=i*20+j*10)).isoformat()})
                if missing and i==0 and j==0: raw={}
                row=dict(id=i*2+j+1,session_id=sid,cylinder_id='cylinder_05',raw_payload=raw)
                db.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(row['id'],sid,j+1,json.dumps(row)))
        db.commit();db.close()
        return path

    def test_auto_interval_from_db_preserves_unknown_lifetime_and_original(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'outputs') as folder:
            path=self.make_db(folder)
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            result=usage.build(Path(folder))['picos']
            five=result['cylinder_05']
            self.assertEqual(five['raw_packets'],12)
            self.assertAlmostEqual(five['estimated_interval_cycles'],60/2.6016069444444443)
            self.assertIsNone(five['actual_total_cycles'])
            self.assertIsNone(five['prior_lifetime_cycles'])
            self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIsNone(result['cylinder_03']['estimated_interval_cycles'])
            self.assertIsNone(result['cylinder_03']['raw_packets'])

    def test_missing_timing_and_different_physical_never_create_usage(self):
        for options in ({'missing':True},{'wrong_physical':True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory(dir=ROOT/'outputs') as folder:
                self.make_db(folder,**options)
                five=usage.build(Path(folder))['picos']['cylinder_05']
                self.assertEqual(five['raw_packets'],12)
                self.assertIsNone(five['estimated_interval_cycles'])
                self.assertIsNone(five['actual_total_cycles'])


if __name__=='__main__':unittest.main()
