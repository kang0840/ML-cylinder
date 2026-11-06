"""Bounded read-only display RMS for receive-only collection.

Never persists derived data or exposes PCM. Canonical serial authorization and
OPERATION selection must complete before calling this helper.
"""
from collections import OrderedDict, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os
import threading

_CACHE=OrderedDict()
_LOCK=threading.RLock()
_MAX_CACHE=1800
_BATCH=50


def _identity(row):
    return row.get('cylinder_id'),row.get('session_id'),row.get('sequence_id')


def fill_display_rms(result,cylinder,repository,source_key=None):
    """Fill missing packet_metrics only for exact rows already authorized."""
    from system.MQTT.message_parser import parse_payload, ALLOWED_CYLINDER_IDS
    from system.ML.Condition.analysis import make_packet_metrics

    if cylinder not in ALLOWED_CYLINDER_IDS:raise ValueError('Invalid cylinder')
    history=result.get('history') or []
    if len(history)>300:raise ValueError('Unbounded monitoring selection')
    rows=[*history]
    if result.get('latest'):rows.append(result['latest'])
    missing={_identity(r) for r in rows if r.get('packet_metrics') is None
             and r.get('cylinder_id')==cylinder and r.get('session_id')
             and type(r.get('sequence_id')) is int and r['sequence_id']>0}
    if not missing:return result
    source=hashlib.sha256((source_key if source_key is not None else os.environ.get('SUPABASE_URL','')).encode()).hexdigest()
    metrics={};error=False
    with _LOCK:
        for key in missing:
            cache_key=(source,*key)
            if cache_key in _CACHE:
                metrics[key]=_CACHE[cache_key];_CACHE.move_to_end(cache_key)
        by_session=defaultdict(list)
        for key in missing-metrics.keys():by_session[key[1]].append(key[2])
        try:
            if by_session:
                client=repository._get_client()
                for session,sequences in by_session.items():
                    sequences=sorted(sequences)
                    for start in range(0,len(sequences),_BATCH):
                        batch=sequences[start:start+_BATCH]
                        found=(client.table('raw_data')
                            .select('id,cylinder_id,session_id,sequence_id,raw_payload')
                            .eq('cylinder_id',cylinder).eq('session_id',session)
                            .in_('sequence_id',batch).limit(len(batch)).execute().data or [])
                        for raw in found:
                            key=_identity(raw)
                            if key not in missing or key[0]!=cylinder or key[1]!=session or key[2] not in batch:
                                continue
                            try:
                                pcm=raw['raw_payload'];received=(pcm.get('runtime') or {}).get('last_received_at')
                                if not received:continue
                                # 0.1.11's parser predates received_at= and mutates
                                # its input. Normalize a copy for both versions.
                                prepared=deepcopy(pcm)
                                if prepared.get('timestamp')=='PI_RECEIVE_TIME':
                                    moment=datetime.fromisoformat(received.replace('Z','+00:00'))
                                    if moment.tzinfo is None:continue
                                    prepared['timestamp']=moment.astimezone(timezone(timedelta(hours=9))).isoformat()
                                payload=parse_payload(prepared)
                                if _identity(payload)!=key:continue
                                value=make_packet_metrics(payload)
                            except (KeyError,ValueError,TypeError,OverflowError):continue
                            metrics[key]=value;_CACHE[(source,*key)]=value
                            _CACHE.move_to_end((source,*key))
                            while len(_CACHE)>_MAX_CACHE:_CACHE.popitem(last=False)
        except Exception:
            # No arbitrary exception text: database configuration can contain secrets.
            error=True
    filled=0
    for row in rows:
        if row.get('packet_metrics') is None and _identity(row) in metrics:
            row['packet_metrics']=deepcopy(metrics[_identity(row)])
            row['packet_metrics_origin']='READ_ONLY_RAW_DISPLAY'
            filled+=1
    result['packet_metrics_status']=('READY' if len(metrics)==len(missing)
        else 'PARTIAL' if filled else 'UNAVAILABLE' if error else 'DATA_REQUIRED')
    return result
