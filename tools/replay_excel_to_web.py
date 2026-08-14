"""저장된 1초 Excel 특징을 센서 스트림처럼 추론하여 웹 DB에 기록한다."""
from __future__ import annotations
import argparse, os, sqlite3, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if sys.version_info >= (3, 12):
    target = ROOT / ".venv-pycaret" / "Scripts" / "python.exe"
    if target.exists() and Path(sys.executable).resolve() != target.resolve():
        os.execv(str(target), [str(target), str(Path(__file__).resolve()), *sys.argv[1:]])

import pandas as pd
from src.database import Database
from src.three_model_inference import ThreeModelInference

FEATURES = ["mean","standard_deviation","rms","maximum","minimum","peak","peak_to_peak","crest_factor","dominant_frequency","dominant_amplitude","spectral_energy"]

def insert_row(connection, engine, row, device, boot, sequence, measured_at):
    vibration = {f: float(row[f"진동_{f}"]) for f in FEATURES}
    sound = {f: float(row[f"소리_{f}"]) for f in FEATURES}
    result = engine.predict(vibration, sound)
    ids = {"sph0645": str(uuid.uuid4()), "inmp441": str(uuid.uuid4())}
    state = str(row.get("실린더 상태", "idle"))
    if state not in {"forward", "backward", "idle"}: state = "idle"
    for sensor, prefix, sensor_result in (("sph0645","진동",result["vibration"]),("inmp441","소리",result["sound"])):
        mid = ids[sensor]; features = vibration if sensor == "sph0645" else sound
        connection.execute("INSERT INTO measurements(measurement_id,device_id,boot_id,sensor_type,measured_at,sample_rate,cylinder_state,sequence,sample_count) VALUES(?,?,?,?,?,?,?,?,?)",(mid,device,boot,sensor,measured_at,24000,state,sequence,0))
        connection.execute("INSERT INTO feature_data(measurement_id,device_id,sensor_type,measured_at,cylinder_state,mean,standard_deviation,rms,maximum,minimum,peak,peak_to_peak,crest_factor,dominant_frequency,dominant_amplitude,spectral_energy) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,device,sensor,measured_at,state,*(features[f] for f in FEATURES)))
        health = result["health_score"]
        connection.execute("INSERT INTO ml_results(measurement_id,device_id,measured_at,cylinder_state,prediction,confidence,health_score,model_version) VALUES(?,?,?,?,?,?,?,?)",(mid,device,measured_at,state,sensor_result["prediction"],sensor_result["confidence"],health,sensor_result["model_version"]))
    controlling = "acoustic_vibration" if result["vibration"]["prediction"] == result["overall_status"] else "sound"
    connection.execute("INSERT INTO combined_results(device_id,boot_id,sequence,measured_at,vibration_measurement_id,sound_measurement_id,prediction,confidence,health_score,remaining_life_percent,remaining_hours,remaining_cycles,rul_lower_bound,rul_upper_bound,rul_status,rul_model_version,controlling_role,fusion_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(device,boot,sequence,measured_at,ids["sph0645"],ids["inmp441"],result["overall_status"],max(result["vibration"]["confidence"],result["sound"]["confidence"]),result["health_score"],None,None,None,None,None,result["rul_status"],result["rul_model_version"],controlling,"three-single-model-v1"))
    connection.commit(); return result

def main():
    parser=argparse.ArgumentParser(description="Excel 1초 데이터를 웹 모니터 DB로 재생")
    parser.add_argument("--input",required=True,type=Path); parser.add_argument("--database",type=Path,default=ROOT/"data/smart_cylinder.db")
    parser.add_argument("--interval",type=float,default=1.0); parser.add_argument("--limit",type=int); parser.add_argument("--device",default="notebook-replay")
    args=parser.parse_args(); source=args.input.resolve(); database=args.database.resolve()
    if not source.exists(): parser.error(f"입력 파일을 찾을 수 없습니다: {source}")
    frame=pd.read_excel(source); required=[f"{p}_{f}" for p in ("진동","소리") for f in FEATURES]
    missing=[c for c in required if c not in frame.columns]
    if missing: parser.error("Excel 필수 열 누락: "+", ".join(missing))
    if args.limit: frame=frame.head(args.limit)
    Database(database).close(); engine=ThreeModelInference(); boot="excel-"+datetime.now().strftime("%Y%m%d-%H%M%S")
    with sqlite3.connect(database) as connection:
        for i,(_,row) in enumerate(frame.iterrows(),1):
            result=insert_row(connection,engine,row,args.device,boot,i,datetime.now(timezone.utc).isoformat())
            print(f"[{i}/{len(frame)}] {result['overall_status']} / 건강 점수 {result['health_score']:.1f}",flush=True)
            if args.interval and i < len(frame): time.sleep(args.interval)
    print(f"웹 연동 DB 저장 완료: {database}")
if __name__=="__main__": main()
