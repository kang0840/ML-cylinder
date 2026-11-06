import base64
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import zlib

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from src import noise_snapshot as codec
from src import monitoring_noise as server
spec=importlib.util.spec_from_file_location('noise_publisher',ROOT/'deploy/pi_noise_publisher.py')
publisher=importlib.util.module_from_spec(spec);spec.loader.exec_module(publisher)
URL='https://test.supabase.co';SOURCE=hashlib.sha256(URL.encode()).hexdigest()
SID='9c1c6a0a-0078-4f39-a52f-4f61f913291c'


def snapshot():
    return dict(version=1,cylinder_id='cylinder_05',session_id=SID,source_id=SOURCE,
        profile_sha256='a'*64,filtered_at=datetime.now(timezone.utc).isoformat(),
        rows=[[123,8,'2026-11-06T04:00:00+00:00',.001,.0004,.002,.0008]])


def response():
    row=dict(cylinder_id='cylinder_05',session_id=SID,sequence_id=8,
        packet_metrics={s:dict(rms=v*(2**31)) for s,v in (('sph0645',.001),('inmp441',.002))})
    return dict(history=[row],latest=deepcopy(row))


class Repo:
    def __init__(self,data):self.data=data;self.queries=0
    def _get_client(self):return self
    def table(self,name):assert name=='admin_settings';return self
    def select(self,col):assert col=='setting_value';return self
    def eq(self,key,value):assert value=='monitoring_noise_v1_cylinder_05';return self
    def limit(self,n):assert n==1;return self
    def execute(self):self.queries+=1;return types.SimpleNamespace(data=[dict(setting_value=codec.encode(self.data))])


class NoiseSnapshotTests(unittest.TestCase):
    def setUp(self):server._CACHE.clear()

    def test_codec_roundtrip_and_rejects_bomb_and_oversized_rows(self):
        s=snapshot();self.assertEqual(codec.decode(codec.encode(s)),s)
        bomb=json.dumps(dict(encoding='zlib-base64',data=base64.b64encode(zlib.compress(b'x'*(codec.MAX_DECODED+1))).decode()))
        with self.assertRaises(ValueError):codec.decode(bomb)
        s['rows']*=101
        with self.assertRaises(ValueError):codec.encode(s)

    def test_exact_packet_and_original_rms_match_and_units(self):
        r=server.attach_noise_rms(response(),'cylinder_05',Repo(snapshot()),URL)
        self.assertEqual(r['noise_reduction_status'],'READY')
        self.assertEqual(r['latest']['noise_reduction']['sensors']['sph0645']['rms'],.0004*(2**31))
        self.assertEqual(r['latest']['packet_metrics']['sph0645']['rms'],.001*(2**31))

    def test_wrong_session_sequence_source_or_original_rms_does_not_attach(self):
        for field,value in [('session_id','06fc87cc-1104-4bdc-ad4f-4e5b53fba6c4'),('source_id','f'*64)]:
            server._CACHE.clear();s=snapshot();s[field]=value
            r=server.attach_noise_rms(response(),'cylinder_05',Repo(s),URL)
            self.assertNotIn('noise_reduction',r['latest'])
        server._CACHE.clear();s=snapshot();s['rows'][0][1]=9
        self.assertNotIn('noise_reduction',server.attach_noise_rms(response(),'cylinder_05',Repo(s),URL)['latest'])
        server._CACHE.clear();s=snapshot();s['rows'][0][3]=.5
        self.assertNotIn('noise_reduction',server.attach_noise_rms(response(),'cylinder_05',Repo(s),URL)['latest'])

    def test_cache_and_stale_status(self):
        s=snapshot();s['filtered_at']='2020-01-01T00:00:00+00:00';repo=Repo(s)
        r=server.attach_noise_rms(response(),'cylinder_05',repo,URL)
        server.attach_noise_rms(response(),'cylinder_05',repo,URL)
        self.assertEqual(r['noise_reduction_status'],'STALE');self.assertEqual(repo.queries,1)

    def test_no_reference_device_never_borrows_another_pico(self):
        r=server.attach_noise_rms(response(),'cylinder_02',None,URL)
        self.assertEqual(r['noise_reduction_status'],'REFERENCE_DATA_REQUIRED')

    def test_publisher_sends_only_bounded_scalar_snapshot(self):
        s=snapshot();s['rows']=s['rows'][:1]
        rec=dict(kind='DERIVED_NOISE_REDUCED_PACKET',cylinder_id='cylinder_05',session_id=SID,
            source_profile_sha256='a'*64,source_database_id=SOURCE,generated_at=s['filtered_at'],
            source_raw_id=123,sequence_id=8,source_received_at=s['rows'][0][2],
            sensors={name:dict(data='PRIVATE_WAVEFORM',original_ac_rms=v,reduced_rms=v*.4)
                     for name,v in (('sph0645',.001),('inmp441',.002))})
        captured=[]
        class HTTP:
            status=201
            def __enter__(self):return self
            def __exit__(self,*_):pass
        def send(req,**kwargs):captured.append(req);return HTTP()
        funcs={name:getattr(codec,name) for name in ('CHANNELS','from_records','encode')}
        with patch.object(publisher,'tail_records',lambda p:[rec] if 'pico05' in str(p) else []),patch.object(publisher.urllib.request,'urlopen',send):
            sent={};publisher.publish_once(ROOT,funcs,URL,'PRIVATE_KEY',sent)
            publisher.publish_once(ROOT,funcs,URL,'PRIVATE_KEY',sent)
        self.assertEqual(len(captured),1)
        body=json.loads(captured[0].data)
        decoded=codec.decode(body['setting_value'])
        self.assertEqual(len(decoded['rows']),1)
        self.assertNotIn('PRIVATE_WAVEFORM',str(decoded));self.assertNotIn('PRIVATE_KEY',str(body))
        self.assertEqual(body['setting_key'],'monitoring_noise_v1_cylinder_05')


if __name__=='__main__':unittest.main()
