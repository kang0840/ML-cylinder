# sover

Smart Cylinder 정적 HTML 서버입니다.

## 실행

```powershell
cd C:\Users\강상훈\Downloads\sover
python server.py
```

또는 `start-server.bat`를 더블클릭하세요.

## 접속

- 제품 페이지: http://localhost:8000/
- 모니터링 페이지: http://localhost:8000/monitoring.html

## GitHub Pages 배포

1. 이 폴더를 GitHub 저장소의 `main` 브랜치에 올립니다.
2. **Settings → Pages → Build and deployment**에서 Source를 **GitHub Actions**로 선택합니다.
3. Actions의 `Deploy to GitHub Pages` 작업이 끝나면 배포 주소가 표시됩니다.

주소 형식은 `https://<GitHub아이디>.github.io/<저장소이름>/`입니다. GitHub Pages에서는 브라우저 로컬 저장소 기반 데모 모드로 작동합니다.

## 영구 시리얼 저장 서버

Render의 Python API가 Supabase PostgreSQL에 시리얼을 저장합니다. Render 환경변수 `DATABASE_URL`에는 Supabase 대시보드의 **Session pooler** 연결 문자열을 설정합니다. 비밀번호가 포함된 연결 문자열은 GitHub에 커밋하지 마세요.
