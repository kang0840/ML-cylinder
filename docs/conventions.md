# Conventions Guide

## 수신 MQTT JSON 데이터 규격
```json
{
  "device_id": "pico01",
  "sensor_type": "sph0645",
  "sample_rate": 1600,
  "cylinder_state": "forward",
  "sequence": 1,
  "timestamp": 1785830000,
  "samples": [125, 128, 131, 129],
  "health_score_target": 87.5
}