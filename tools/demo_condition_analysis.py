"""Read-only intermittent demonstration STFT comparison, not measured RUL."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import runpy
import signal
import time

VERSION = 'demo_stft_reference_v1'
DEFAULT_REFERENCE_MEAN = 167500
SENSORS = ('sph0645', 'inmp441')


def now():
    return datetime.now(timezone.utc)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.incoming')
    temporary.write_text(canonical(value), encoding='utf-8')
    temporary.replace(path)


def condition_lifetime(reference_mean, shape, distance, influence):
    """Unconditional state-conditioned mean life; no age=0 or unknown-age substitution."""
    values = (reference_mean, shape, distance, influence)
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
        raise ValueError('Finite model parameters required')
    if not (1 <= reference_mean <= 1e8 and .2 <= shape <= 50 and 0 <= distance <= 100 and 0 <= influence <= 5):
        raise ValueError('Model parameters outside supported bounds')
    scale = reference_mean/math.gamma(1+1/shape)
    effective = scale*math.exp(-influence*distance)
    if effective < 1e-6:
        raise ValueError('Sound influence too large')
    mean = effective*math.gamma(1+1/shape)
    upper = effective*(-math.log(1e-6))**(1/shape)
    return dict(status='SIMULATION_ONLY', reference_mean_cycles=reference_mean, shape=shape,
                assumed_sound_influence=influence, condition_distance=distance,
                effective_scale=effective, mean_life_cycles=mean,
                median_life_cycles=effective*math.log(2)**(1/shape),
                change_from_reference_percent=100*(mean/reference_mean-1),
                actual_age_cycles=None, remaining_cycles=None,
                survival=[dict(cycles=upper*i/80, probability=math.exp(-((upper*i/80)/effective)**shape)) for i in range(81)],
                formula='mean(D)=reference_mean*exp(-assumed_gamma*D); unconditional total-life scenario, not remaining life')


def features(row, configs):
    import numpy as np
    from system.MQTT.message_parser import parse_payload
    from system.ML.Condition.analysis import make_packet_stft_rows
    raw = row['raw_payload']
    received = (raw.get('runtime') or {}).get('last_received_at')
    if not received:
        raise ValueError('PI_RECEIPT_METADATA_MISSING')
    stamp = datetime.fromisoformat(received)
    if stamp.tzinfo is None:
        raise ValueError('RECEIPT_TIMEZONE_MISSING')
    payload = parse_payload(raw, received_at=received)
    if any(payload[k] != row[k] for k in ('session_id', 'cylinder_id', 'sequence_id')):
        raise ValueError('PACKET_IDENTITY_MISMATCH')
    spectra, frequencies, energies = {}, {}, []
    for item in make_packet_stft_rows(payload, configs):
        name, f = item['sensor_type'], item['features']
        spectra[name] = np.log1p(f['mean_magnitude']).tolist()
        frequencies[name] = f['frequency_hz']
        energies.append(math.log1p(f['rms']))
    return dict(raw_id=row['id'], session_id=row['session_id'], received_at=stamp.isoformat(),
                energy=sum(energies)/len(energies), spectra=spectra, frequencies=frequencies)


def activity_gate(energies):
    """Experimental 1-D energy split; it is not a mechanical motion detector."""
    import numpy as np
    values = np.asarray(energies, dtype=float)
    if len(values) < 10 or not np.isfinite(values).all():
        return dict(status='UNRESOLVED', method='normal_energy_two_cluster_v1')
    low, high = np.quantile(values, [.2, .8])
    for _ in range(40):
        mask = values > (low+high)/2
        if not mask.any() or mask.all():
            break
        new_low, new_high = float(values[~mask].mean()), float(values[mask].mean())
        if abs(new_low-low)+abs(new_high-high) < 1e-8:
            low, high = new_low, new_high
            break
        low, high = new_low, new_high
    threshold = (low+high)/2
    fraction = float(np.mean(values > threshold))
    resolved = high-low >= math.log(2) and .05 <= fraction <= .95
    return dict(status='EXPERIMENTAL' if resolved else 'UNRESOLVED', method='normal_energy_two_cluster_v1',
                log_energy_threshold=threshold, low_center=float(low), high_center=float(high),
                reference_high_energy_fraction=fraction,
                limitation='Low acoustic activity is not confirmed cylinder stop. Exhaust/leakage/other machinery can pass this gate.')


def reference_sessions(client):
    rows = client.table('collection_sessions').select('*').eq('cylinder_id', client.cylinder).eq(
        'collection_mode', 'TRAINING').in_('ground_truth', ['NORMAL', 'SEAL_LEAK']).execute().data
    normal = [s for s in rows if s.get('ground_truth') == 'NORMAL' and s.get('dataset_role') in ('BASELINE', 'UNASSIGNED')]
    if not normal:
        raise ValueError('NORMAL_REFERENCE_REQUIRED')
    groups = {(s.get('physical_cylinder_id'), canonical(s.get('test_conditions'))) for s in normal}
    if len(groups) != 1 or next(iter(groups))[0] is None:
        raise ValueError('REFERENCE_IDENTITY_CONDITIONS_REQUIRED')
    physical, conditions = next(iter(groups))
    leak = [s for s in rows if s.get('ground_truth') == 'SEAL_LEAK' and s.get('dataset_role') != 'TEST'
            and s.get('physical_cylinder_id') == physical and canonical(s.get('test_conditions')) == conditions]
    return normal, leak, physical, conditions


def build_reference(client, model, cache_path=None):
    import numpy as np
    normal, leak, physical, conditions = reference_sessions(client)
    selected = normal+leak
    inventory = []
    for session in selected:
        count, maximum = client.db.execute('SELECT count(*),max(id) FROM raw_data WHERE session_id=? AND id<=?',
                                          (session['session_id'], client.highwater)).fetchone()
        inventory.append(dict(session=session, raw_packets=count, maximum_raw_id=maximum))
    fingerprint = hashlib.sha256(canonical(dict(version=VERSION, cylinder=client.cylinder, configs=model['CONFIGS'],
                                                path=str(client.path.resolve()), inventory=inventory)).encode()).hexdigest()
    if cache_path and Path(cache_path).exists():
        try:
            cached = json.loads(Path(cache_path).read_text(encoding='utf-8'))
            if cached.get('fingerprint') == fingerprint and cached.get('version') == VERSION:
                client.verify_source()
                return cached
        except (ValueError, OSError):
            pass
    packets = {'NORMAL': [], 'SEAL_LEAK': []}
    excluded = {}
    for session in selected:
        cursor = 0
        while True:
            rows = client.table('raw_data').select('id,session_id,cylinder_id,sequence_id,raw_payload').eq(
                'session_id', session['session_id']).eq('cylinder_id', client.cylinder).gt('id', cursor).order('id').limit(64).execute().data
            if not rows:
                break
            for row in rows:
                try:
                    packets[session['ground_truth']].append(features(row, model['CONFIGS']))
                except ValueError as error:
                    reason = str(error) if str(error) in ('PI_RECEIPT_METADATA_MISSING', 'RECEIPT_TIMEZONE_MISSING') else 'INVALID_PCM_OR_IDENTITY'
                    excluded[reason] = excluded.get(reason, 0)+1
            cursor = rows[-1]['id']
    gate = activity_gate([p['energy'] for p in packets['NORMAL']])
    if gate['status'] != 'EXPERIMENTAL':
        raise ValueError('ACOUSTIC_ACTIVITY_REFERENCE_REQUIRED')
    active = {label: [p for p in group if p['energy'] > gate['log_energy_threshold']] for label, group in packets.items()}
    if len(active['NORMAL']) < 10:
        raise ValueError('MORE_NORMAL_ACOUSTIC_CANDIDATES_REQUIRED')
    sensors = {}
    for sensor in SENSORS:
        matrix = np.array([p['spectra'][sensor] for p in active['NORMAL']])
        mean, std = matrix.mean(axis=0), matrix.std(axis=0, ddof=1)
        std = np.maximum(std, max(float(std.mean())*.1, 1e-6))
        normal_dist = np.sqrt(np.mean(((matrix-mean)/std)**2, axis=1))
        leak_matrix = np.array([p['spectra'][sensor] for p in active['SEAL_LEAK']])
        leak_mean = leak_matrix.mean(axis=0) if len(leak_matrix) >= 5 else None
        sensors[sensor] = dict(frequency_hz=active['NORMAL'][0]['frequencies'][sensor], normal_mean=mean.tolist(),
                               normal_std=std.tolist(), normal_p95=max(float(np.quantile(normal_dist, .95)), 1e-6),
                               leak_mean=leak_mean.tolist() if leak_mean is not None else None)
    result = dict(status='REFERENCE_READY', version=VERSION, fingerprint=fingerprint, cylinder_id=client.cylinder,
                  physical_cylinder_id=physical, test_conditions=json.loads(conditions), activity_gate=gate,
                  sensors=sensors, normal_packets=len(packets['NORMAL']), leak_packets=len(packets['SEAL_LEAK']),
                  normal_candidates=len(active['NORMAL']), leak_candidates=len(active['SEAL_LEAK']),
                  reference_sessions=inventory, excluded_packets_by_reason=excluded,
                  generated_at=now().isoformat(), source_file=client.path.name,
                  leak_reference_available=all(v['leak_mean'] is not None for v in sensors.values()),
                  limitation='Manual labels are reference examples, not Weibull failure events or a held-out accuracy test.')
    client.verify_source()
    if cache_path:
        write_json(cache_path, result)
    return result


def verify_reference(client, reference):
    """A pinned reference can survive new inserts, but not deleted or relabeled source rows."""
    keys = ('cylinder_id','physical_cylinder_id','ground_truth','dataset_role','collection_mode','test_conditions')
    for item in reference['reference_sessions']:
        session = item['session']
        row = client.db.execute('SELECT document FROM sessions WHERE session_id=?', (session['session_id'],)).fetchone()
        if not row or any(json.loads(row[0]).get(k) != session.get(k) for k in keys):
            raise ValueError('REFERENCE_SOURCE_CHANGED')
        count = client.db.execute('SELECT count(*) FROM raw_data WHERE session_id=? AND id<=?',
                                 (session['session_id'], item['maximum_raw_id'])).fetchone()[0]
        if count != item['raw_packets']:
            raise ValueError('REFERENCE_SOURCE_CHANGED')


def compare(reference, packets, min_active=5, at=None, replay=False, active_history=None):
    """Compare independent acoustic candidates from one demo/session; never concatenate PCM."""
    import numpy as np
    at = at or now()
    result = dict(status='WAITING_NEW_DATA', sampled_packets=len(packets), active_candidates=0, low_activity_packets=0,
                  required_candidates=min_active, measured_cycle_count=None, measured_lifetime_usage=None, scenario=None)
    if not packets:
        return result
    newest = max(packets, key=lambda p: p['raw_id'])
    packets = sorted([p for p in packets if p['session_id'] == newest['session_id']], key=lambda p:p['raw_id'])
    result.update(sampled_packets=len(packets), session_id=newest['session_id'], latest_received_at=newest['received_at'])
    latest_time = datetime.fromisoformat(newest['received_at'])
    if not replay and ((at-latest_time).total_seconds() > 30 or (latest_time-at).total_seconds() > 5):
        result['status'] = 'STALE_DEMO_DATA'
        return result
    current_active = [p for p in packets if p['energy'] > reference['activity_gate']['log_energy_threshold']]
    result['low_activity_packets'] = len(packets)-len(current_active)
    combined = {p['raw_id']:p for p in (active_history or [])+current_active if p['session_id']==newest['session_id']}
    active = sorted([p for p in combined.values() if 0 <= (latest_time-datetime.fromisoformat(p['received_at'])).total_seconds() <= 180],key=lambda p:p['raw_id'])
    if not active:
        result['status'] = 'LOW_ACOUSTIC_ACTIVITY'
        return result
    latest_active = datetime.fromisoformat(active[-1]['received_at'])
    if not replay and (at-latest_active).total_seconds() > 10:
        result['status'] = 'LOW_ACOUSTIC_ACTIVITY'
        return result
    # Independent packet features may span intermittent actions. No waveform or operation time is joined.
    burst = active[-min_active:]
    result['active_candidates'] = len(burst)
    result['comparison_window_seconds'] = (latest_active-datetime.fromisoformat(burst[0]['received_at'])).total_seconds()
    if len(burst) < min_active:
        result['status'] = 'MORE_ACOUSTIC_CANDIDATES_REQUIRED'
        return result
    normal_scores, leak_scores, demo_spectra = [], [], {}
    for sensor in SENSORS:
        settings = reference['sensors'][sensor]
        if any(p['frequencies'][sensor] != settings['frequency_hz'] for p in burst):
            raise ValueError('FREQUENCY_CONFIGURATION_MISMATCH')
        matrix = np.array([p['spectra'][sensor] for p in burst])
        mean, std = np.array(settings['normal_mean']), np.array(settings['normal_std'])
        distances = np.sqrt(np.mean(((matrix-mean)/std)**2, axis=1))
        normal_scores.append(distances/settings['normal_p95'])
        if settings['leak_mean'] is not None:
            leak_scores.append(np.sqrt(np.mean(((matrix-np.array(settings['leak_mean']))/std)**2, axis=1))/settings['normal_p95'])
        demo_spectra[sensor] = dict(frequency_hz=settings['frequency_hz'], normal_mean=settings['normal_mean'],
                                    leak_mean=settings['leak_mean'], demo_mean=matrix.mean(axis=0).tolist())
    normal_distance = float(np.median(np.mean(normal_scores, axis=0)))
    leak_distance = float(np.median(np.mean(leak_scores, axis=0))) if len(leak_scores) == len(SENSORS) else None
    distance = math.log1p(max(0, normal_distance-1))
    result.update(status='DEMO_COMPARISON_READY', normal_distance=normal_distance, leak_distance=leak_distance,
                  condition_distance=distance, spectra=demo_spectra,
                  reference_closer='NORMAL' if leak_distance is not None and normal_distance < leak_distance else
                                   ('SEAL_LEAK' if leak_distance is not None and leak_distance < normal_distance else 'UNRESOLVED'),
                  first_candidate_received_at=min(p['received_at'] for p in burst),
                  last_candidate_received_at=max(p['received_at'] for p in burst),
                  raw_ids=sorted(p['raw_id'] for p in burst),
                  limitation='Acoustic candidate gate and nearest reference are experimental; neither is ground truth, mechanical cycles, nor validated diagnosis.')
    return result


def read_targets(client, floor, limit=50, since=None):
    rows = client.db.execute(
        "SELECT r.document,s.document FROM raw_data r JOIN sessions s ON s.session_id=r.session_id "
        "WHERE r.id>? AND r.id<=? AND json_extract(s.document,'$.collection_mode')='OPERATION' "
        "ORDER BY r.id DESC LIMIT ?", (floor, client.highwater, limit)).fetchall()
    if not rows:
        return [], None
    newest_session = json.loads(rows[0][0])['session_id']
    output, meta = [], None
    for document, session_document in rows:
        row = json.loads(document)
        if row['session_id'] != newest_session:
            continue
        if row.get('cylinder_id') != client.cylinder:
            raise ValueError('TARGET_CHANNEL_MISMATCH')
        received = (row.get('raw_payload', {}).get('runtime') or {}).get('last_received_at')
        if since and received and datetime.fromisoformat(received) < since:
            continue
        meta = json.loads(session_document)
        if meta.get('cylinder_id') != client.cylinder:
            raise ValueError('TARGET_CHANNEL_MISMATCH')
        output.append(row)
    return output, meta


def tick(directory, cylinders, references, floors, model, min_active=5, window=50, since=None, replay=False,
         reference_mean=DEFAULT_REFERENCE_MEAN, influence=.25, feature_cache=None, activity_buffers=None):
    feature_cache = feature_cache if feature_cache is not None else {}
    activity_buffers = activity_buffers if activity_buffers is not None else {}
    output = {}
    shape = model['fit_weibull'](model['PAPER_FAILURES'])['shape']
    for cylinder in cylinders:
        reference = references[cylinder]
        result = dict(cylinder_id=cylinder, generated_at=now().isoformat(), reference=reference,
                      status=reference['status'], scenario=None,
                      source_mode='HISTORICAL_REPLAY' if replay else 'NEW_DB_DATA_AFTER_DEMO_START')
        client = None
        try:
            if reference['status'] == 'REFERENCE_READY':
                client = model['PicoSQLiteClient'](directory, cylinder)
                verify_reference(client, reference)
                rows, meta = read_targets(client, floors[cylinder], window, since)
                if meta and meta.get('physical_cylinder_id') and meta['physical_cylinder_id'] != reference['physical_cylinder_id']:
                    raise ValueError('TARGET_PHYSICAL_IDENTITY_MISMATCH')
                if meta and meta.get('test_conditions') and meta['test_conditions'] != reference['test_conditions']:
                    raise ValueError('TARGET_CONDITIONS_MISMATCH')
                packets, excluded = [], {}
                cached = feature_cache.setdefault(cylinder, {})
                for row in rows:
                    fingerprint = hashlib.sha256(canonical(row['raw_payload']).encode()).hexdigest()
                    key = str(row['id'])+':'+fingerprint
                    try:
                        if key not in cached:
                            cached[key] = features(row, model['CONFIGS'])
                        packets.append(cached[key])
                    except ValueError:
                        excluded['MISSING_TIME_OR_INVALID_PCM'] = excluded.get('MISSING_TIME_OR_INVALID_PCM', 0)+1
                keep = {str(row['id'])+':'+hashlib.sha256(canonical(row['raw_payload']).encode()).hexdigest() for row in rows}
                feature_cache[cylinder] = {k:v for k,v in cached.items() if k in keep}
                incoming_ids={row['id'] for row in rows}
                previous=[p for p in activity_buffers.get(cylinder,[]) if p['raw_id'] not in incoming_ids]
                result.update(compare(reference, packets, min_active, replay=replay,active_history=previous))
                combined={p['raw_id']:p for p in previous+packets if p['energy']>reference['activity_gate']['log_energy_threshold']}
                if packets:
                    latest=max(packets,key=lambda p:p['raw_id'])
                    latest_time=datetime.fromisoformat(latest['received_at'])
                    activity_buffers[cylinder]=sorted([p for p in combined.values() if p['session_id']==latest['session_id'] and 0<=(latest_time-datetime.fromisoformat(p['received_at'])).total_seconds()<=180],key=lambda p:p['raw_id'])[-min_active:]
                else:
                    activity_buffers[cylinder]=[]
                result.update(excluded_packets_by_reason=excluded, source_file=client.path.name,
                              source_highwater_id=client.highwater, source_raw_packets=client.start_count,
                              demo_started_after_raw_id=floors[cylinder],
                              identity_conditions_confirmed=bool(meta and meta.get('physical_cylinder_id') and meta.get('test_conditions')))
                if result['status'] == 'DEMO_COMPARISON_READY':
                    result['scenario'] = condition_lifetime(reference_mean, shape, result['condition_distance'], influence)
                client.verify_source()
        except Exception as error:
            result.update(status='DEMO_INPUT_REQUIRED', error_type=type(error).__name__, scenario=None)
            if isinstance(error, ValueError) and str(error) in ('TARGET_PHYSICAL_IDENTITY_MISMATCH','TARGET_CONDITIONS_MISMATCH','TARGET_CHANNEL_MISMATCH','FREQUENCY_CONFIGURATION_MISMATCH','REFERENCE_SOURCE_CHANGED'):
                result['reason'] = str(error)
        finally:
            if client: client.close()
        output[cylinder] = result
    return dict(status='DEMO_COMPARISON_COLLECTION', version=VERSION, generated_at=now().isoformat(),
                source_mode='HISTORICAL_REPLAY' if replay else 'NEW_DB_DATA_AFTER_DEMO_START', picos=output,
                defaults=dict(reference_mean_cycles=reference_mean, paper_author_reported_mean=DEFAULT_REFERENCE_MEAN,
                              shape=shape, assumed_sound_influence=influence, window_packets=window, min_acoustic_candidates=min_active),
                limitations=['Sound-life influence is hypothetical, not trained from actual failures.',
                             'No measured mechanical usage or remaining life is generated.',
                             'Render copies are deployment-time snapshots; Pi results update only while this separate worker runs.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/smart-cylinder-pi5'))
    parser.add_argument('--cylinder', choices=tuple('cylinder_%02d'%n for n in range(1,7)))
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--replay', action='store_true', help='Explicit historical replay; never presented as current demonstration')
    parser.add_argument('--since', help='Optional timezone-aware lower receipt bound')
    parser.add_argument('--window-packets', type=int, default=50)
    parser.add_argument('--min-candidates', type=int, default=5)
    parser.add_argument('--interval', type=float, default=3)
    parser.add_argument('--reference-mean', type=float, default=DEFAULT_REFERENCE_MEAN)
    parser.add_argument('--influence', type=float, default=.25)
    args = parser.parse_args()
    if args.watch and args.replay:
        parser.error('Historical replay must be a one-shot explicit check')
    if not (5 <= args.window_packets <= 350 and 2 <= args.min_candidates <= args.window_packets and 1 <= args.interval <= 60):
        parser.error('Invalid window, candidate count or polling interval')
    since = datetime.fromisoformat(args.since) if args.since else None
    if since and since.tzinfo is None:
        parser.error('--since requires timezone')
    condition_lifetime(args.reference_mean, 2, 0, args.influence)
    import fcntl
    lock = open('/tmp/smart-cylinder-demo-analysis.lock','a')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('DEMO_WORKER_ALREADY_RUNNING',flush=True)
        return 2
    model = runpy.run_path(str(Path(__file__).with_name('lifetime_condition_simulation.py')))
    cylinders = (args.cylinder,) if args.cylinder else model['PICOS']
    references, floors = {}, {}
    # Capture all device boundaries before the first reference build.
    for cylinder in cylinders:
        client = None
        try:
            client = model['PicoSQLiteClient'](args.root/'data', cylinder)
            floors[cylinder] = 0 if args.replay or since else client.highwater
        except Exception:
            floors[cylinder] = 0
        finally:
            if client: client.close()
    for cylinder in cylinders:
        client = None
        try:
            client = model['PicoSQLiteClient'](args.root/'data', cylinder)
            references[cylinder] = build_reference(client, model, args.root/'data'/('lifetime-demo-reference-pico'+cylinder[-2:]+'.json'))
            print('REFERENCE_READY '+cylinder,flush=True)
        except Exception as error:
            references[cylinder] = dict(status='REFERENCE_DATA_REQUIRED', reason=str(error) if isinstance(error,ValueError) else type(error).__name__, cylinder_id=cylinder)
            print('REFERENCE_DATA_REQUIRED '+cylinder,flush=True)
        finally:
            if client: client.close()
    cache = {}; activity_buffers={}; result=None
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    try:
        while True:
            result = tick(args.root/'data', cylinders, references, floors, model, args.min_candidates, args.window_packets,
                          since, args.replay, args.reference_mean, args.influence, cache, activity_buffers)
            result['worker_state']='RUNNING' if args.watch else ('HISTORICAL_REPLAY' if args.replay else 'ONE_SHOT')
            # Single-Pico checks preserve other devices but retain each device's own timestamp.
            old = args.root/'data/lifetime-demo-snapshot.json'
            if args.cylinder and old.exists():
                previous = json.loads(old.read_text(encoding='utf-8'))
                result['picos'] = {**previous.get('picos', {}), **result['picos']}
            write_json(old, result)
            write_json(args.root/'public/lifetime-demo-snapshot.json', result)
            print('DEMO_UPDATED '+','.join(k+':'+v['status'] for k,v in result['picos'].items()),flush=True)
            if not args.watch: break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        if result is not None:
            result['worker_state']='STOPPED';result['generated_at']=now().isoformat()
            for cylinder in cylinders:
                if cylinder in result['picos']:
                    result['picos'][cylinder].update(status='DEMO_STOPPED',scenario=None,generated_at=result['generated_at'])
            write_json(args.root/'data/lifetime-demo-snapshot.json',result)
            write_json(args.root/'public/lifetime-demo-snapshot.json',result)
        print('DEMO_STOPPED',flush=True)
    return 0


if __name__ == '__main__': raise SystemExit(main())
