"""Real PCM/SQLite tests for intermittent demonstration comparison; no production writes."""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import runpy
import sqlite3
import struct
import subprocess
import tempfile
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('demo',ROOT/'tools/demo_condition_analysis.py')
demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
model=runpy.run_path(str(ROOT/'tools/lifetime_condition_simulation.py'))
AT=datetime(2026,11,5,12,tzinfo=timezone(timedelta(hours=9)))


def payload(row_id, session, sequence, frequency=250, amplitude=3000, stamp=AT):
    raw=dict(cylinder_id='cylinder_05',session_id=session,sequence_id=sequence,timestamp=stamp.isoformat(),
             runtime={'last_received_at':stamp.isoformat()})
    for sensor,rate,count in [('sph0645',4000,256),('inmp441',16000,1024)]:
        samples=[round(amplitude*math.sin(2*math.pi*frequency*i/rate)) for i in range(count)]
        raw[sensor]=dict(sample_rate=rate,sample_count=count,sample_format='s32le',encoding='base64',
                         data=base64.b64encode(struct.pack('<%di'%count,*samples)).decode())
    return dict(id=row_id,cylinder_id='cylinder_05',session_id=session,sequence_id=sequence,raw_payload=raw)


class DemonstrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'outputs')
        self.folder=Path(self.tmp.name);self.path=self.folder/'pico05-analysis.sqlite3'
        self.db=sqlite3.connect(self.path)
        self.db.executescript('CREATE TABLE identity(key TEXT,value TEXT); CREATE TABLE sessions(session_id TEXT PRIMARY KEY,document TEXT); CREATE TABLE raw_data(id INTEGER PRIMARY KEY,session_id TEXT,sequence_id INTEGER,document TEXT);')
        self.db.execute('INSERT INTO identity VALUES (?,?)',('cylinder_id','cylinder_05'))
        self.normal=str(UUID(int=1));self.leak=str(UUID(int=2));self.operation=str(UUID(int=3))
        self.session(self.normal,'NORMAL','TRAINING','BASELINE')
        self.session(self.leak,'SEAL_LEAK','TRAINING','VALIDATION')
        for i in range(40):
            self.insert(payload(i+1,self.normal,i+1,amplitude=30+i if i<10 else 2400+i*30))
        for i in range(20):self.insert(payload(i+41,self.leak,i+1,frequency=500,amplitude=3000+i*30))
        self.db.commit()
        client=model['PicoSQLiteClient'](self.folder,'cylinder_05')
        self.reference=demo.build_reference(client,model,self.folder/'reference.json');client.close()
        self.session(self.operation,None,'OPERATION','UNASSIGNED')
        self.db.commit()
        self.original_now=demo.now;demo.now=lambda:AT

    def tearDown(self):
        demo.now=self.original_now;self.db.close();self.tmp.cleanup()

    def session(self,sid,label,mode,role,physical='CYL-005'):
        meta=dict(session_id=sid,cylinder_id='cylinder_05',physical_cylinder_id=physical,
                  ground_truth=label,collection_mode=mode,dataset_role=role,test_conditions={})
        self.db.execute('INSERT INTO sessions VALUES (?,?)',(sid,json.dumps(meta)))

    def insert(self,row):
        self.db.execute('INSERT INTO raw_data VALUES (?,?,?,?)',(row['id'],row['session_id'],row['sequence_id'],json.dumps(row)))

    def targets(self,frequency=250,amplitude=3000,count=5,start_id=61,sid=None,old=False):
        sid=sid or self.operation
        for i in range(count):
            stamp=AT-timedelta(seconds=(count-i-1)+(60 if old else 0))
            self.insert(payload(start_id+i,sid,i+1,frequency,amplitude,stamp))
        self.db.commit()

    def tick(self,floor=60):
        return demo.tick(self.folder,('cylinder_05',),{'cylinder_05':self.reference},
                         {'cylinder_05':floor},model)['picos']['cylinder_05']

    def test_new_normal_and_leak_sounds_compare_both_references_and_total_life(self):
        self.targets()
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        normal=self.tick()
        self.assertEqual(normal['status'],'DEMO_COMPARISON_READY')
        self.assertEqual(normal['active_candidates'],5)
        self.assertEqual(normal['reference_closer'],'NORMAL')
        self.assertIsNone(normal['scenario']['actual_age_cycles'])
        self.assertIsNone(normal['scenario']['remaining_cycles'])
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.targets(frequency=500,start_id=66)
        leak=self.tick(floor=65)
        self.assertEqual(leak['reference_closer'],'SEAL_LEAK')
        self.assertGreater(leak['condition_distance'],normal['condition_distance'])
        self.assertLess(leak['scenario']['mean_life_cycles'],normal['scenario']['mean_life_cycles'])

    def test_idle_missing_time_stale_and_pre_demo_data_never_generate_result(self):
        self.assertEqual(self.tick()['status'],'WAITING_NEW_DATA')
        self.targets(amplitude=30)
        self.assertEqual(self.tick()['status'],'LOW_ACOUSTIC_ACTIVITY')
        self.assertIsNone(self.tick()['scenario'])
        self.assertEqual(self.tick(floor=65)['status'],'WAITING_NEW_DATA')
        self.targets(start_id=66,old=True)
        self.assertEqual(self.tick(floor=65)['status'],'STALE_DEMO_DATA')
        self.targets(start_id=71)
        row=payload(76,self.operation,6);row['raw_payload'].pop('runtime');self.insert(row);self.db.commit()
        output=self.tick(floor=71)
        self.assertEqual(output['status'],'MORE_ACOUSTIC_CANDIDATES_REQUIRED')
        self.assertEqual(output['excluded_packets_by_reason']['MISSING_TIME_OR_INVALID_PCM'],1)

    def test_new_session_does_not_fill_candidate_count_with_previous_session(self):
        self.targets()
        sid=str(UUID(int=4));self.session(sid,None,'OPERATION','UNASSIGNED')
        self.targets(start_id=66,sid=sid,count=3)
        output=self.tick()
        self.assertEqual(output['session_id'],sid)
        self.assertEqual(output['active_candidates'],3)
        self.assertIsNone(output['scenario'])

    def test_reference_deletion_and_target_identity_mismatch_block_results(self):
        self.targets()
        self.db.execute('DELETE FROM raw_data WHERE id=1');self.db.commit()
        output=self.tick()
        self.assertEqual(output['reason'],'REFERENCE_SOURCE_CHANGED')
        self.assertIsNone(output['scenario'])

    def test_test_split_is_not_used_for_reference_and_cache_is_reused(self):
        sid=str(UUID(int=99));self.session(sid,'SEAL_LEAK','TRAINING','TEST')
        self.insert(payload(90,sid,1,frequency=1000,amplitude=9000));self.db.commit()
        client=model['PicoSQLiteClient'](self.folder,'cylinder_05')
        newer=demo.build_reference(client,model,self.folder/'reference.json')
        self.assertEqual(newer['fingerprint'],self.reference['fingerprint'])
        self.assertNotIn(sid,[x['session']['session_id'] for x in newer['reference_sessions']])
        client.close()

    def test_flat_activity_reference_cannot_claim_stop_or_motion_separation(self):
        self.assertEqual(demo.activity_gate([1.0]*40)['status'],'UNRESOLVED')

    def test_intermittent_candidates_survive_idle_packets_without_continuous_operation(self):
        buffers={}
        self.targets(count=3)
        def tick():
            return demo.tick(self.folder,('cylinder_05',),{'cylinder_05':self.reference},
                             {'cylinder_05':60},model,activity_buffers=buffers)['picos']['cylinder_05']
        self.assertEqual(tick()['active_candidates'],3)
        # A minute of low activity replaces all raw packets in the 50-packet view.
        for i in range(60):
            self.insert(payload(64+i,self.operation,4+i,amplitude=30,stamp=AT+timedelta(seconds=i+1)))
        self.db.commit();demo.now=lambda:AT+timedelta(seconds=60)
        self.assertEqual(tick()['status'],'LOW_ACOUSTIC_ACTIVITY')
        self.assertEqual(len(buffers['cylinder_05']),3)
        for i in range(2):self.insert(payload(124+i,self.operation,64+i,stamp=AT+timedelta(seconds=61+i)))
        self.db.commit();demo.now=lambda:AT+timedelta(seconds=62)
        result=tick()
        self.assertEqual(result['status'],'DEMO_COMPARISON_READY')
        self.assertEqual(result['active_candidates'],5)
        self.assertGreater(result['comparison_window_seconds'],60)
        self.assertIsNone(result['measured_cycle_count'])


class StateLifeMathTests(unittest.TestCase):
    def test_no_usage_required_and_python_browser_math_match(self):
        cases=[dict(referenceMean=m,shape=k,distance=d,influence=.25) for m in (100,167500) for k in (.5,2,7.51525) for d in (0,1,4)]
        code="const m=require(process.argv[1]);const a=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(a.map(m.condition)));"
        result=subprocess.run(['node','-e',code,str(ROOT/'public/lifetime-condition-math.js')],input=json.dumps(cases),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for case,actual in zip(cases,json.loads(result.stdout)):
            expected=demo.condition_lifetime(case['referenceMean'],case['shape'],case['distance'],case['influence'])
            self.assertAlmostEqual(actual['mean_life_cycles'],expected['mean_life_cycles'],delta=1e-6)
            self.assertAlmostEqual(actual['median_life_cycles'],expected['median_life_cycles'],delta=1e-6)
            self.assertIsNone(actual['actual_age_cycles']);self.assertIsNone(actual['remaining_cycles'])
            if case['distance']==0:self.assertAlmostEqual(actual['mean_life_cycles'],case['referenceMean'],places=6)
        for value in (0,-1,float('nan')):
            with self.assertRaises(ValueError):demo.condition_lifetime(value,2,1,.25)


if __name__=='__main__':unittest.main()
