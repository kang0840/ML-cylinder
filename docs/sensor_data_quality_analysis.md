# 스마트 실린더 센서 데이터 품질 분석

## 평가 대상

- `data/export/inference_results.xlsx`: 실제 추론 저장 흐름에서 생성된 파일
- `smart_cylinder_training.xlsx`: 기존 데모/통계 구역 학습 파일
- `data/smart_cylinder.db`: 노트북 재생으로 생성된 로컬 분석 DB

## 품질 점수

| 대상 | 점수 | 학습 사용 여부 |
|---|---:|---|
| inference_results.xlsx | 26/100 | 사용 금지 |
| smart_cylinder_training.xlsx | 0/100 | 실제 현장 모델 학습에 사용 금지 |

### inference_results.xlsx

- 4행: SPH0645 2행, INMP441 2행
- 모든 동작 상태가 `forward`, 모든 예측이 `normal`
- 모든 모델 버전이 `fallback-threshold-v1`
- Ground Truth, RMS, 주요 FFT 특징이 없음
- 검증된 클래스 개수: normal 0, pressure_low 0, seal_leak 0, internal_wear 0
- 학습 위험 행: 4/4
- timestamp 간격 단절 후보: 2개

### smart_cylinder_training.xlsx

- 881행 중 800행이 `v4_demo_seed`, 81행이 `project_statistical_zone`
- 800행이 동일 timestamp를 공유하며 독립적인 실험 세션이 아님
- SPH0645/INMP441 구분, 실린더 동작 상태, 실험 ID, 검증된 Ground Truth가 없음
- 음향 PCM 마이크 데이터에 `mm/s` 속도 단위를 사용해 물리량 정의가 맞지 않음
- 숫자 라벨 0~3은 실제 고장을 사람이 확인한 Ground Truth라는 근거가 없음
- 실제 현장 모델 학습 위험 행: 881/881

## 잘된 부분

- prediction, confidence, model_version을 이미 별도 저장함
- DB의 raw_sensor_data와 feature_data, ml_results가 분리되어 있음
- measurement_id와 센서 sequence로 중복을 방지함
- SPH0645와 INMP441을 별도 모델로 추론함
- Raw PCM을 정상 MQTT 경로에서는 보존할 수 있음

## 핵심 문제

1. Threshold prediction을 정답처럼 사용할 위험
2. 검증된 Ground Truth와 라벨 출처가 없음
3. 실험·세션·압력·하중 정보 부족
4. 동작 상태 종류 부족 또는 idle 고정
5. 연속 데이터를 임의 행 단위로 Train/Test 분할할 누출 위험
6. 재생용 특징 데이터는 Raw PCM이 없어 재전처리가 불가능
7. 실제 run-to-failure 데이터가 없어 RUL 학습 불가능

## 수정된 저장 원칙

`prediction`은 항상 모델 출력이며 `ground_truth`를 덮어쓰지 않는다. 다음 세 출처만 학습 가능한 Ground Truth로 인정한다.

- controlled_experiment
- human_verified
- maintenance_verified

Ground Truth가 있는 행은 experiment_id와 session_id가 반드시 있어야 한다. fallback 모델 결과만 존재하는 행은 추론 이력으로 저장할 수 있지만 학습 데이터에서는 제외한다.

## 수정된 동작 상태

- idle
- extending
- extended
- retracting
- retracted

기존 `forward`와 `backward` 입력은 각각 `extending`, `retracting`으로 정규화해 기존 장치 호환성을 유지한다.

## 권장 수집 JSON

```json
{
  "device_id": "pico01",
  "boot_id": "boot-20260816-01",
  "sensor_id": "dual_i2s_mic",
  "sample_rate_hz": 24000,
  "sequence": 1,
  "timestamp": 1786838400,
  "cylinder_state": "extending",
  "experiment_id": "seal_leak_01",
  "session_id": "seal_leak_01_run_01",
  "pressure_mpa": 0.5,
  "load_kg": 0,
  "ground_truth": "seal_leak",
  "ground_truth_source": "controlled_experiment",
  "left_samples": [0.1, 0.2],
  "right_samples": [0.3, 0.4]
}
```

## 학습 분할

행 단위 무작위 분할을 사용하지 않는다. `experiment_id` 단위 GroupShuffleSplit으로 시험 실험을 완전히 분리하고, 학습 내부 교차검증도 GroupKFold를 사용한다.

## 수정 전/후 흐름

수정 전:

`Raw/Feature → Threshold Prediction → condition_target로 혼동 가능 → 행 단위 학습`

수정 후:

`실험 조건 + 검증 Ground Truth → Raw PCM → FFT/Feature → Prediction → Ground Truth와 비교 → experiment_id 단위 학습/평가`

## RUL에 추가로 필요한 데이터

- 실린더별 누적 운전 시간과 누적 사이클
- 최초 설치·정비·부품 교체 시각
- 실제 고장 또는 사용 한계 도달 시각
- 압력·하중·속도 프로파일
- 동일 실린더를 고장까지 추적한 독립 run-to-failure 세션

