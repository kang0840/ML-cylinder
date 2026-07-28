# Smart Cylinder Monitoring System

진동·소음 데이터를 기반으로 공압 실린더의 상태를 분석하고, 이상 징후를 조기에 확인할 수 있도록 지원하는 AI 기반 예지보전 학생 프로젝트입니다.

**GitHub 저장소:** [https://github.com/kang0840/ML-cylinder](https://github.com/kang0840/ML-cylinder)

## 프로젝트 소개

Smart Cylinder Monitoring System은 ADXL345 가속도 센서와 INMP441 마이크에서 수집되는 데이터를 분석하여 공압 실린더의 상태를 A~D 등급으로 분류하는 시스템입니다. 현재 저장소에서는 실제 센서 연결 전에도 전체 분석 과정을 검증할 수 있도록 센서 시뮬레이터를 제공합니다.

주요 기능은 다음과 같습니다.

- 진동 및 소음 데이터 생성·수집
- FFT 기반 진동 신호 전처리 및 특징 추출
- Random Forest 모델을 이용한 실린더 상태 분류
- 정상 기준 대비 통계적 상태 구간 판정
- 건강 점수와 진동 RMS 변화 추세 제공
- 분석 결과의 JSON 및 Excel 저장
- 시리얼 번호 기반 웹 모니터링과 관리자 기능 제공

## 문제 정의

공압 실린더의 상태를 작업자가 소리, 진동, 동작 감각만으로 판단하면 이상을 정량적으로 비교하기 어렵고 점검 시점도 작업자의 경험에 의존하게 됩니다. 정기 점검만으로는 점검 주기 사이에 발생하는 고장을 놓칠 수 있으며, 불필요한 부품 교체와 예기치 않은 설비 중단이 발생할 수 있습니다.

이 프로젝트는 진동과 소음 데이터를 지속적으로 수집하고 AI로 상태를 분류하여 다음 문제를 해결하고자 합니다.

- 육안 및 경험 중심 점검의 주관성 완화
- 초기 이상 징후의 정량적 탐지
- 고장 발생 후 수리 방식에서 상태 기반 유지보수로 전환
- 설비 상태와 분석 이력의 체계적인 관리

## 아키텍처

전체 동작 과정은 다음과 같습니다.

1. ADXL345에서 가속도 데이터를, INMP441에서 소음 PCM 데이터를 수집합니다.
2. 진동 신호의 DC 성분을 제거하고 2~1,000 Hz 대역을 기준으로 FFT를 수행합니다.
3. 진동 속도 RMS, 최대 진동 속도, 파고율, 주요 주파수와 소음 RMS를 특징으로 추출합니다.
4. Excel 학습 데이터로 Random Forest 분류 모델을 학습합니다.
5. 모델이 입력 특징을 분석하여 실린더 상태를 A, B, C, D 구간으로 분류하고 신뢰도를 계산합니다.
6. 정상 데이터 기준의 통계적 구간, 건강 점수 및 진동 RMS 추세를 함께 산출합니다.
7. 분석 결과를 JSON과 Excel에 저장하고 웹 대시보드에서 설비 상태를 확인합니다.

**데이터 흐름:** ADXL345·INMP441 → 신호 전처리·FFT → 특징 추출 → Random Forest → 상태 등급·건강 점수·추세 분석 → JSON·Excel·웹 대시보드

> 상태 구간과 건강 점수는 프로젝트 기준이며, 실제 설비에 적용하려면 대상 실린더에서 수집한 정상·이상·고장 데이터로 별도 검증해야 합니다. 현재 잔여 수명은 수명주기 데이터 부족으로 확정값을 제공하지 않고 실험적 임계 구간 도달 추세만 산출합니다.

## 사용 기술 스택

| 분야 | 기술 |
|------|------|
| Language | Python 3, JavaScript, HTML, CSS |
| AI / Machine Learning | Scikit-learn, Random Forest Classifier |
| Data Processing | NumPy, FFT, 통계적 Z-score 분석 |
| Data Storage | Excel, JSON, PostgreSQL |
| Backend | Flask, Gunicorn, psycopg |
| Frontend | Vanilla JavaScript 기반 웹 대시보드 |
| Hardware / Sensor | Raspberry Pi 연동 대상, ADXL345, INMP441 |
| Test | pytest |
| Deployment | Render |

## 실행 방법

1. Python 3.12 권장 환경을 준비합니다.
2. 저장소를 내려받은 뒤 프로젝트 루트 디렉터리로 이동합니다.
3. 루트의 `requirements.txt`에 정의된 패키지를 설치합니다.
4. 웹 서비스를 사용할 경우 `ADMIN_PASSWORD`를 설정합니다. PostgreSQL을 사용할 때는 `DATABASE_URL`을 추가하고, 허용할 프런트엔드 주소가 있다면 `ALLOWED_ORIGIN`을 설정합니다.
5. Windows에서는 `start-server.bat`을 실행하거나 `server.py`를 직접 실행합니다.
6. 브라우저에서 `http://127.0.0.1:8000`에 접속합니다.
7. AI 분석을 실행하려면 프로젝트 루트의 `smart_cylinder_ai.py`를 실행합니다.
8. 분석이 완료되면 `smart_cylinder_result.json`과 `smart_cylinder_training.xlsx`에서 결과 및 누적 데이터를 확인합니다.
9. 기능 검증이 필요한 경우 `tests` 디렉터리의 테스트를 pytest로 실행합니다.

## AI 사용 내역

| 항목 | 내용 |
|------|------|
| AI를 사용하는 이유 | 여러 진동·소음 특징의 관계를 함께 분석하여 단일 임계값 방식보다 설비 상태를 종합적으로 판별하기 위해 사용합니다. |
| 머신러닝 모델 | 200개 결정 트리로 구성된 `RandomForestClassifier`를 사용하며, 클래스 불균형 보정과 OOB 검증을 적용합니다. |
| 입력 데이터 | 진동 속도 RMS, 최대 진동 속도, 파고율, 주요 주파수, 소음 RMS를 사용합니다. 운전 시간은 추세 관리에는 활용하지만 모델 입력에서는 제외합니다. |
| 출력 데이터 | A~D 상태 등급, 상태 설명, 예측 신뢰도, 건강 점수, 통계적 상태 구간과 진동 RMS 추세를 출력합니다. |
| 학습 과정 | Excel의 신뢰 가능한 라벨 데이터를 불러와 유효성을 검사하고 Random Forest 모델을 학습합니다. 자동 생성된 통계 라벨은 재학습 데이터에서 제외하여 자기강화 오류를 방지합니다. |
| 추론 과정 | 센서 신호를 전처리한 뒤 특징 벡터로 변환하고, 학습된 모델의 클래스와 확률을 계산하여 상태 등급과 신뢰도를 결정합니다. |
| AI 적용 효과 | 이상 상태를 일관된 기준으로 분류하고, 점검 우선순위 설정과 상태 기반 유지보수를 지원합니다. |

현재 포함된 학습 데이터와 센서 입력은 데모·시뮬레이션 성격을 가집니다. 실제 현장 정확도는 실설비에서 취득한 정답 라벨 데이터로 재학습하고 별도의 테스트 세트로 평가해야 합니다.

## 프로젝트 구조

~~~text
ML-cylinder-main/
├── public/                         # 사용자·관리자 웹 화면 및 스크립트
├── src/
│   ├── algorithms/                 # KNN, Naive Bayes, 회귀 알고리즘
│   ├── excel_dataset.py            # Excel 데이터 관리 및 모델 학습
│   ├── health_assessment.py        # 상태 구간, 건강 점수, 추세 분석
│   └── vibration_features.py       # FFT 기반 진동 특징 추출
├── tests/                          # 특징 추출·상태 평가·데이터셋 테스트
├── adxl345_simulator.py            # 가속도 센서 시뮬레이터
├── inmp441_microphone.py           # 마이크 센서 시뮬레이터
├── smart_cylinder_ai.py            # AI 분석 실행 파일
├── smart_cylinder_training.xlsx    # 학습 및 측정 데이터
├── smart_cylinder_result.json      # 최신 AI 분석 결과
├── server.py                       # Flask API 및 정적 웹 서버
├── requirements.txt                # 웹 서버 및 AI 공통 의존성
├── render.yaml                     # Render 배포 설정
├── start-server.bat                # Windows 서버 실행 파일
└── README.md
~~~

## 기대 효과

- **유지보수 비용 절감:** 설비 상태에 따라 점검과 교체 시점을 결정하여 불필요한 정비를 줄일 수 있습니다.
- **설비 고장 예방:** 진동과 소음의 이상 변화를 조기에 감지하여 돌발 고장 가능성을 낮출 수 있습니다.
- **생산성 향상:** 비계획 정지 시간을 줄이고 점검 우선순위를 효율적으로 관리할 수 있습니다.
- **기존 설비 활용:** 기존 공압 실린더에 센서와 분석 시스템을 추가하는 방식으로 스마트 모니터링 환경을 구성할 수 있습니다.

## 라이선스

이 프로젝트는 MIT License를 기준으로 배포합니다. 저작권 고지와 라이선스 문구를 포함하는 조건으로 소프트웨어를 자유롭게 사용, 복제, 수정, 병합, 배포 및 재사용할 수 있습니다.

자세한 내용은 저장소의 `LICENSE` 파일을 참고하세요.
