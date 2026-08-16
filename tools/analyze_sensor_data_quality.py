"""Excel 센서 데이터의 학습 적합성을 JSON으로 보고한다."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
from src.data_quality import assess_frame
def main():
    p=argparse.ArgumentParser();p.add_argument("--input",required=True,type=Path);p.add_argument("--sheet");p.add_argument("--interval",type=float,default=1.0);a=p.parse_args()
    frame=pd.read_excel(a.input,sheet_name=a.sheet or 0)
    aliases={"measured_at":"timestamp","dominant_frequency_hz":"dominant_frequency"}
    frame=frame.rename(columns={k:v for k,v in aliases.items() if k in frame and v not in frame})
    print(json.dumps(assess_frame(frame,a.interval).to_dict(),ensure_ascii=False,indent=2,default=str))
if __name__=="__main__":main()
