"""Publish only a bounded scalar RMS snapshot; never uploads or edits raw PCM."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import signal
import threading
import time
import urllib.request


def tail_records(path):
    if not path.exists():return []
    with path.open('rb') as handle:
        handle.seek(0,2);size=handle.tell();start=max(0,size-2*1024*1024);handle.seek(start)
        lines=handle.read().splitlines()
    if start:lines=lines[1:]
    records=[]
    for line in reversed(lines):
        try:row=json.loads(line)
        except ValueError:continue
        if row.get('kind')=='DERIVED_NOISE_REDUCED_PACKET':records.append(row)
        if len(records)==100:break
    return list(reversed(records))


def parent_identity(pid):
    try:return Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:return None


def publish_once(root,codec,url,key,sent):
    result={}
    source=hashlib.sha256(url.rstrip('/').encode()).hexdigest()
    for ch in codec['CHANNELS']:
        records=tail_records(root/'data'/('pico'+ch[-2:]+'-noise-reduced.jsonl'))
        if not records:result[ch]='WAITING_DERIVED_DATA';continue
        snapshot=codec['from_records'](records)
        if snapshot['source_id']!=source:raise ValueError('Source mismatch')
        value=codec['encode'](snapshot);digest=hashlib.sha256(value.encode()).hexdigest()
        if sent.get(ch)==digest:result[ch]='UNCHANGED';continue
        body=json.dumps(dict(setting_key='monitoring_noise_v1_'+ch,setting_value=value,
            updated_at=datetime.now(timezone.utc).isoformat())).encode()
        req=urllib.request.Request(url.rstrip('/')+'/rest/v1/admin_settings?on_conflict=setting_key',
            data=body,method='POST',headers={'apikey':key,'Authorization':'Bearer '+key,
                'Content-Type':'application/json','Prefer':'resolution=merge-duplicates,return=minimal'})
        with urllib.request.urlopen(req,timeout=4) as response:
            if response.status not in (200,201,204):raise ValueError('Publish not confirmed')
        sent[ch]=digest;result[ch]=dict(status='PUBLISHED',sequence=snapshot['rows'][-1][1],
                                      rows=len(snapshot['rows']),bytes=len(value))
    return result


def main():
    from dotenv import dotenv_values
    import fcntl
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/opt/smart-cylinder-pi5'))
    parser.add_argument('--parent-pid',type=int)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args();root=args.root;stopped=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stopped.set())
    lock=(root/'data/noise-publisher.lock').open('a')
    deadline=time.monotonic()+10
    while True:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
        except BlockingIOError:
            if stopped.wait(.5) or time.monotonic()>deadline:return 2
    values=dotenv_values(root/'.env')
    url=values.get('SUPABASE_URL') or os.environ.get('SUPABASE_URL')
    key=values.get('SUPABASE_SERVICE_ROLE_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not url or not key:raise ValueError('Server configuration required')
    codec=runpy.run_path(str(root/'deploy/noise_snapshot.py'));sent={}
    parent=parent_identity(args.parent_pid) if args.parent_pid else None
    if args.parent_pid and not parent:return 2
    status_path=root/'data/noise-publish-status.json'
    while not stopped.is_set() and (not args.parent_pid or parent_identity(args.parent_pid)==parent):
        try:
            states=publish_once(root,codec,url,key,sent);status='PUBLISHED'
        except Exception as error:
            states={'error_type':type(error).__name__};status='RETRYING'
        temporary=status_path.with_name(status_path.name+'.incoming')
        temporary.write_text(json.dumps(dict(status=status,pid=os.getpid(),parent_pid=args.parent_pid,
            generated_at=datetime.now(timezone.utc).isoformat(),cylinders=states)))
        temporary.replace(status_path)
        if args.once:return 0 if status=='PUBLISHED' else 2
        stopped.wait(3)
    return 0


if __name__=='__main__':raise SystemExit(main())
