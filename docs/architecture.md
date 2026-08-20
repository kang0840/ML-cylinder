# Architecture Guide

## 데이터 파이프라인
SPH0645/INMP441 (Pico W) → Wi-Fi MQTT (QoS 1) → Raspberry Pi 5
  → JSON 검증 → SQLite 원시값 저장 및 ingest_queue 즉시 저장
  → FFT / 특징 추출 → ML 추론 → SQLite 저장 및 Excel 기록
  → 별도 백그라운드 작업에서 Supabase 요약 업로드 (실패 시 upload_queue 재시도)

## 핵심 컴포넌트 역할
- `main.py`: 서비스 진입점
- `src/mqtt_receiver.py`: MQTT Subscriber (non-blocking, 영구 저장 큐 활용)
- `src/database.py`: SQLite WAL 모드 트랜잭션 및 큐 관리
- `src/fft_processor.py` & `feature_extractor.py`: DC 제거, Hann window, rFFT, 특징량 11개 추출
- `src/ml_predictor.py`: joblib 기반 고장 진단 및 임계값 fallback
- `src/supabase_uploader.py`: 네트워크지연 차단을 위한 비동기 업로드 및 재시도 큐

## SQLite 데이터베이스 구조 (`data/smart_cylinder.db`)
- `measurements`: 측정 메타데이터 및 Sequence
- `raw_sensor_data`: 원시 샘플 배열
- `feature_data`: 시간/주파수 특징량
- `ml_results`: 예측, 신뢰도, 건강 점수
- `upload_queue`: Supabase 실패 패킷 재시도 큐