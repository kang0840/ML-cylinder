# Technical Decisions Log

## 1. Offline-First 수신 최적화
- MQTT 콜백에서 Supabase 직접 전송을 금지하고, 로컬 SQLite (`ingest_queue`, `upload_queue`)에 우선 저장을 수행함으로 네트워크 지연/단절 시 센서 손실 방지

## 2. 가상환경 분리 전략
- PyCaret 3.x의 Python 버전 제약(3.9~3.11)으로 인해 메인 서비스 환경과 PyCaret 비교 환경을 완벽히 분리

## 3. 원시 데이터 전송 제한
- Supabase에는 요약 및 추론 결과 데이터만 전송하며, 고용량 원시 데이터 배열(`samples`)은 로컬 SQLite에만 유지

## 4. 안전한 자동 학습 프로세스
- 라벨 데이터 10개 수집 시 `SensorPolynomialRegressor` 기반 자동 재학습 수행
- 학습 중에도 수집/추론은 지속되며, 성공 시에만 `models/cylinder_model.pkl` 원자적 교체