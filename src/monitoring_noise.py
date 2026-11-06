"""Read Pi scalar snapshots after existing serial authorization; never expose PCM."""
from collections import OrderedDict
from datetime import datetime,timezone
import hashlib
import math
import os
import threading
import time

from src.noise_snapshot import decode, CHANNELS

_CACHE=OrderedDict()
_LOCK=threading.Lock()


def attach_noise_rms(result,cylinder,repository,source_key=None):
    if cylinder not in CHANNELS:
        result['noise_reduction_status']='REFERENCE_DATA_REQUIRED';return result
    url=source_key if source_key is not None else os.environ.get('SUPABASE_URL','')
    source=hashlib.sha256(url.rstrip('/').encode()).hexdigest();key=(source,cylinder)
    snapshot=None;failed=False
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and time.monotonic()-cached[0]<1:
            snapshot,failed=cached[1:]
        else:
            try:
                rows=(repository._get_client().table('admin_settings').select('setting_value')
                    .eq('setting_key','monitoring_noise_v1_'+cylinder).limit(1).execute().data or [])
                if rows:
                    snapshot=decode(rows[0]['setting_value'])
                    if snapshot['source_id']!=source or snapshot['cylinder_id']!=cylinder:
                        snapshot=None;failed=True
            except Exception:failed=True
            _CACHE[key]=(time.monotonic(),snapshot,failed);_CACHE.move_to_end(key)
            while len(_CACHE)>32:_CACHE.popitem(last=False)
    if not snapshot:
        result['noise_reduction_status']='UNAVAILABLE' if failed else 'WAITING_FOR_PI';return result
    by_sequence={p[1]:p for p in snapshot['rows']}
    history=result.get('history') or []
    if len(history)>300:raise ValueError('Unbounded history')
    targets=[*history]
    if result.get('latest'):targets.append(result['latest'])
    matched=[]
    for row in targets:
        if row.get('cylinder_id')!=cylinder or row.get('session_id')!=snapshot['session_id']:continue
        values=by_sequence.get(row.get('sequence_id'))
        if not values:continue
        metrics=row.get('packet_metrics') or {}
        originals=[(metrics.get(s) or {}).get('rms') for s in ('sph0645','inmp441')]
        if any(type(v) not in (float,int) or not math.isfinite(v) for v in originals):continue
        if not all(math.isclose(value/(2**31),values[i],rel_tol=1e-6,abs_tol=1e-12)
                   for value,i in zip(originals,(3,5))):continue
        row['noise_reduction']=dict(status='EXPERIMENTAL',raw_id=values[0],
            profile_sha256=snapshot['profile_sha256'],filtered_at=snapshot['filtered_at'],
            source_received_at=values[2],sensors={s:dict(original_ac_rms=values[i]*(2**31),rms=values[i+1]*(2**31))
                for s,i in (('sph0645',3),('inmp441',5))})
        matched.append(row['sequence_id'])
    try:
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(snapshot['filtered_at'])).total_seconds()
    except (TypeError,ValueError):age=float('inf')
    result['noise_reduction_status']=('STALE' if age>15 else 'READY') if matched else 'WAITING_FOR_MATCHING_PACKET'
    result['noise_reduction_latest_sequence']=max(matched) if matched else None
    return result
