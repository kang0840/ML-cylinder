"""Read per-Pico DB usage metadata; never invent lifetime totals or modify raw DBs."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy


def build(directory, model=None):
    model = model or runpy.run_path(str(Path(__file__).with_name('lifetime_condition_simulation.py')))
    results = {}
    for cylinder in model['PICOS']:
        client = None
        result = dict(cylinder_id=cylinder, actual_total_cycles=None, prior_lifetime_cycles=None,
                      estimated_interval_cycles=None, raw_packets=None,
                      status='SOURCE_UNAVAILABLE', source_file='pico'+cylinder[-2:]+'-analysis.sqlite3')
        try:
            client = model['PicoSQLiteClient'](directory, cylinder)
            inventory = client.inventory()
            result.update(status='PACKETS_ONLY', source_inventory=inventory,
                          raw_packets=inventory['raw_packets'], source='PICO_SQLITE_READ_ONLY')
            if cylinder == 'cylinder_05':
                ids = sorted(model['CONFIRMED_STOP_SESSIONS'])
                sessions = client.table('collection_sessions').select('*').in_('session_id', ids).execute().data
                spans = []
                for session in sessions:
                    rows = client.db.execute(
                        "SELECT json_extract(document,'$.raw_payload.runtime.last_received_at') FROM raw_data "
                        "WHERE session_id=? AND id<=?", (session['session_id'], client.highwater)).fetchall()
                    if any(not row[0] for row in rows):
                        raise ValueError('Confirmed interval has missing receipt metadata')
                    times = [datetime.fromisoformat(row[0]) for row in rows]
                    if not times: raise ValueError('Confirmed interval has no raw packets')
                    session.update(first_received_at=min(times).isoformat(), last_received_at=max(times).isoformat(),
                                   observed_span_seconds=(max(times)-min(times)).total_seconds(), packets=len(times))
                    spans.append(session['observed_span_seconds'])
                snapshot = dict(cylinder_id=cylinder, physical_cylinder_id='CYL-005', sessions=sessions,
                                operation=dict(confirmed_stops=True, observed_operation_seconds=sum(spans)))
                video = model['apply_video_period'](snapshot)['video_usage']
                result['video_usage'] = video
                if video['status'] == 'VIDEO_PERIOD_INTERVAL_ESTIMATE':
                    result.update(status='CONFIRMED_INTERVAL_ESTIMATE',
                                  estimated_interval_cycles=video['estimated_interval_cycles'],
                                  confirmed_interval_packets=sum(s['packets'] for s in sessions))
            client.verify_source()
        except Exception as error:
            # Never expose DB URLs or raw contents; retain the distinction from a zero count.
            result['status'] = 'INTERVAL_UNAVAILABLE' if result['raw_packets'] is not None else 'SOURCE_UNAVAILABLE'
            result['error_type'] = type(error).__name__
        finally:
            if client: client.close()
        results[cylinder] = result
    return dict(status='DB_USAGE_SNAPSHOT', generated_at=datetime.now(timezone.utc).isoformat(), picos=results,
                limitation='Packets are not cycles. Only the confirmed Pico05 six-session video interval is converted. Lifetime totals remain unknown.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/smart-cylinder-pi5'))
    args = parser.parse_args()
    model = runpy.run_path(str(Path(__file__).with_name('lifetime_condition_simulation.py')))
    result = build(args.root/'data', model)
    model['atomic_json'](args.root/'data/lifetime-db-usage.json', result)
    # The existing writer uses the analysis variable name; this separate file has its own name.
    path = args.root/'public/lifetime-db-usage.js'
    temporary = path.with_suffix('.tmp')
    temporary.write_text('window.LIFETIME_DB_USAGE = '+json.dumps(result, ensure_ascii=False, allow_nan=False)+';\n', encoding='utf-8')
    temporary.replace(path)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__': raise SystemExit(main())
