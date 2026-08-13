# Raspberry Pi 5 스마트 실린더 수집·분석 시스템

Raspberry Pi Pico W가 MQTT로 전송하는 SPH0645/INMP441 원시 샘플을 Raspberry Pi 5에서 수신하고, 검증·SQLite 저장·FFT·특징 추출·머신러닝 추론·Supabase 요약 업로드까지 수행하는 오프라인 우선 서비스입니다. MQTT 콜백은 수신 원본을 `ingest_queue`에 즉시 영구 저장하고 반환하므로 인터넷이나 Supabase 지연이 센서 수신을 막지 않습니다.

## Raspberry Pi 5의 역할

Pi 5는 Mosquitto 브로커와 Python subscriber를 실행하는 중앙 처리 장치입니다. Pico W는 센서값 발행만 담당합니다. 인터넷이나 Supabase가 중단돼도 MQTT 수신, 로컬 저장, FFT 및 추론은 계속되며 업로드 실패 데이터는 `upload_queue`에 남아 자동 재시도됩니다.

```text
SPH0645 / INMP441 → Pico W → Wi-Fi MQTT(QoS 1) → Raspberry Pi 5
  → JSON 검증 → SQLite 원시값 저장 → FFT/특징 → ML 추론
  → SQLite ingest_queue 즉시 저장
  → 최신 데이터 우선 FFT·추론·결과 저장
  → 별도 작업에서 Supabase 요약 업로드(실패 시 재시도 큐)
```

구독 토픽은 `smartCylinder/+/sph0645/raw`, `smartCylinder/+/inmp441/raw`, `smartCylinder/+/status`입니다. 중복은 `device_id + sensor_type + sequence`로 차단하고 sequence 누락과 역순 수신을 로그에 기록합니다.

## 프로젝트 구조

```text
├── main.py                       # Pi 5 서비스 진입점
├── config/settings.py            # .env 설정 및 검증
├── src/
│   ├── mqtt_receiver.py          # MQTT QoS 1 subscriber
│   ├── data_validator.py         # 패킷 검증
│   ├── database.py               # WAL SQLite/트랜잭션
│   ├── fft_processor.py          # DC 제거, Hann window, rFFT
│   ├── feature_extractor.py      # 시간·주파수 특징
│   ├── ml_predictor.py           # joblib 모델/시험용 fallback
│   ├── pipeline.py               # 전체 처리 흐름
│   ├── supabase_uploader.py      # 업로드 및 재시도
│   └── export_manager.py         # CSV/Excel 내보내기
├── tools/
│   ├── generate_dummy_data.py    # 실제 MQTT 더미 발행
│   ├── export_data.py            # 요청 시 보고서 생성
│   └── inspect_database.py       # DB 확인
├── tests/                        # 핵심 단위 테스트
├── deploy/smart-cylinder.service # systemd 예제
├── supabase_schema.sql           # Supabase 테이블 SQL
├── data/backup, data/export      # 백업/내보내기 경로
├── logs/                         # 회전 로그
└── models/cylinder_model.pkl     # 선택적 실제 모델
```

기존 데모용 웹/알고리즘 파일은 참고 호환 자산이며, 운영 데이터 수집 경로에는 사용하지 않습니다.

## 데이터 형식

```json
{
  "device_id": "pico01",
  "sensor_type": "sph0645",
  "sample_rate": 1600,
  "cylinder_state": "forward",
  "sequence": 1,
  "timestamp": 1785830000,
  "samples": [125, 128, 131, 129]
}
```

센서는 `sph0645` 또는 `inmp441`, 상태는 `forward`, `backward`, `idle`만 허용합니다. 샘플은 유한한 숫자 2~100,000개여야 합니다. 시스템 시간이 잘못된 Pi에서는 timestamp도 틀어지므로 `timedatectl status`로 NTP 동기화를 확인하십시오.

## Raspberry Pi OS 설치

64비트 Raspberry Pi OS와 Python 3.13을 기준으로 합니다. NumPy/SciPy wheel을 이용할 수 있도록 pip를 먼저 갱신합니다.

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients python3.13 python3.13-venv libopenblas0
sudo systemctl enable --now mosquitto

cd /opt/smart-cylinder-pi5
python3.13 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

`.env`의 Supabase 값을 실제 값으로 바꾸십시오. 키는 소스에 넣지 않습니다. 외부 MQTT 접속을 허용하려면 Mosquitto에 인증과 방화벽을 구성해야 하며, 익명 포트 1883을 인터넷에 노출하면 안 됩니다.

## 실행과 자동 시작

```bash
source venv/bin/activate
python main.py
```

부팅 자동 실행은 서비스 파일의 사용자와 설치 경로를 실제 환경에 맞춘 뒤 설정합니다.

```bash
sudo cp deploy/smart-cylinder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smart-cylinder
sudo journalctl -u smart-cylinder -f
```

종료는 터미널에서 `Ctrl+C`, 서비스에서는 `sudo systemctl stop smart-cylinder`를 사용합니다. 애플리케이션 로그는 `logs/smart_cylinder.log`에 회전 저장됩니다.

## 더미 MQTT 종단 테스트

터미널 1에서 `python main.py`를 실행하고 터미널 2에서 다음을 실행합니다.

```bash
source venv/bin/activate
python tools/generate_dummy_data.py --condition normal --count 5
python tools/generate_dummy_data.py --condition seal_leak --count 5 --device-id pico02
python tools/inspect_database.py
```

지원 조건은 `normal`, `pressure_drop`, `seal_leak`, `internal_wear`, `random_noise`입니다. 기본 fallback 판정은 연결 확인용일 뿐 실제 고장 진단 모델이 아닙니다.

## 실제 머신러닝 모델

`models/cylinder_model.pkl`이 없거나 손상돼도 서비스는 종료되지 않고 시험용 임계 판정으로 동작합니다. 실제 모델은 다음 형태 중 하나를 joblib로 저장할 수 있습니다.

```python
joblib.dump({
    "model": trained_model,
    "feature_names": ["mean", "standard_deviation", "rms", "maximum", "minimum", "peak", "peak_to_peak", "crest_factor", "dominant_frequency", "dominant_amplitude", "spectral_energy"],
    "model_version": "cylinder-rf-2026-08-04"
}, "models/cylinder_model.pkl")
```

모델 출력은 `normal`, `pressure_drop`, `seal_leak`, `internal_wear`, `unknown` 중 하나여야 합니다. 센서 종류와 동작 상태별 실제 라벨 데이터로 검증해야 합니다.

### PyCaret 모델 비교

건강 점수 정답(`health_score_target`)이 있는 측정값이 30건 이상 쌓이면
PyCaret으로 회귀 모델을 비교할 수 있습니다. PyCaret 3.x는 Python 3.9~3.11만
지원하므로 서비스용 Python 3.12 환경과 분리된 Python 3.11 가상환경을 사용합니다.

```powershell
py -3.11 -m venv .venv-pycaret
.\.venv-pycaret\Scripts\python.exe -m pip install -r requirements-pycaret.txt
.\.venv-pycaret\Scripts\python.exe .\tools\compare_models_pycaret.py
```

결과는 `data/model_comparison/pycaret_leaderboard.xlsx`와 CSV에 저장되고,
최적 후보는 `pycaret_best_health_regressor.pkl`로 별도 저장됩니다. 검증 전에는
운영 파일인 `models/cylinder_model.pkl`을 덮어쓰지 않습니다.

더미 라벨은 아래처럼 전송할 수 있지만 최종 모델 선정 근거로 사용하면 안 됩니다.

```powershell
python .\tools\generate_dummy_data.py --condition normal --count 10 --device-id label-normal --health-score-target 90
python .\tools\generate_dummy_data.py --condition seal_leak --count 10 --device-id label-leak --health-score-target 55
```

## Supabase

Supabase SQL Editor에서 `supabase_schema.sql`을 실행합니다. 원시 센서 배열은 Supabase에 보내지 않고 SQLite에만 보관합니다. Pi에는 가능하면 제한된 서버 전용 키를 사용하고 `.env` 권한을 `chmod 600 .env`로 제한하십시오.

## CSV와 Excel 내보내기

실시간 추론 결과는 먼저 `data/export/inference_results.xlsx`의
`InferenceResults` 시트에 기록됩니다. 서비스는 기록된 추론값을 다시 읽어
SQLite `ml_results` 테이블에 저장하고, Excel의 `db_status`를 `saved`로
변경합니다. DB 저장에 실패하면 `failed`와 오류 내용이 남습니다.

`INFERENCE_EXCEL_PATH` 환경 변수로 이 Excel 파일의 위치를 변경할 수 있습니다.

전체 DB 보고서(`smart_cylinder_report.xlsx`)는 사용자가 아래 명령을 실행할 때만 생성합니다.

```bash
python tools/export_data.py
python tools/export_data.py --date 2026-08-04
python tools/export_data.py --date 2026-08-04 --raw-limit 500000
```

CSV 네 개와 `smart_cylinder_report.xlsx`의 Measurements, RawData, Features, MLResults 시트가 생성됩니다. Excel 한 시트 한도는 1,048,576행이므로 원시값이 많으면 날짜나 `--raw-limit`을 사용하십시오.

## 테스트

```bash
python -m pytest -q
python -m compileall -q config src tools main.py
```

## 10개 단위 자동 학습

서비스 실행 파일인 `raspberry_pi5_sensor_model.py`는 `src/algorithms/SensorPolynomialRegressor`를 사용합니다. Random Forest는 자동 학습 경로에서 사용하지 않습니다. Pico W는 점검이나 실험으로 정답을 알고 있는 패킷에만 다음 필드를 추가합니다.

```json
"health_score_target": 87.5
```

값은 0~100 범위이며 선택 사항입니다. 이 필드가 없는 패킷도 계속 수신·저장·추론하지만 학습 데이터에는 포함하지 않습니다. 정답이 있는 신규 데이터가 `AUTO_TRAIN_BATCH_SIZE`(기본 10)개 쌓이면 별도 스레드에서 전체 정답 데이터를 다시 학습합니다. 학습 중에도 MQTT 수집과 기존 모델 추론은 계속되며, 학습 성공 후 `models/cylinder_model.pkl`을 원자적으로 교체합니다. 학습 실패 시에는 기존 모델을 그대로 사용합니다.

터미널에서 `python smart_cylinder_ai.py`를 실행할 때도 Excel에 신뢰할 수 있는 라벨 데이터가 10개 이상 있어야 합니다. 10개 미만이면 현재 개수와 부족한 개수를 출력하고 모델 실행 전에 종료합니다.

## PyCaret 3단계 후보 모델 검증

현재 모델은 최종 운영 모델이 아니라 검증 중인 후보입니다. 진동 상태 모델, 소리
상태 모델, 통합 잔여수명 모델을 다음 실행 파일에서 순서대로 호출합니다.

```text
진동 FFT 특징 11개 → 진동 상태·신뢰도
소리 FFT 특징 11개 → 소리 상태·신뢰도
두 상태·신뢰도 → 예상 잔여수명
```

후보 모델은 `models/pycaret_final`에 있으며, 통합 실행 파일은
`run_three_pycaret_models.py`입니다. JSON 한 건 또는 1초 입력 데이터가 들어 있는
Excel 파일을 사용할 수 있습니다.

```powershell
# 예제 JSON 한 건 추론
python run_three_pycaret_models.py

# 지정한 JSON 한 건 추론
python run_three_pycaret_models.py --input inputs\below_average_model_input.json

# Excel의 모든 1초 데이터 일괄 추론
python run_three_pycaret_models.py `
  --input outputs\pi5_inference\below_average_model_input.xlsx
```

Excel 입력에는 `1초 입력데이터` 시트가 있어야 합니다. 한 행은 1초분 데이터이며,
`초 번호`, `진동_` 접두사의 특징 11개, `소리_` 접두사의 특징 11개로 구성됩니다.
실행 결과는 새 파일을 만들지 않고 동일한 Excel의 `추론결과` 시트에 기록됩니다.

평균보다 20% 낮게 조정한 3,234개 시험 행을 일괄 추론한 현재 결과는 다음과
같습니다.

- 진동: 정상 2,915건, 실링 누설 269건, 내부 마모 50건
- 소리: 정상 2,055건, 실링 누설 555건, 내부 마모 624건
- 통합: 정상 1,970건, 실링 누설 615건, 내부 마모 649건
- 예상 잔여수명: 정상 90%, 실링 누설 60%, 내부 마모 45%
- 평균 예상 잔여수명: 75.26%

잔여수명 모델은 기존 분류기의 신뢰도 값 0.55·0.60·0.65로만 학습됐기 때문에,
PyCaret 분류기가 출력하는 0.9~1.0 확률을 그대로 전달하면 학습 범위를 벗어나
54.6% 부근에 결과가 집중됐습니다. 화면과 Excel에는 PyCaret의 실제 신뢰도를
그대로 표시하되, 잔여수명 모델에는 상태별 학습 기준값을 전달하도록 수정했습니다.
종합 상태는 잔여수명 구간에서 고장명을 임의 생성하지 않고 두 센서 중 더 위험한
실제 판정을 사용합니다. 사용자가 명시적으로 승인하기 전에는 이 후보를 최종 운영
모델로 확정하거나 Raspberry Pi 서비스에 적용하지 않습니다.

### Raspberry Pi 모델 추론 전용 설치

Pi에는 학습·비교 라이브러리 전체를 설치하지 않고 모델 추론 전용 가상환경만
설치합니다. 프로젝트와 선택된 모델 3개를 `/opt/smart-cylinder-pi5`에 복사한 뒤
다음을 실행합니다.

```bash
cd /opt/smart-cylinder-pi5
sudo bash deploy/install-model-runtime.sh
```

설치되는 주요 패키지는 NumPy, SciPy, Pandas, scikit-learn, imbalanced-learn,
joblib, category-encoders, openpyxl과 저장된 파이프라인을 불러오기 위한 PyCaret
모듈입니다.
PyCaret은 `--no-deps`로 설치하므로 LightGBM, XGBoost, CatBoost, Jupyter 및 모델
비교용 추가 패키지는 설치하지 않습니다. 기존 수집·웹 서비스의 `venv`와 분리된
`venv-model`을 사용합니다.

```bash
./venv-model/bin/python run_three_pycaret_models.py --input 입력파일.xlsx
```

## SQLite 테이블

- `measurements`: 측정 메타데이터와 sequence
- `raw_sensor_data`: sample index와 원시값
- `feature_data`: 통계 및 FFT 특징
- `ml_results`: 예측, 신뢰도, 건강 점수, 모델 버전
- `upload_queue`: Supabase 실패 payload와 재시도 상태

DB는 `data/smart_cylinder.db`에 자동 생성되며 기존 행을 삭제하지 않습니다. 파일 백업은 서비스를 잠시 중지한 뒤 SQLite backup API 또는 `sqlite3 data/smart_cylinder.db '.backup data/backup/...'`를 사용하십시오.

## 자주 발생하는 오류

- `Connection refused`: `systemctl status mosquitto`와 `.env`의 호스트/포트를 확인합니다.
- NumPy/SciPy 설치 실패: 64비트 OS인지 `uname -m`으로 확인하고 pip를 갱신합니다.
- Supabase 실패: 로컬 처리는 유지됩니다. 네트워크·URL·키·테이블을 고치면 큐가 자동 재시도됩니다.
- 중복 패킷: QoS 1의 정상적인 재전송일 수 있으며 DB에는 한 번만 저장됩니다.
- sequence 누락: Pico W 재부팅 여부, Wi-Fi 품질과 발행 코드를 확인합니다.
- DB 잠김: 다른 프로그램이 DB를 장시간 쓰고 있지 않은지 확인합니다. 이 서비스는 WAL과 30초 timeout을 사용합니다.
