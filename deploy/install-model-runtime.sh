#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/smart-cylinder-pi5"
MODEL_VENV="$PROJECT_DIR/venv-model"

if [[ ${EUID} -ne 0 ]]; then
  echo "sudo로 실행하세요: sudo bash deploy/install-model-runtime.sh"
  exit 1
fi

cd "$PROJECT_DIR"

if ! command -v python3.11 >/dev/null 2>&1; then
  apt-get update
  apt-get install -y python3.11 python3.11-venv libopenblas0
fi

python3.11 -m venv "$MODEL_VENV"
"$MODEL_VENV/bin/python" -m pip install --upgrade pip
"$MODEL_VENV/bin/python" -m pip install -r requirements-model-runtime.txt

# 저장된 Pipeline 클래스의 역직렬화에만 PyCaret 모듈 경로가 필요하다.
# --no-deps로 학습·비교용 전체 의존성 설치를 막는다.
"$MODEL_VENV/bin/python" -m pip install --no-deps pycaret==3.3.2

"$MODEL_VENV/bin/python" - <<'PY'
from pathlib import Path
import joblib

root = Path("/opt/smart-cylinder-pi5")
models = sorted((root / "models/pycaret_final").glob("*.pkl"))
if len(models) != 3:
    raise SystemExit(f"모델 파일 3개가 필요합니다. 현재: {len(models)}개")
for model_path in models:
    package = joblib.load(model_path)
    if not isinstance(package, dict) or "model" not in package:
        raise SystemExit(f"올바르지 않은 모델 패키지: {model_path}")
    print(f"모델 로드 확인: {model_path.name}")
PY

echo "모델 추론 환경 설치 완료: $MODEL_VENV"
echo "실행 예: $MODEL_VENV/bin/python run_three_pycaret_models.py --input <입력.xlsx>"
