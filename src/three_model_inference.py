"""실시간과 파일 추론이 함께 사용하는 3개 단일 모델 실행기."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATHS = {
    "acoustic_vibration": ROOT / "models/pycaret_final/pycaret_final_vibration_model.pkl",
    "sound": ROOT / "models/pycaret_final/pycaret_final_sound_model.pkl",
    "rul": ROOT / "models/pycaret_final/pycaret_final_life_model.pkl",
}
SEVERITY = {"normal": 0, "pressure_drop": 1, "seal_leak": 2, "internal_wear": 3, "unknown": 4}

class _PackagePredictor:
    def __init__(self, path: Path):
        self.package = joblib.load(path)
        self.model = self.package["model"]
        self.feature_names = list(self.package["feature_names"])
    def frame(self, values):
        missing = [name for name in self.feature_names if name not in values]
        if missing: raise ValueError("필수 특징 누락: " + ", ".join(missing))
        return pd.DataFrame([{n: values[n] for n in self.feature_names}], columns=self.feature_names)

class AcousticVibrationPredictor(_PackagePredictor):
    role = "acoustic_vibration"
    def predict(self, features): return _condition(self, features)

class SoundPredictor(_PackagePredictor):
    role = "sound"
    def predict(self, features): return _condition(self, features)

def _condition(predictor, features):
    frame = predictor.frame(features); prediction = str(predictor.model.predict(frame)[0])
    confidence = float(np.max(predictor.model.predict_proba(frame)[0])) if hasattr(predictor.model,"predict_proba") else None
    return {"prediction": prediction, "confidence": confidence, "model_version": predictor.package.get("model_version", "unknown"), "model_count": 1}

class RULPredictor(_PackagePredictor):
    """현재 모델은 건강 점수만 산출한다. 실제 수명 데이터가 없으면 RUL을 만들지 않는다."""
    def predict(self, values):
        frame = self.frame(values)
        health = float(np.clip(self.model.predict(frame)[0], 0, 100))
        has_lifecycle = all(values.get(k) is not None for k in ("operating_hours", "cumulative_cycles"))
        return {
            "health_score": round(health, 2),
            "remaining_life_percent": None,
            "remaining_hours": None,
            "remaining_cycles": None,
            "rul_lower_bound": None,
            "rul_upper_bound": None,
            "rul_status": "model_requires_run_to_failure_labels" if has_lifecycle else "insufficient_lifecycle_data",
            "rul_model_version": self.package.get("model_version", "unknown"),
            "model_count": 1,
        }

class ThreeModelInference:
    def __init__(self):
        self.vibration = AcousticVibrationPredictor(MODEL_PATHS["acoustic_vibration"])
        self.sound = SoundPredictor(MODEL_PATHS["sound"])
        self.rul = RULPredictor(MODEL_PATHS["rul"])
    def predict(self, vibration_features, sound_features, *, context=None):
        vibration = self.vibration.predict(vibration_features)
        sound = self.sound.predict(sound_features)
        overall = max((vibration["prediction"], sound["prediction"]), key=lambda x: SEVERITY.get(x,4))
        rul_input = {"vibration_prediction": vibration["prediction"], "vibration_confidence": vibration["confidence"], "sound_prediction": sound["prediction"], "sound_confidence": sound["confidence"], **(context or {})}
        life = self.rul.predict(rul_input)
        return {"overall_status": overall, "vibration": vibration, "sound": sound, **life, "total_model_count": 3}

    def predict_many(self, vibration_frame: pd.DataFrame, sound_frame: pd.DataFrame):
        """Excel용 벡터 추론. 단건 추론과 동일한 모델과 출력 규칙을 사용한다."""
        vp = self.vibration.model.predict(vibration_frame).astype(str)
        sp = self.sound.model.predict(sound_frame).astype(str)
        vc = np.max(self.vibration.model.predict_proba(vibration_frame), axis=1)
        sc = np.max(self.sound.model.predict_proba(sound_frame), axis=1)
        life_frame = pd.DataFrame({"vibration_prediction":vp,"vibration_confidence":vc,"sound_prediction":sp,"sound_confidence":sc})
        health = np.clip(self.rul.model.predict(life_frame),0,100)
        return [{"overall_status":max((vp[i],sp[i]),key=lambda x:SEVERITY.get(x,4)),"vibration":{"prediction":vp[i],"confidence":float(vc[i]),"model_version":self.vibration.package.get("model_version","unknown"),"model_count":1},"sound":{"prediction":sp[i],"confidence":float(sc[i]),"model_version":self.sound.package.get("model_version","unknown"),"model_count":1},"health_score":round(float(health[i]),2),"remaining_life_percent":None,"remaining_hours":None,"remaining_cycles":None,"rul_lower_bound":None,"rul_upper_bound":None,"rul_status":"insufficient_lifecycle_data","rul_model_version":self.rul.package.get("model_version","unknown"),"model_count":1,"total_model_count":3} for i in range(len(vp))]
