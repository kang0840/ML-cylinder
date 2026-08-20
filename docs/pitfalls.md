### 3. `docs/pitfalls.md` (자주 발생하는 문제 및 문제 해결)

```markdown
# Pitfalls & Troubleshooting Guide

## 자주 발생하는 문제 및 해결 방안
1. **MQTT Connection refused**: `systemctl status mosquitto`로 서비스 상태 및 `.env` 포트 점검
2. **SQLite Database Locked**: WAL 모드 및 30초 Timeout 적용 유지. 장시간 프로세스 점유 방지
3. **NumPy / SciPy 설치 실패**: 64-bit OS 필수 (`uname -m` 확인) 및 pip 최신 버전 갱신
4. **PyCaret 버전 충돌**: 
   - 메인 서비스: Python 3.12 / 3.13 (`venv`)
   - PyCaret 학습/비교: Python 3.11 독립 환경 (`.venv-pycaret`)
   - Raspberry Pi 추론 전용: `deploy/install-model-runtime.sh` 이용 (`venv-model`, `--no-deps` 옵션 적용)
5. **Timestamp 불일치**: 시스템 시계 오류 시 `timedatectl status`로 NTP 동기화 확인

허용 허용범위 및 제약 조건
sensor_type: sph0645 또는 inmp441만 허용

cylinder_state: forward, backward, idle만 허용

samples: 유한한 숫자 2 ~ 100,000개 제한

health_score_target: 0 ~ 100 선택 사항 (10개 이상 누적 시 자동 학습 대상)

MQTT 토픽 규칙
smartCylinder/+/sph0645/raw

smartCylinder/+/inmp441/raw

smartCylinder/+/status

중복 체크 키: device_id + sensor_type + sequence