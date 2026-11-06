"""Small, bounded scalar-only snapshots shared by Pi and the web server."""
import base64
import json
import math
import re
from uuid import UUID
import zlib

MAX_ROWS=100
MAX_TEXT=32*1024
MAX_DECODED=128*1024
CHANNELS=('cylinder_01','cylinder_03','cylinder_05')


def validate(snapshot):
    if snapshot.get('version')!=1 or snapshot.get('cylinder_id') not in CHANNELS:
        raise ValueError('Invalid snapshot channel/version')
    UUID(snapshot['session_id'])
    for key in ('source_id','profile_sha256'):
        if not re.fullmatch('[0-9a-f]{64}',snapshot.get(key,'')):raise ValueError('Invalid source hash')
    rows=snapshot.get('rows')
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_ROWS:raise ValueError('Invalid snapshot size')
    previous=0
    for row in rows:
        if (not isinstance(row,list) or len(row)!=7 or type(row[0]) is not int or row[0]<=0
            or type(row[1]) is not int or row[1]<=previous or not isinstance(row[2],str)):
            raise ValueError('Invalid snapshot identity')
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=2 for v in row[3:]):
            raise ValueError('Invalid normalized RMS')
        previous=row[1]
    return snapshot


def from_records(records):
    if not records:raise ValueError('No derived records')
    last=records[-1];rows={}
    for record in records:
        if (record.get('kind')!='DERIVED_NOISE_REDUCED_PACKET'
            or record.get('cylinder_id')!=last['cylinder_id']
            or record.get('session_id')!=last['session_id']
            or record.get('source_profile_sha256')!=last['source_profile_sha256']
            or record.get('source_database_id')!=last['source_database_id']):continue
        sensors=record['sensors']
        rows[record['sequence_id']]=[record['source_raw_id'],record['sequence_id'],record['source_received_at'],
            sensors['sph0645']['original_ac_rms'],sensors['sph0645']['reduced_rms'],
            sensors['inmp441']['original_ac_rms'],sensors['inmp441']['reduced_rms']]
    return validate(dict(version=1,cylinder_id=last['cylinder_id'],session_id=last['session_id'],
        source_id=last['source_database_id'],profile_sha256=last['source_profile_sha256'],
        filtered_at=last['generated_at'],rows=[rows[k] for k in sorted(rows)[-MAX_ROWS:]]))


def encode(snapshot):
    validate(snapshot)
    raw=json.dumps(snapshot,separators=(',',':'),allow_nan=False).encode()
    if len(raw)>MAX_DECODED:raise ValueError('Oversized snapshot')
    encoded=base64.b64encode(zlib.compress(raw,6)).decode('ascii')
    result=json.dumps(dict(encoding='zlib-base64',data=encoded),separators=(',',':'))
    if len(result)>MAX_TEXT:raise ValueError('Oversized encoded snapshot')
    return result


def decode(value):
    if not isinstance(value,str) or len(value)>MAX_TEXT:raise ValueError('Oversized setting')
    envelope=json.loads(value)
    if envelope.get('encoding')!='zlib-base64':raise ValueError('Unknown encoding')
    compressed=base64.b64decode(envelope['data'],validate=True)
    inflater=zlib.decompressobj();raw=inflater.decompress(compressed,MAX_DECODED+1)
    if len(raw)>MAX_DECODED or not inflater.eof or inflater.unused_data:raise ValueError('Invalid compressed size')
    return validate(json.loads(raw))
