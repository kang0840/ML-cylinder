#!/bin/sh
# Read-only DB analysis; independent of collection, YOLO and DB STFT worker.
set -u
result=0
if [ "$#" -eq 0 ]; then set -- --all-picos; fi
/opt/smart-cylinder-pi5/venv/bin/python -I /opt/smart-cylinder-pi5/deploy/lifetime_condition_simulation.py "$@" || result=$?
if [ "$result" -eq 0 ]; then
    printf '\nPico별 분석 갱신 완료. 자료 부족 여부는 각 Pico에서 확인하세요. http://192.168.1.1:8000/lifetime-condition.html 을 새로고침하세요.\n'
else
    printf '\n분석 실패. 이전 웹 자료를 현재 결과로 해석하지 마세요. 종료 코드: %s\n' "$result"
fi
if [ -t 0 ]; then
    printf '창을 닫으려면 Enter를 누르세요. '
    read -r reply
fi
exit "$result"
