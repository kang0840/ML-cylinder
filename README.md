# ML-cylinder

이 프로젝트는 다른 프로젝트에서도 재사용할 수 있도록 최소한의 머신러닝 구성으로 정리된 예시입니다.

## 구조
- src/algorithms: 기본 알고리즘 모듈
- src/__init__.py: 패키지 진입점

## 사용 예시
```python
from src.algorithms.sensor_regression import SensorPolynomialRegressor

model = SensorPolynomialRegressor(max_degree=3)
model.fit([
    {"vibration": [0.1, 0.2, 0.3, 0.4], "microphone": [0.2, 0.1, 0.3, 0.2]},
], [1.0])
```
