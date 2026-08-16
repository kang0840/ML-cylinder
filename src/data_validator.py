"""Pico W 센서 패킷과 실험 메타데이터 검증."""
from __future__ import annotations
import math, re, time
from dataclasses import dataclass
from typing import Any

VALID_SENSORS={"sph0645","inmp441"}
SENSOR_ROLES={"sph0645":"acoustic_vibration","inmp441":"sound"}
VALID_STATES={"idle","extending","extended","retracting","retracted"}
LEGACY_STATE_MAP={"forward":"extending","backward":"retracting"}
VALID_GROUND_TRUTH={"normal","pressure_low","seal_leak","internal_wear"}
TRUSTED_GROUND_TRUTH_SOURCES={"controlled_experiment","human_verified","maintenance_verified"}
REQUIRED_FIELDS={"device_id","sensor_type","sample_rate","cylinder_state","sequence","timestamp","samples"}
MAX_SAMPLES=100_000

@dataclass(frozen=True,slots=True)
class SensorPacket:
    device_id:str; sensor_type:str; sample_rate:int; cylinder_state:str
    sequence:int; timestamp:float; samples:tuple[float,...]
    health_score_target:float|None=None; boot_id:str="legacy"
    condition_target:str|None=None  # 읽기 호환용. 신규 학습에는 사용하지 않는다.
    experiment_id:str|None=None; session_id:str|None=None
    pressure_mpa:float|None=None; load_kg:float|None=None
    ground_truth:str|None=None; ground_truth_source:str|None=None

def _optional_number(payload:dict,name:str,minimum:float=0.0)->float|None:
    value=payload.get(name)
    if value is None:return None
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum:
        raise ValueError(f"{name}는 {minimum} 이상의 유한한 숫자여야 합니다.")
    return float(value)

def validate_sensor_payload(payload:Any)->SensorPacket:
    if not isinstance(payload,dict):raise ValueError("메시지는 JSON 객체여야 합니다.")
    missing=REQUIRED_FIELDS-payload.keys()
    if missing:raise ValueError("필수 항목 누락: "+", ".join(sorted(missing)))
    device=str(payload["device_id"]).strip(); sensor=payload["sensor_type"]
    state=LEGACY_STATE_MAP.get(payload["cylinder_state"],payload["cylinder_state"])
    rate=payload["sample_rate"]; sequence=payload["sequence"]; timestamp=payload["timestamp"]; samples=payload["samples"]
    if not device or len(device)>64:raise ValueError("device_id는 1~64자여야 합니다.")
    if sensor not in VALID_SENSORS:raise ValueError(f"sensor_type은 {sorted(VALID_SENSORS)} 중 하나여야 합니다.")
    if state not in VALID_STATES:raise ValueError(f"cylinder_state는 {sorted(VALID_STATES)} 중 하나여야 합니다.")
    if isinstance(rate,bool) or not isinstance(rate,int) or not 1<=rate<=192_000:raise ValueError("sample_rate는 1~192000 정수여야 합니다.")
    if isinstance(sequence,bool) or not isinstance(sequence,int) or sequence<0:raise ValueError("sequence는 0 이상 정수여야 합니다.")
    if isinstance(timestamp,bool) or not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp) or timestamp<=0:raise ValueError("timestamp는 양의 Unix 시간이어야 합니다.")
    if not isinstance(samples,list) or not 2<=len(samples)<=MAX_SAMPLES:raise ValueError(f"samples는 2~{MAX_SAMPLES}개 배열이어야 합니다.")
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in samples):raise ValueError("samples에는 유한한 숫자만 허용됩니다.")
    boot=str(payload.get("boot_id","legacy")).strip()
    if not boot or len(boot)>64:raise ValueError("boot_id는 1~64자여야 합니다.")
    experiment=payload.get("experiment_id"); session=payload.get("session_id")
    for name,value in (("experiment_id",experiment),("session_id",session)):
        if value is not None and (not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",value)):
            raise ValueError(f"{name}는 영문·숫자·_- 조합 1~64자여야 합니다.")
    truth=payload.get("ground_truth"); source=payload.get("ground_truth_source")
    if truth is not None:
        if truth not in VALID_GROUND_TRUTH:raise ValueError(f"ground_truth는 {sorted(VALID_GROUND_TRUTH)} 중 하나여야 합니다.")
        if source not in TRUSTED_GROUND_TRUTH_SOURCES:raise ValueError("ground_truth에는 검증된 ground_truth_source가 반드시 필요합니다.")
        if not experiment or not session:raise ValueError("ground_truth 데이터에는 experiment_id와 session_id가 필요합니다.")
    elif source is not None:raise ValueError("ground_truth_source는 ground_truth와 함께 입력해야 합니다.")
    # 기존 condition_target은 예측값과 혼동될 수 있어 신규 입력에서 학습 라벨로 승격하지 않는다.
    legacy=payload.get("condition_target")
    target=_optional_number(payload,"health_score_target")
    if target is not None and target>100:raise ValueError("health_score_target은 0~100이어야 합니다.")
    return SensorPacket(device,sensor,rate,state,sequence,float(timestamp),tuple(float(v) for v in samples),target,boot,legacy,experiment,session,_optional_number(payload,"pressure_mpa"),_optional_number(payload,"load_kg"),truth,source)

def expand_dual_microphone_payload(payload:Any)->tuple[dict[str,Any],...]:
    if not isinstance(payload,dict) or payload.get("sensor_id")!="dual_i2s_mic":return (payload,)
    required={"device_id","sequence","sample_rate_hz","left_samples","right_samples"}; missing=required-payload.keys()
    if missing:raise ValueError("이중 마이크 필수 항목 누락: "+", ".join(sorted(missing)))
    common={k:payload[k] for k in ("device_id","sequence")}
    common.update({"sample_rate":payload["sample_rate_hz"],"cylinder_state":payload.get("cylinder_state","idle"),"timestamp":payload.get("timestamp",time.time()),"boot_id":payload.get("boot_id","legacy")})
    for key in ("experiment_id","session_id","pressure_mpa","load_kg","ground_truth","ground_truth_source","health_score_target"):
        if key in payload:common[key]=payload[key]
    return ({**common,"sensor_type":"sph0645","samples":payload["left_samples"]},{**common,"sensor_type":"inmp441","samples":payload["right_samples"]})
