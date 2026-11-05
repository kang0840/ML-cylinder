#!/bin/sh
# Read-only DB analysis; independent of collection, YOLO and DB STFT worker.
set -u
result=0
program=/opt/smart-cylinder-pi5/deploy/demo_condition_analysis.py
if [ "$#" -eq 0 ]; then set -- --watch; fi
if [ "${1:-}" = "--historical" ]; then
    shift
    program=/opt/smart-cylinder-pi5/deploy/lifetime_condition_simulation.py
fi
/opt/smart-cylinder-pi5/venv/bin/python -I "$program" "$@" || result=$?
if [ "$result" -eq 0 ]; then
    printf '\n분석 종료. 시현 분석은 실행 중에 새 DB 소리를 갱신합니다. 각 Pico 상태와 자료 시각을 확인하세요. http://192.168.1.1:8000/lifetime-condition.html 을 새로고침하세요.\n'
else
    printf '\n분석 실패. 이전 웹 자료를 현재 결과로 해석하지 마세요. 종료 코드: %s\n' "$result"
fi
if [ -t 0 ]; then
    printf '창을 닫으려면 Enter를 누르세요. '
    read -r reply
fi
exit "$result"
