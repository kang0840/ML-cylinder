"""JSON 또는 Excel을 정확히 3개의 PyCaret 단일 모델로 추론한다."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if sys.version_info >= (3,12):
    choices=[ROOT/".venv-pycaret/Scripts/python.exe",ROOT/"venv-model/bin/python"]
    target=next((p for p in choices if p.exists()),None)
    if target and Path(sys.executable).resolve()!=target.resolve(): os.execv(str(target),[str(target),str(Path(__file__).resolve()),*sys.argv[1:]])
    raise RuntimeError("PyCaret 실행에는 Python 3.9~3.11이 필요합니다.")
import pandas as pd
from src.three_model_inference import ThreeModelInference

FEATURES=["mean","standard_deviation","rms","maximum","minimum","peak","peak_to_peak","crest_factor","dominant_frequency","dominant_amplitude","spectral_energy"]
LABELS={"normal":"정상","pressure_drop":"압력 저하","seal_leak":"씰 누설","internal_wear":"내부 마모","unknown":"판정 불가"}

def show(result,name,count=None):
    print("\n"+"="*54); print("          스마트 실린더 AI 상태 진단"); print("="*54)
    print(f"  종합 상태       : {LABELS.get(result['overall_status'],result['overall_status'])}")
    print(f"  건강 점수       : {result['health_score']:5.1f}점")
    print(f"  실제 잔여수명   : 산출 불가 ({result['rul_status']})")
    print("-"*54)
    print(f"  음향 진동       : {LABELS.get(result['vibration']['prediction'],result['vibration']['prediction'])}")
    print(f"  진동 신뢰도     : {result['vibration']['confidence']*100:5.1f}%")
    print(f"  소리 상태       : {LABELS.get(result['sound']['prediction'],result['sound']['prediction'])}")
    print(f"  소리 신뢰도     : {result['sound']['confidence']*100:5.1f}%")
    print("="*54); print(f"  입력 파일       : {name}"); print("  실행 모델 수    : 3개")
    if count is not None: print(f"  처리 행 수      : {count:,}개 (화면은 마지막 행)")

def main():
    parser=argparse.ArgumentParser(description="스마트 실린더 3개 단일 모델 추론")
    parser.add_argument("--input",default=str(ROOT/"inputs/three_model_input_example.json")); parser.add_argument("--output",type=Path); parser.add_argument("--json",action="store_true")
    args=parser.parse_args(); path=Path(args.input.strip().strip('"').strip("'")).expanduser().resolve()
    if not path.exists(): parser.error(f"입력 파일을 찾을 수 없습니다: {path}")
    engine=ThreeModelInference(); count=None
    if path.suffix.lower()==".xlsx":
        df=pd.read_excel(path); required=[f"진동_{f}" for f in FEATURES]+[f"소리_{f}" for f in FEATURES]
        missing=[c for c in required if c not in df.columns]
        if missing: parser.error("Excel 필수 열 누락: "+", ".join(missing))
        vf=df[[f"진동_{f}" for f in FEATURES]].copy(); vf.columns=FEATURES
        sf=df[[f"소리_{f}" for f in FEATURES]].copy(); sf.columns=FEATURES
        results=engine.predict_many(vf,sf)
        result=results[-1]; count=len(results)
        out=pd.DataFrame([{"초 번호":df.iloc[i].get("초 번호",i+1),"진동 판정":LABELS.get(r["vibration"]["prediction"]),"진동 신뢰도":r["vibration"]["confidence"],"소리 판정":LABELS.get(r["sound"]["prediction"]),"소리 신뢰도":r["sound"]["confidence"],"건강 점수":r["health_score"],"RUL 상태":r["rul_status"]} for i,r in enumerate(results)])
        with pd.ExcelWriter(path,engine="openpyxl",mode="a",if_sheet_exists="replace") as writer: out.to_excel(writer,sheet_name="추론결과",index=False)
    elif path.suffix.lower()==".json":
        payload=json.loads(path.read_text(encoding="utf-8")); result=engine.predict(payload["vibration"],payload["sound"],context=payload.get("context"))
    else: parser.error("지원 형식은 .json과 .xlsx입니다.")
    rendered=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: args.output.write_text(rendered+"\n",encoding="utf-8")
    if args.json: print(rendered)
    else: show(result,path.name,count)
if __name__=="__main__": main()
