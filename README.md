# Raspberry Pi 5 스마트 실린더 수집·분석 시스템

Raspberry Pi Pico W가 MQTT로 전송하는 SPH0645/INMP441 원시 샘플을 Raspberry Pi 5에서 수신하고, 검증·SQLite 저장·FFT·특징 추출·머신러닝 추론·Supabase 요약 업로드까지 수행하는 오프라인 우선 서비스입니다.

## Raspberry Pi 5의 역할

Pi 5는 Mosquitto 브로커와 Python subscriber를 실행하는 중앙 처리 장치입니다. Pico W는 센서값 발행만 담당합니다. 인터넷이나 Supabase가 중단돼도 MQTT 수신, 로컬 저장, FFT 및 추론은 계속되며 업로드 실패 데이터는 `upload_queue`에 남아 자동 재시도됩니다.

```text
SPH0645 / INMP441 → Pico W → Wi-Fi MQTT(QoS 1) → Raspberry Pi 5
  → JSON 검증 → SQLite 원시값 저장 → FFT/특징 → ML 추론
  → SQLite 결과 저장 → Supabase 요약 업로드(실패 시 재시도 큐)
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

## Supabase

Supabase SQL Editor에서 `supabase_schema.sql`을 실행합니다. 원시 센서 배열은 Supabase에 보내지 않고 SQLite에만 보관합니다. Pi에는 가능하면 제한된 서버 전용 키를 사용하고 `.env` 권한을 `chmod 600 .env`로 제한하십시오.

## CSV와 Excel 내보내기

Excel에 실시간으로 쓰지 않습니다. 사용자가 명령을 실행할 때만 `data/export`에 생성합니다.

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

## 50개 단위 자동 학습

서비스 실행 파일인 `raspberry_pi5_sensor_model.py`는 `src/algorithms/SensorPolynomialRegressor`를 사용합니다. Random Forest는 자동 학습 경로에서 사용하지 않습니다. Pico W는 점검이나 실험으로 정답을 알고 있는 패킷에만 다음 필드를 추가합니다.

```json
"health_score_target": 87.5
```

값은 0~100 범위이며 선택 사항입니다. 이 필드가 없는 패킷도 계속 수신·저장·추론하지만 학습 데이터에는 포함하지 않습니다. 정답이 있는 신규 데이터가 `AUTO_TRAIN_BATCH_SIZE`(기본 50)개 쌓이면 별도 스레드에서 전체 정답 데이터를 다시 학습합니다. 학습 중에도 MQTT 수집과 기존 모델 추론은 계속되며, 학습 성공 후 `models/cylinder_model.pkl`을 원자적으로 교체합니다. 학습 실패 시에는 기존 모델을 그대로 사용합니다.

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
