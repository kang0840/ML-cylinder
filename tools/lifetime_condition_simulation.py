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
        'ground_truth',['NORMAL','SEAL_LEAK']).order('created_at').execute().data


def build_snapshot(client, cylinder, progress=print, operation_since=None):
    import numpy as np
    from system.MQTT.message_parser import parse_payload
    from system.ML.Condition.analysis import make_packet_stft_rows
    sessions=query_sessions(client,cylinder)
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
                'session_id',s['session_id']).gt('id',cursor).order('id').limit(64).execute().data
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
                  default_assumptions=dict(age_cycles=None,sound_influence=.25,feature='OPERATION' if operation_since else 'SEAL_LEAK'),
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
    return snapshot


def atomic_json(path, value, javascript=False):
    path=Path(path);temporary=path.with_name(path.name+'.incoming')
    text=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    if javascript:text='window.LIFETIME_CONDITION_SNAPSHOT = '+text+';\n'
    try:
        temporary.write_text(text,encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cylinder',default='cylinder_05',choices=['cylinder_%02d'%i for i in range(1,7)])
    parser.add_argument('--operation-since',help='Analyze unlabeled operation received since timezone-aware ISO time')
    parser.add_argument('--root',type=Path,default=Path('/opt/smart-cylinder-pi5'))
    args=parser.parse_args()
    try:
        adapter=runpy.run_path(str(args.root/'deploy/pi_sensor_runtime.py'))
        adapter['load_environment'](str(args.root/'.env'))
        from system.DB.Supabase.cylinder_result_repository import CylinderResultRepository
        result=build_snapshot(CylinderResultRepository()._get_client(),args.cylinder,operation_since=args.operation_since)
        atomic_json(args.root/'data/lifetime-condition-snapshot.json',result)
        atomic_json(args.root/'public/lifetime-condition-snapshot.js',result,javascript=True)
        print('SIMULATION_SNAPSHOT_READY normal='+str(result['normal_packets'])+' leak='+str(result['leak_packets']),flush=True)
        return 0
    except Exception as error:
        print('SIMULATION_NOT_UPDATED '+type(error).__name__+'; prior snapshot preserved; no credentials printed',flush=True)
        return 2


if __name__=='__main__':raise SystemExit(main())
