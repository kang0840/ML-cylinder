"""장시간 수집 중단과 부팅 변경을 기준으로 자동 session_id를 부여한다."""
from __future__ import annotations
from datetime import datetime,timezone
import threading
import re

class SessionTracker:
    def __init__(self,gap_seconds:float=3.0):self.gap_seconds=gap_seconds;self._states={};self._lock=threading.Lock()
    def enrich(self,payload:dict)->dict:
        if payload.get("session_id"):return payload
        device=str(payload.get("device_id","unknown"));boot=str(payload.get("boot_id","legacy"));timestamp=float(payload.get("timestamp",0))
        key=(device,boot)
        with self._lock:
            current=self._states.get(key)
            if current is None or timestamp-current[0]>self.gap_seconds or timestamp<current[0]:
                stamp=datetime.fromtimestamp(timestamp,timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                safe_device=re.sub(r"[^A-Za-z0-9_-]","_",device)
                session=f"auto_{safe_device}_{stamp}"[:64];self._states[key]=(timestamp,session)
            else:self._states[key]=(timestamp,current[1])
            session=self._states[key][1]
        return {**payload,"session_id":session}
