import base64
from copy import deepcopy
import importlib.util
from pathlib import Path
import struct
import sys
import types
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
spec=importlib.util.spec_from_file_location('display_rms',ROOT/'src/monitoring_rms.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def raw(seq=1,ch='cylinder_05'):
    payload=dict(cylinder_id=ch,session_id='9c1c6a0a-0078-4f39-a52f-4f61f913291c',
                 sequence_id=seq,timestamp='PI_RECEIVE_TIME',
                 runtime={'last_received_at':'2026-11-06T04:00:00+00:00'})
    for sensor,rate,n in (('sph0645',4000,256),('inmp441',16000,1024)):
        values=[99,101]*(n//2)
        payload[sensor]=dict(sample_rate=rate,sample_count=n,sample_format='s32le',encoding='base64',
                            data=base64.b64encode(struct.pack('<'+'i'*n,*values)).decode())
    return dict(id=seq,cylinder_id=ch,session_id=payload['session_id'],sequence_id=seq,raw_payload=payload)


def response(rows):
    values=[dict(cylinder_id=r['cylinder_id'],session_id=r['session_id'],sequence_id=r['sequence_id'],packet_metrics=None) for r in rows]
    return dict(history=values,latest=deepcopy(values[-1]) if values else None)


class Query:
    def __init__(self,rows,owner):self.rows=list(rows);self.owner=owner
    def select(self,columns):self.owner.columns.append(columns);return self
    def eq(self,k,v):self.rows=[r for r in self.rows if r[k]==v];return self
    def in_(self,k,values):self.rows=[r for r in self.rows if r[k] in values];return self
    def limit(self,n):self.rows=self.rows[:n];self.owner.limits.append(n);return self
    def execute(self):self.owner.queries+=1;return types.SimpleNamespace(data=deepcopy(self.rows))


class Repository:
    def __init__(self,rows):self.rows=rows;self.queries=0;self.columns=[];self.limits=[]
    def _get_client(self):return self
    def table(self,name):
        assert name=='raw_data';return Query(self.rows,self)


class DisplayRMSTests(unittest.TestCase):
    def setUp(self):module._CACHE.clear()

    def test_receive_only_raw_produces_real_ac_rms_without_exposing_pcm(self):
        r=raw();original=deepcopy(r);repo=Repository([r]);out=module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        self.assertEqual(out['latest']['packet_metrics']['sph0645']['rms'],1)
        self.assertEqual(out['latest']['packet_metrics']['inmp441']['sample_count'],1024)
        self.assertNotIn('raw_payload',out['latest']);self.assertNotIn('data',out['latest']['packet_metrics']['sph0645'])
        self.assertEqual(r,original);self.assertEqual(out['packet_metrics_status'],'READY')

    def test_repeated_poll_only_fetches_new_packet(self):
        r=raw();repo=Repository([r])
        module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        self.assertEqual(repo.queries,1)
        second=raw(3);repo.rows.append(second)
        out=module.fill_display_rms(response([r,second]),'cylinder_05',repo,'db1')
        self.assertEqual(repo.limits,[1,1]);self.assertEqual([v['sequence_id'] for v in out['history']],[1,3])

    def test_existing_metrics_not_replaced_and_database_cache_isolated(self):
        r=raw();repo=Repository([r]);out=response([r])
        out['latest']['packet_metrics']={'existing':True};out['history'][0]['packet_metrics']={'existing':True}
        module.fill_display_rms(out,'cylinder_05',repo,'db1');self.assertEqual(repo.queries,0)
        module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        module.fill_display_rms(response([r]),'cylinder_05',repo,'db2');self.assertEqual(repo.queries,2)

    def test_foreign_device_and_malformed_pcm_not_filled(self):
        foreign=raw(ch='cylinder_03');repo=Repository([foreign])
        result=module.fill_display_rms(response([foreign]),'cylinder_05',repo,'db1')
        self.assertIsNone(result['latest']['packet_metrics']);self.assertEqual(repo.queries,0)
        r=raw();r['raw_payload']['sph0645']['data']='invalid';repo=Repository([r])
        result=module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        self.assertIsNone(result['latest']['packet_metrics']);self.assertEqual(result['packet_metrics_status'],'DATA_REQUIRED')

    def test_payload_identity_must_match_selected_packet(self):
        r=raw();r['raw_payload']['sequence_id']=2;repo=Repository([r])
        result=module.fill_display_rms(response([r]),'cylinder_05',repo,'db1')
        self.assertIsNone(result['latest']['packet_metrics'])

    def test_bounded_batches_and_cache(self):
        rows=[raw(i+1) for i in range(120)];repo=Repository(rows)
        out=module.fill_display_rms(response(rows),'cylinder_05',repo,'db1')
        self.assertEqual(repo.limits,[50,50,20]);self.assertEqual(len(out['history']),120)
        self.assertLessEqual(len(module._CACHE),module._MAX_CACHE)

    def test_database_failure_remains_explicit_without_fabricated_zero(self):
        class Failed:
            def _get_client(self):raise RuntimeError('private credential text')
        result=module.fill_display_rms(response([raw()]),'cylinder_05',Failed(),'db1')
        self.assertIsNone(result['latest']['packet_metrics']);self.assertEqual(result['packet_metrics_status'],'UNAVAILABLE')
        self.assertNotIn('private',str(result))


if __name__=='__main__':unittest.main()
