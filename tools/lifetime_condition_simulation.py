"""Measured packet STFT + explicitly assumed Weibull-AFT condition scenario.

Read-only DB analysis. Never writes labels, failure times, Cycle or predictions.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import runpy
import sqlite3
from types import SimpleNamespace

PICOS = tuple('cylinder_%02d' % n for n in range(1, 7))


class PicoSQLiteClient:
    """Read one device archive, bounded at its starting Raw ID; no write lock."""
    def __init__(self, directory, cylinder):
        if cylinder not in PICOS:
            raise ValueError('Unknown Pico')
        self.path = Path(directory) / ('pico' + cylinder[-2:] + '-analysis.sqlite3')
        self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, isolation_level=None)
        identity = dict(self.db.execute('SELECT key,value FROM identity'))
        if identity.get('cylinder_id') != cylinder:
            self.db.close()
            raise ValueError('Archive identity mismatch')
        self.cylinder = cylinder
        self.highwater = self.db.execute('SELECT coalesce(max(id),0) FROM raw_data').fetchone()[0]
        self.start_count = self.db.execute('SELECT count(*) FROM raw_data WHERE id<=?', (self.highwater,)).fetchone()[0]

    def inventory(self):
        rows = self.db.execute("SELECT json_extract(s.document,'$.ground_truth'), "
            "json_extract(s.document,'$.collection_mode'),count(*),count(distinct r.session_id) "
            "FROM raw_data r JOIN sessions s ON s.session_id=r.session_id WHERE r.id<=? GROUP BY 1,2",
            (self.highwater,)).fetchall()
        return dict(raw_packets=sum(r[2] for r in rows), source_highwater_id=self.highwater,
                    groups=[dict(ground_truth=label,collection_mode=mode,packets=count,sessions=sessions)
                            for label,mode,count,sessions in rows])

    def verify_source(self):
        count = self.db.execute('SELECT count(*) FROM raw_data WHERE id<=?', (self.highwater,)).fetchone()[0]
        if count != self.start_count:
            raise ValueError('Source changed during analysis; retry')

    def close(self):
        self.db.close()

    def table(self, name):
        if name not in ('collection_sessions', 'raw_data'):
            raise ValueError('Read-only analysis table')
        owner = self

        class Query:
            def __init__(self):
                self.filters, self.ordering, self.maximum = [], ('id', False), None
                self.columns = None

            def select(self, columns):
                self.columns = None if columns == '*' else [part.strip() for part in columns.split(',')]
                if self.columns and any(not name.isidentifier() for name in self.columns):
                    raise ValueError('Unsupported projection')
                return self
            def eq(self, key, value): self.filters.append((key, '=', [value])); return self
            def gt(self, key, value): self.filters.append((key, '>', [value])); return self
            def in_(self, key, values): self.filters.append((key, 'IN', list(values))); return self
            def order(self, key, desc=False): self.ordering = (key, desc); return self
            def limit(self, value): self.maximum = value; return self

            def execute(self):
                allowed = {'id','session_id','cylinder_id','collection_mode','ground_truth','created_at','started_at'}
                def column(key):
                    if key not in allowed: raise ValueError('Unsupported read column')
                    if key == 'id' and name == 'raw_data': return 't.id'
                    if key == 'session_id': return 't.session_id'
                    return "json_extract(t.document,'$." + key + "')"
                params, terms = [], []
                if name == 'raw_data':
                    terms.append('t.id<=?'); params.append(owner.highwater)
                else:
                    terms.append('EXISTS(SELECT 1 FROM raw_data r WHERE r.session_id=t.session_id AND r.id<=?)')
                    params.append(owner.highwater)
                for key, operator, values in self.filters:
                    if operator == 'IN':
                        terms.append(column(key) + ' IN (' + ','.join('?' for _ in values) + ')' if values else '0')
                    else: terms.append(column(key) + operator + '?')
                    params.extend(values)
                key, desc = self.ordering
                if name == 'collection_sessions' and key == 'id': key = 'session_id'
                sql = 'SELECT t.document FROM ' + ('sessions' if name == 'collection_sessions' else 'raw_data')
                sql += ' t WHERE ' + ' AND '.join(terms) + ' ORDER BY ' + column(key) + (' DESC' if desc else ' ASC')
                if self.maximum is not None:
                    sql += ' LIMIT ?'; params.append(self.maximum)
                rows = [json.loads(row[0]) for row in owner.db.execute(sql, params).fetchall()]
                if self.columns is not None:
                    rows = [{key: row.get(key) for key in self.columns} for row in rows]
                return SimpleNamespace(data=rows)
        return Query()

VERSION = 'measured_stft_conditional_weibull_scenario_v1'
PAPER_FAILURES = [95000, 125997, 141000, 150000, 162000]
CONFIRMED_STOP_SESSIONS = {
    'c1a75b49-3852-4b08-9dfb-647121e15d2d', '2f5c7571-b033-4423-b7bd-9cb9bc99fca2',
    '2f138a41-621d-46e3-bfd2-d87dfe1f8498', '4bd5133d-2ffa-4071-8d76-5916c7dcb398',
    '39573bde-c929-4612-a19f-cb81a6b3b2ae', '0b82f226-b803-4c35-b287-e357bc3fc167',
}
CONFIGS = {
    'sph0645': dict(window_size=256, hop_length=64, n_fft=256, window_function='hann'),
    'inmp441': dict(window_size=1024, hop_length=256, n_fft=1024, window_function='hann'),
}

VIDEO_PERIOD_MEASUREMENT = dict(
    source='20261104_150725.mp4', mean_cycle_seconds=2.6016069444444443,
    source_sha256='ff18a9b670f1d9e334968da82af55cdca6490d8cc83e1ab23ca5447359edb887',
    min_cycle_seconds=2.5989951388888883, max_cycle_seconds=2.6031722222222253,
    measured_cycle_intervals=8, capture_fps=240,
    timing_basis='RAW_MP4_PTS_DIVIDED_BY_8; SAME_DIRECTION_ROD_TRANSITIONS',
    speed_confirmation='USER_CONFIRMED_SAME_OPERATING_SPEED_2026_11_04',
)


def apply_video_period(snapshot):
    """Confirmed six-session usage estimate, never a fabricated lifetime age."""
    measurement = dict(VIDEO_PERIOD_MEASUREMENT)
    selected = [s for s in snapshot['sessions'] if s.get('ground_truth') == 'SEAL_LEAK']
    result = dict(status='VIDEO_PERIOD_SCOPE_NOT_CONFIRMED', measurement=measurement,
                  estimated_interval_cycles=None, prior_lifetime_cycles=None,
                  estimated_total_cycles=None, actual_measured_cycles=None)
    if (snapshot['cylinder_id'] != 'cylinder_05' or snapshot.get('physical_cylinder_id') != 'CYL-005'
            or {s['session_id'] for s in selected} != CONFIRMED_STOP_SESSIONS
            or len(selected) != len(CONFIRMED_STOP_SESSIONS)
            or any(s.get('cylinder_id') != 'cylinder_05' or s.get('physical_cylinder_id') != 'CYL-005' for s in selected)
            or not snapshot['operation'].get('confirmed_stops')):
        snapshot['video_usage'] = result
        return snapshot
    spans = []
    for session in selected:
        start = datetime.fromisoformat(session['first_received_at'])
        end = datetime.fromisoformat(session['last_received_at'])
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('Valid Pi receipt spans required for video usage')
        spans.append((start, end))
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise ValueError('Overlapping session spans')
    seconds = sum((end-start).total_seconds() for start,end in spans)
    if not math.isclose(seconds, snapshot['operation']['observed_operation_seconds'], abs_tol=1e-6):
        raise ValueError('Observed operation span mismatch')
    result.update(status='VIDEO_PERIOD_INTERVAL_ESTIMATE',
                  observed_operation_seconds=seconds,
                  source_session_ids=sorted(CONFIRMED_STOP_SESSIONS),
                  estimated_interval_cycles=seconds/measurement['mean_cycle_seconds'],
                  observed_period_range_cycles=[seconds/measurement['max_cycle_seconds'],
                                                seconds/measurement['min_cycle_seconds']],
                  timing_basis='SUM_OF_FIRST_TO_LAST_PI_RECEIPT_SPANS; STOP_GAPS_EXCLUDED',
                  limitation='Interval estimate only; previous lifetime usage remains unknown. Period range is not a confidence interval.')
    snapshot['video_usage'] = result
    snapshot['usage_status'] = 'INTERVAL_ESTIMATED_PRIOR_LIFETIME_UNKNOWN'
    return snapshot


def fit_weibull(failures):
    if len(failures) < 2 or any(isinstance(x, bool) or not math.isfinite(x) or x <= 0 for x in failures):
        raise ValueError('At least two positive failure counts required')
    logs = [math.log(x) for x in failures]
    if max(logs) - min(logs) < 1e-10:
        raise ValueError('Failure counts must vary')
    def objective(k):
        peak = max(logs) * k
        log_power_mean = peak + math.log(sum(math.exp(k*x-peak) for x in logs) / len(logs))
        return len(logs)*log_power_mean - len(logs)*math.log(k) - (k-1)*sum(logs)
    lo, hi = .2, 50.
    ratio = (math.sqrt(5)-1)/2
    for _ in range(160):
        a, b = hi-ratio*(hi-lo), lo+ratio*(hi-lo)
        if objective(a) < objective(b): hi = b
        else: lo = a
    k = (lo+hi)/2
    if k < .2001 or k > 49.999:
        raise ValueError('Shape estimate outside supported fit range')
    peak = max(logs)*k
    scale = math.exp((peak+math.log(sum(math.exp(k*x-peak) for x in logs)/len(logs)))/k)
    return dict(shape=k, scale=scale, mean=scale*math.gamma(1+1/k), method='complete_failure_weibull_mle')


def conditional_residual_quantile(age, scale, shape, survival_probability):
    if age == 0:
        return scale*(-math.log(survival_probability))**(1/shape)
    log_x = shape*math.log(age/scale)
    c = -math.log(survival_probability)
    if log_x >= math.log(c):
        return age*math.expm1(math.log1p(c*math.exp(-log_x))/shape)
    return scale*math.exp((math.log(c)+math.log1p(math.exp(log_x-math.log(c))))/shape)-age


def conditional_mean(age, scale, shape):
    """scale/k * exp(x) * upper_gamma(1/k,x), evaluated without overflow."""
    if age == 0: return scale*math.gamma(1+1/shape)
    log_x=shape*math.log(age/scale);s=1/shape
    if log_x>35:return math.exp(math.log(age)-math.log(shape)-log_x)
    x=math.exp(log_x)
    if x<s+1:
        total=term=1/s
        for i in range(1,1000):
            term*=x/(s+i);total+=term
            if abs(term)<abs(total)*1e-14:break
        p=total*math.exp(-x+s*log_x-math.lgamma(s)) if x else 0
        return math.exp(math.log(scale)-math.log(shape)+x+math.lgamma(s)+math.log1p(-p))
    b=x+1-s;c=1e300;d=1/b;h=d
    for i in range(1,1000):
        an=-i*(i-s);b+=2;d=an*d+b;c=b+an/c
        if abs(d)<1e-300:d=1e-300
        if abs(c)<1e-300:c=1e-300
        d=1/d;delta=d*c;h*=delta
        if abs(delta-1)<1e-14:break
    return age/shape*h


def scenario(age, distance, influence, scale, shape):
    values = (age, distance, influence, scale, shape)
    if any(isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise ValueError('Finite numeric scenario inputs required')
    if not (0 <= age <= 1e7 and 0 <= distance <= 100 and 0 <= influence <= 5 and 1 <= scale <= 1e8 and .2 <= shape <= 50):
        raise ValueError('Scenario input outside supported range')
    effective_scale = scale*math.exp(-influence*distance)
    if effective_scale < 1e-6:
        raise ValueError('Assumed condition reduces scale below numeric range')
    def survival(extra):
        if extra <= 0: return 1.
        if age == 0: hazard = (extra/effective_scale)**shape
        else:
            log_h = shape*math.log(age/effective_scale)
            delta = shape*math.log1p(extra/age)
            log_difference = delta if delta > 50 else math.log(math.expm1(delta))
            log_hazard = log_h + log_difference
            if log_hazard > math.log(745): return 0.
            hazard = math.exp(log_hazard)
        return math.exp(-hazard) if hazard < 745 else 0.
    median = conditional_residual_quantile(age,effective_scale,shape,.5)
    upper = conditional_residual_quantile(age,effective_scale,shape,1e-10)
    mean=conditional_mean(age,effective_scale,shape)
    return dict(status='SIMULATION_ONLY', assumed_age_cycles=age, condition_distance=distance,
                assumed_sound_influence=influence, baseline_scale=scale, effective_scale=effective_scale,
                shape=shape, mean_remaining_cycles=mean, median_remaining_cycles=median,
                mean_total_cycles=age+mean,
                survival=[dict(extra_cycles=upper*i/100,probability=survival(upper*i/100)) for i in range(101)],
                formula='S(age+extra|D)/S(age|D); scale(D)=scale0*exp(-assumed_influence*D)')


def query_sessions(client, cylinder):
    return client.table('collection_sessions').select(
        'session_id,cylinder_id,physical_cylinder_id,experiment_id,ground_truth,dataset_role,test_conditions'
    ).eq('cylinder_id',cylinder).eq('collection_mode','TRAINING').in_(
        'ground_truth',['NORMAL','SEAL_LEAK']).order('started_at').execute().data


def build_snapshot(client, cylinder, progress=print, operation_since=None):
    import numpy as np
    from system.MQTT.message_parser import parse_payload
    from system.ML.Condition.analysis import make_packet_stft_rows
    sessions=query_sessions(client,cylinder)
    if any(s['cylinder_id'] != cylinder for s in sessions): raise ValueError('Mixed Pico sessions')
    normal=[s for s in sessions if s['ground_truth']=='NORMAL' and s['dataset_role'] in ('BASELINE','UNASSIGNED')]
    if not normal: raise ValueError('NORMAL baseline required')
    identity={s['physical_cylinder_id'] for s in normal}
    conditions={json.dumps(s['test_conditions'],sort_keys=True) for s in normal}
    if len(identity)!=1 or None in identity or len(conditions)!=1:
        raise ValueError('Choose matching physical cylinder and conditions for normal baseline')
    physical=next(iter(identity));condition=next(iter(conditions))
    selected=[s for s in sessions if (s in normal or s['ground_truth']=='SEAL_LEAK') and s['physical_cylinder_id']==physical and json.dumps(s['test_conditions'],sort_keys=True)==condition]
    if operation_since:
        since=datetime.fromisoformat(operation_since)
        if since.tzinfo is None: raise ValueError('Operation start timezone required')
        operation=client.table('collection_sessions').select('session_id,cylinder_id,physical_cylinder_id,experiment_id,ground_truth,dataset_role,test_conditions').eq('cylinder_id',cylinder).eq('collection_mode','OPERATION').execute().data
        selected=normal+operation
    excluded=[dict(session_id=s['session_id'],reason='PHYSICAL_OR_CONDITION_MISMATCH') for s in sessions if s not in selected]
    vectors={sensor:[] for sensor in CONFIGS}; frequencies={};records=[];source_hash=hashlib.sha256()
    summaries=[]
    for s in selected:
        cursor=0;seen=set();times=[];first_index=len(records)
        while True:
            page=client.table('raw_data').select('id,cylinder_id,session_id,sequence_id,raw_payload').eq(
                'session_id',s['session_id']).eq('cylinder_id',cylinder).gt('id',cursor).order('id').limit(64).execute().data
            if not page: break
            for row in page:
                if row['sequence_id'] in seen: raise ValueError('Duplicate packet')
                seen.add(row['sequence_id'])
                raw=row['raw_payload'];received=(raw.get('runtime') or {}).get('last_received_at')
                if not received: raise ValueError('Pi receipt metadata missing')
                stamp=datetime.fromisoformat(received)
                if operation_since and s not in normal and stamp<since: continue
                if stamp.tzinfo is None: raise ValueError('Timezone missing')
                payload=parse_payload(raw,received_at=received)
                if any(payload[k]!=row[k] for k in ('cylinder_id','session_id','sequence_id')): raise ValueError('Raw identity mismatch')
                features=make_packet_stft_rows(payload,CONFIGS)
                rms={}
                for feature in features:
                    sensor=feature['sensor_type'];f=feature['features']
                    vectors[sensor].append(np.log1p(f['mean_magnitude']))
                    frequencies[sensor]=f['frequency_hz'];rms[sensor]=f['rms']
                times.append(stamp)
                records.append(dict(raw_id=row['id'],session_id=s['session_id'],sequence_id=row['sequence_id'],
                                    ground_truth=s['ground_truth'],baseline=s in normal,rms=rms))
                source_hash.update(json.dumps(dict(id=row['id'],payload=raw),sort_keys=True).encode())
            cursor=page[-1]['id']
        if times:
            summaries.append(dict(s,packets=len(times),first_received_at=min(times).isoformat(),
                                  last_received_at=max(times).isoformat(),observed_span_seconds=(max(times)-min(times)).total_seconds(),
                                  record_start=first_index,record_end=len(records)))
        progress('STFT_SESSION '+str(s['experiment_id'])+' packets='+str(len(times)),flush=True)
    if not records: raise ValueError('No matched packets')
    mask=np.array([r['baseline'] for r in records]);normal_count=int(mask.sum())
    if normal_count<2: raise ValueError('Varying normal packets required')
    distances=[];spectra={}
    for sensor in CONFIGS:
        matrix=np.array(vectors[sensor]);baseline=matrix[mask]
        mean=baseline.mean(axis=0);std=baseline.std(axis=0,ddof=1)
        floor=max(float(std.mean())*.1,1e-6)
        distances.append(np.sqrt(np.mean(((matrix-mean)/np.maximum(std,floor))**2,axis=1)))
        leak=matrix[~mask]
        spectra[sensor]=dict(frequency_hz=frequencies[sensor],normal_mean_log1p=mean.tolist(),
                             leak_mean_log1p=leak.mean(axis=0).tolist() if len(leak) else None,std_floor=floor)
    raw_distance=np.mean(distances,axis=0)
    normal95=max(float(np.quantile(raw_distance[mask],.95)),1e-9)
    d=np.log1p(np.maximum(raw_distance/normal95-1,0))
    for i,r in enumerate(records): r['distance']=float(d[i])
    for s in summaries:
        part=records[s.pop('record_start'):s.pop('record_end')]
        s['median_distance']=float(np.median([r['distance'] for r in part]))
        s['distance_p95']=float(np.quantile([r['distance'] for r in part],.95))
    leak_sessions=[s for s in summaries if s['ground_truth']=='SEAL_LEAK']
    confirmed=bool(leak_sessions) and all(s['session_id'] in CONFIRMED_STOP_SESSIONS for s in leak_sessions)
    operating=sum(s['observed_span_seconds'] for s in leak_sessions)
    wall=(max(datetime.fromisoformat(s['last_received_at']) for s in leak_sessions)-min(datetime.fromisoformat(s['first_received_at']) for s in leak_sessions)).total_seconds() if leak_sessions else 0
    candidates=[]
    for label,name in [('NORMAL','정상 기준 전체'),('SEAL_LEAK','누설 자료 전체')]:
        group=[r['distance'] for r in records if r['ground_truth']==label]
        if group:candidates.append(dict(key=label,name='정상 기준 전체' if label=='NORMAL' else name,distance=float(np.median(group))))
    candidates.extend(dict(key=s['session_id'],name=s['experiment_id'] or s['session_id'],distance=s['median_distance']) for s in summaries)
    if operation_since:
        targets=[r['distance'] for r in records if not r['baseline']]
        if not targets: raise ValueError('No operation packets received in requested interval')
        candidates=[dict(key='OPERATION',name='이번 실제 수신 운영 데이터 (라벨 없음)',distance=float(np.median(targets)))]
    snapshot=dict(status='SIMULATION_ONLY',version=VERSION,generated_at=datetime.now(timezone.utc).isoformat(),
                  cylinder_id=cylinder,physical_cylinder_id=physical,source='SUPABASE_RAW_READ_ONLY',source_sha256=source_hash.hexdigest(),
                  normal_packets=normal_count,leak_packets=len(records)-normal_count,sessions=summaries,excluded_sessions=excluded,
                  stft_configs=CONFIGS,spectra=spectra,candidates=candidates,
                  baseline_distance_p95=normal95,distance_definition='log1p(max(0, mean(sensor standardized log-spectrum distance)/normal_p95 - 1))',
                  baseline_scope='NORMAL BASELINE/UNASSIGNED only; no leakage labels used to fit baseline',
                  default_failures=PAPER_FAILURES,paper_author_reported_mean=167500,
                  reference_url='https://www.tribology.rs/journals/2025/2025-1/15-1831.pdf',
                  default_assumptions=dict(age_cycles=None,sound_influence=.25,feature='OPERATION' if operation_since else ('SEAL_LEAK' if any(c['key']=='SEAL_LEAK' for c in candidates) else 'NORMAL')),
                  operation=dict(confirmed_stops=confirmed,observed_operation_seconds=operating,
                                 excluded_stop_gap_seconds=wall-operating if confirmed else None,
                                 cycle_period_seconds=None,actual_mechanical_cycles=None),
                  limitations=['Paper failures are from a different cylinder experiment.',
                               'Sound influence and usage are hypotheses, not learned failure relationships.',
                               'No measured remaining life, classification accuracy or maintenance recommendation.',
                               'Snapshot is refreshed when the Pi analysis is run, not live browser inference.'])
    fit=fit_weibull(PAPER_FAILURES)
    target=next((c['distance'] for c in candidates if c['key']=='SEAL_LEAK'),0)
    snapshot['default_result']=None
    snapshot['usage_status']='UNKNOWN_MEASURED_CYCLES_REQUIRED'
    snapshot['operation_packets']=len(records)-normal_count if operation_since else 0
    if operation_since:
        snapshot['leak_packets']=0
        snapshot['comparison_scope']='SAME_CHANNEL_OPERATION; physical identity and conditions unconfirmed; not a validated condition comparison'
        snapshot['operation_since']=operation_since
    snapshot['paper_fit']=fit
    return apply_video_period(snapshot)


def atomic_json(path, value, javascript=False):
    path=Path(path);temporary=path.with_name(path.name+'.incoming')
    text=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if javascript:text='window.LIFETIME_CONDITION_SNAPSHOT = '+text+';\n'
    try:
        temporary.write_text(text,encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def analyze_picos(directory, cylinders=PICOS, progress=print, operation_since=None, client_factory=None):
    """Separate inputs/results; never substitute another device's baseline."""
    results = {}
    reasons = {
        'NORMAL baseline required': ('NORMAL_BASELINE_REQUIRED','이 Pico의 원본이 있는 정상 기준 세션이 필요합니다.'),
        'Choose matching physical cylinder and conditions for normal baseline':
            ('BASELINE_CONDITIONS_REQUIRED','정상 기준의 실물과 조건을 한 그룹으로 확인해야 합니다.'),
        'Varying normal packets required': ('MORE_NORMAL_PACKETS_REQUIRED','정상 기준 원본 패킷이 더 필요합니다.'),
        'No matched packets': ('RAW_DATA_REQUIRED','분석할 원본이 없습니다.'),
        'No operation packets received in requested interval': ('OPERATION_DATA_REQUIRED','지정 구간의 운영 원본이 없습니다.'),
    }
    for cylinder in cylinders:
        client, inventory = None, None
        try:
            client = client_factory(cylinder) if client_factory else PicoSQLiteClient(directory,cylinder)
            if isinstance(client,PicoSQLiteClient): inventory = client.inventory()
            result = build_snapshot(client,cylinder,progress,operation_since)
            if isinstance(client,PicoSQLiteClient):
                client.verify_source()
                result['source'] = 'PICO_SQLITE_READ_ONLY'
                result['source_file'] = client.path.name
                result['source_inventory'] = inventory
            results[cylinder] = result
        except Exception as error:
            known = reasons.get(str(error)) if isinstance(error,ValueError) else None
            results[cylinder] = dict(
                status='DATA_REQUIRED' if known else 'ANALYSIS_FAILED', cylinder_id=cylinder,
                generated_at=datetime.now(timezone.utc).isoformat(),
                reason=known[0] if known else 'SOURCE_OR_ANALYSIS_FAILED',
                message=known[1] if known else '해당 Pico DB 또는 분석 입력을 확인하세요. 다른 Pico 결과로 대체하지 않았습니다.',
                error_type=None if known else type(error).__name__,source_inventory=inventory,
                normal_packets=0,leak_packets=0,operation_packets=0,sessions=[],default_result=None)
        finally:
            if isinstance(client,PicoSQLiteClient): client.close()
        progress('PICO_ANALYSIS '+cylinder+' '+results[cylinder]['status'],flush=True)
    valid = [key for key,value in results.items() if value['status']=='SIMULATION_ONLY']
    return dict(status='PICO_ANALYSIS_COLLECTION',generated_at=datetime.now(timezone.utc).isoformat(),
                default_cylinder='cylinder_05' if 'cylinder_05' in valid else (valid[0] if valid else cylinders[0]),
                calculated_picos=valid,picos=results)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cylinder',choices=PICOS,help='Only this Pico; default analyzes all six independently')
    parser.add_argument('--all-picos',action='store_true')
    parser.add_argument('--source',choices=['sqlite','supabase'],default='sqlite')
    parser.add_argument('--database-directory',type=Path,help='Existing per-Pico archive directory; default root/data')
    parser.add_argument('--operation-since',help='Analyze unlabeled operation received since timezone-aware ISO time')
    parser.add_argument('--root',type=Path,default=Path('/opt/smart-cylinder-pi5'))
    args=parser.parse_args()
    if args.cylinder and args.all_picos: parser.error('Choose one Pico or --all-picos')
    try:
        import fcntl
        lock=open('/tmp/smart-cylinder-lifetime-analysis.lock','a')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client_factory=None
        if args.source=='supabase':
            adapter=runpy.run_path(str(args.root/'deploy/pi_sensor_runtime.py'))
            adapter['load_environment'](str(args.root/'.env'))
            from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository
            client_factory=lambda _:CylinderResultRepository()._get_client()
        if args.source == 'sqlite':
            usage_tool = Path(__file__).with_name('build_db_usage.py')
            if usage_tool.exists():
                usage = runpy.run_path(str(usage_tool))['build'](args.database_directory or args.root/'data')
                atomic_json(args.root/'data/lifetime-db-usage.json', usage)
                usage_path=args.root/'public/lifetime-db-usage.js'
                usage_tmp=usage_path.with_suffix('.tmp')
                usage_tmp.write_text('window.LIFETIME_DB_USAGE = '+json.dumps(usage,ensure_ascii=False,allow_nan=False)+';\n',encoding='utf-8')
                usage_tmp.replace(usage_path)
        chosen=(args.cylinder,) if args.cylinder else PICOS
        result=analyze_picos(args.database_directory or args.root/'data',chosen,operation_since=args.operation_since,
                             client_factory=client_factory)
        for cylinder,snapshot in result['picos'].items():
            atomic_json(args.root/'data'/('lifetime-condition-pico'+cylinder[-2:]+'.json'),snapshot)
        if args.cylinder:
            previous_path=args.root/'data/lifetime-condition-snapshot.json'
            if previous_path.exists():
                previous=json.loads(previous_path.read_text(encoding='utf-8'))
                if previous.get('status')=='PICO_ANALYSIS_COLLECTION':
                    result['picos']={**previous['picos'],**result['picos']}
        atomic_json(args.root/'data/lifetime-condition-snapshot.json',result)
        atomic_json(args.root/'public/lifetime-condition-snapshot.js',result,javascript=True)
        print('PICO_SNAPSHOTS_READY calculated='+','.join(result['calculated_picos'])+'; check each Pico status',flush=True)
        return 0
    except Exception as error:
        print('SIMULATION_NOT_UPDATED '+type(error).__name__+'; prior snapshot preserved; no credentials printed',flush=True)
        return 2


if __name__=='__main__':raise SystemExit(main())
