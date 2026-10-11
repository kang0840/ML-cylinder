# 판별 성능 평가

정답과 이미 실행한 판별 결과를 비교하는 오프라인 평가 기능이다. 학습·실시간 추론·웹 배포·DB 변경을 수행하지 않는다.

입력 JSON 예:

```json
{
  "dataset_description": "실제 독립 실험의 조건과 정답 확인 방법",
  "training_session_ids": ["train-session"],
  "rows": [
    {"sample_id": "cycle-1", "session_id": "test-session", "ground_truth": "NORMAL", "prediction": "NORMAL", "inference_status": "OK"},
    {"sample_id": "cycle-2", "session_id": "test-session", "ground_truth": "SEAL_LEAK", "prediction": null, "inference_status": "ERROR"}
  ]
}
```

실행(프로젝트 최상위 PowerShell):

```powershell
python ML-cylinder/tools/evaluate_condition_predictions.py --input 기존입력.json --output 새평가결과.json
```

기존 출력은 덮어쓰지 않는다. 폴더를 생성하지 않는다. 실제 정답은 실험으로 별도 확인하고 예측이나 자동 규칙 라벨을 정답으로 대체하지 않는다. sample_id는 분석 단위(Cycle 또는 명시한 패킷), session_id는 수집 세션이다. 같은 세션/샘플 중복은 오류다. 입력의 분석 단위를 혼합하지 않는다.

NORMAL은 정상, ABNORMAL/SEAL_LEAK/PRESSURE_DROP/PRESSURE_LOW/INTERNAL_WEAR는 이상으로 평가한다. 알 수 없는 정답은 제외 건수, 알 수 없는 예측은 잘못된 예측 건수로 보고한다. 추론 상태는 OK/ERROR/ABSTAIN이다.

- 정확도: (TP+TN)/유효 예측 수
- 이상 검출률: TP/(TP+FN)
- 오경보율: FP/(FP+TN)
- Precision와 F1도 제공
- 예측 커버리지: 유효 예측 수/정답이 있는 전체 수
- 정답 보유 전체 대비 올바른 판정 비율도 제공하여 실패·보류 누락 영향 확인

분모 0은 null이다. 비율 단위는 0~1이며 화면에 백분율로 표시하려면 100을 곱한다. 모두 틀린 유효 예측은 정확도 0, 추론이 모두 실패한 경우 정확도 null과 NO_EVALUABLE_PREDICTIONS로 구분한다.

training_session_ids를 모르면 생략한다. 빈 배열은 학습 세션이 없다고 확인한 경우에만 사용한다. 학습 세션 중복 여부를 경고하며, 세션이 분리됐더라도 정답 출처·개체 독립·일반화가 자동 검증된 것은 아니므로 결과는 EXPLORATORY로 표시한다.

outputs/condition_evaluation_example.json과 대응 report는 합성 소프트웨어 검증 예시다. 실제 실린더 성능으로 제출하지 않는다. 관련 테스트 4개가 통과했다. 실제 데이터 연결·모델 추론·웹 평가 표시 연결·배포는 후속 작업이다. README는 변경하지 않았다.
