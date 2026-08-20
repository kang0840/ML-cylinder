#!/usr/bin/env bash

set -u

clear
printf '스마트 실린더 실시간 상태\n'
printf '=========================\n\n'
printf 'Raspberry Pi IP: %s\n' "$(hostname -I 2>/dev/null || true)"
printf 'Mosquitto:       %s\n' "$(systemctl is-active mosquitto 2>/dev/null || true)"
printf '추론 서비스:     %s\n' "$(systemctl is-active smart-cylinder 2>/dev/null || true)"
printf '\n아래 로그는 자동 갱신됩니다. 종료: Ctrl+C\n'
printf '%s\n\n' '------------------------------------------------------------'

journalctl -u smart-cylinder -n 30 -f -l --no-pager
