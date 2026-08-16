"""센서 학습 데이터의 품질을 평가하되 이상 신호를 자동 삭제하지 않는다."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import pandas as pd
import numpy as np

REQUIRED_TRAINING_COLUMNS=("timestamp","sensor_type","cylinder_state","ground_truth","rms","dominant_frequency","dominant_amplitude")
GROUND_TRUTH_CLASSES=("normal","pressure_low","seal_leak","internal_wear")
FALLBACK_PREFIX="fallback-"

@dataclass(frozen=True)
class QualityReport:
    row_count:int; score:int; missing_columns:list[str]; missing_values:dict[str,int]
    duplicate_count:int; invalid_timestamp_count:int; interval_gap_count:int
    suggested_session_breaks:int; outlier_flag_count:int
    sensor_counts:dict[str,int]; state_counts:dict[str,int]
    class_counts:dict[str,int]; class_ratios:dict[str,float]
    unsafe_training_rows:int; warnings:list[str]
    def to_dict(self)->dict[str,Any]:return asdict(self)

def assess_frame(frame:pd.DataFrame, expected_interval_seconds:float=1.0, gap_multiplier:float=3.0)->QualityReport:
    data=frame.copy(); missing_columns=[c for c in REQUIRED_TRAINING_COLUMNS if c not in data]
    missing_values={c:int(data[c].isna().sum()) for c in REQUIRED_TRAINING_COLUMNS if c in data}
    timestamp_col="timestamp" if "timestamp" in data else "measured_at" if "measured_at" in data else None
    times=pd.to_datetime(data[timestamp_col],utc=True,errors="coerce") if timestamp_col else pd.Series(pd.NaT,index=data.index)
    invalid_times=int(times.isna().sum())
    duplicate_count=int(data.duplicated(subset=[c for c in ("sensor_type",timestamp_col) if c],keep=False).sum()) if timestamp_col and "sensor_type" in data else 0
    ordered=pd.DataFrame({"sensor":data.get("sensor_type","unknown"),"time":times}).dropna().sort_values(["sensor","time"])
    deltas=ordered.groupby("sensor")["time"].diff().dt.total_seconds()
    interval_gaps=int((deltas>expected_interval_seconds*gap_multiplier).sum())
    numeric=[c for c in ("rms","peak","crest_factor","dominant_frequency","dominant_amplitude") if c in data]
    outlier_rows=pd.Series(False,index=data.index)
    for column in numeric:
        values=pd.to_numeric(data[column],errors="coerce"); median=values.median(); mad=(values-median).abs().median()
        if pd.notna(mad) and mad>0:outlier_rows|=((values-median).abs()/(1.4826*mad)>6)
    class_counts={name:int((data.get("ground_truth",pd.Series(index=data.index,dtype=object))==name).sum()) for name in GROUND_TRUTH_CLASSES}
    labelled=sum(class_counts.values()); class_ratios={k:round(v/labelled,4) if labelled else 0.0 for k,v in class_counts.items()}
    fallback=data.get("model_version",pd.Series("",index=data.index)).astype(str).str.startswith(FALLBACK_PREFIX)
    truth_missing=data.get("ground_truth",pd.Series(np.nan,index=data.index)).isna()
    source_untrusted=~data.get("ground_truth_source",pd.Series("",index=data.index)).isin({"controlled_experiment","human_verified","maintenance_verified"})
    unsafe=int((truth_missing|source_untrusted|fallback&truth_missing).sum())
    warnings=[]
    if missing_columns:warnings.append("학습 필수 컬럼이 없습니다.")
    if labelled==0:warnings.append("검증된 Ground Truth가 한 건도 없습니다.")
    if class_ratios and max(class_ratios.values(),default=0)>.7:warnings.append("특정 Ground Truth 클래스가 70%를 초과합니다.")
    if data.get("cylinder_state",pd.Series(dtype=object)).nunique()<=1:warnings.append("실린더 동작 상태가 한 종류뿐입니다.")
    if duplicate_count:warnings.append("동일 센서·timestamp 중복 후보가 있습니다.")
    if interval_gaps:warnings.append("예상 수집 간격을 벗어난 구간은 새 session 후보입니다.")
    penalty=min(100,len(missing_columns)*8+sum(missing_values.values())*2+min(25,unsafe*2)+min(15,duplicate_count)+min(10,interval_gaps*2)+(20 if labelled==0 else 0)+(10 if data.get("cylinder_state",pd.Series(dtype=object)).nunique()<=1 else 0))
    return QualityReport(len(data),max(0,100-penalty),missing_columns,missing_values,duplicate_count,invalid_times,interval_gaps,interval_gaps,int(outlier_rows.sum()),data.get("sensor_type",pd.Series(dtype=object)).value_counts().to_dict(),data.get("cylinder_state",pd.Series(dtype=object)).value_counts().to_dict(),class_counts,class_ratios,unsafe,warnings)

def training_safe(frame:pd.DataFrame)->pd.DataFrame:
    """검증된 실제 라벨과 실험 ID가 있는 행만 반환한다."""
    required={"ground_truth","ground_truth_source","experiment_id","session_id"}
    if not required.issubset(frame.columns):return frame.iloc[0:0].copy()
    return frame[frame.ground_truth.notna()&frame.ground_truth_source.isin({"controlled_experiment","human_verified","maintenance_verified"})&frame.experiment_id.notna()&frame.session_id.notna()].copy()
