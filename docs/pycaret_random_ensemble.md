# PyCaret 무작위 3개 모델 조합 비교

이 도구는 학습 파일에서 PyCaret 후보 모델 3개를 seed 기반으로 무작위 선택한 뒤 아래 7개 구성을 비교합니다.

- 단일 모델 3개
- 두 모델을 결합한 blend 3개
- 세 모델을 모두 결합한 blend 1개

최고 모델은 **학습 파일의 교차검증 점수**로만 선택합니다. 테스트 파일은 모델 선택에 사용하지 않고 최종 성능 확인에만 사용하므로 테스트 데이터 누수를 방지합니다.

## 환경

PyCaret 3.3.2는 이 프로젝트의 기본 Python 3.12가 아닌 Python 3.11 전용 가상환경에서 실행합니다.

```powershell
py -3.11 -m venv .venv-pycaret
.\.venv-pycaret\Scripts\python.exe -m pip install -r requirements-pycaret.txt
```

## 데이터 파일

CSV, TSV, XLS, XLSX를 지원합니다. 학습 파일과 테스트 파일에는 같은 특징 열과 정답 열이 있어야 합니다. ID 및 시각 열은 기본적으로 특징에서 제외됩니다.

권장 운영 흐름은 세 번의 독립 실행입니다.

1. 진동 특징 → 진동 상태 정답
2. 소리 특징 → 소리 상태 정답
3. 진동·소리 추론 결과 → 실린더 잔여수명 정답

수명 모델을 시간 또는 cycle 단위로 학습하려면 테스트·학습 파일에 실제 고장 시점에서 산출한 RUL 정답이 필요합니다. 현재처럼 건강도만 있다면 0~100 잔여수명률 회귀로 학습합니다.

## 실행 예시

분류:

```powershell
.\.venv-pycaret\Scripts\python.exe .\tools\pycaret_random_ensemble.py `
  --train .\data\train.xlsx `
  --test .\data\test.xlsx `
  --target condition_target `
  --task classification `
  --seed 42
```

잔여수명률 회귀:

```powershell
.\.venv-pycaret\Scripts\python.exe .\tools\pycaret_random_ensemble.py `
  --train .\data\life_train.xlsx `
  --test .\data\life_test.xlsx `
  --target remaining_life_percent `
  --task regression `
  --features vibration_health,sound_health,vibration_confidence,sound_confidence
```

세 모델을 모두 포함한 blend만 최종 모델 후보로 허용하려면 다음 옵션을 추가합니다.

```powershell
--selection-scope full_blend
```

결과는 기본적으로 `data/model_comparison/random_ensemble`에 저장됩니다.

- `ensemble_leaderboard.xlsx`: 교차검증 및 독립 테스트 점수
- `ensemble_leaderboard.csv`: 같은 결과의 CSV
- `test_predictions.csv`: 선택된 모델의 테스트 예측값
- `best_model_package.pkl`: 선택된 최종 PyCaret 모델
- `run_summary.json`: 무작위 후보, seed, 특징 및 선택 근거

운영 모델은 테스트 결과를 검토한 뒤에만 별도로 배포해야 하며, 이 도구는 현재 운영 모델을 자동으로 덮어쓰지 않습니다.
