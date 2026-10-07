#!/bin/sh
# Raspberry Pi desktop entry; secrets remain in the existing external .env.
set -eu
if [ "$#" -eq 0 ]; then
    set -- --interactive
fi
exec /opt/smart-cylinder-pi5/venv/bin/python -I \
    /home/mother/Desktop/pi_sensor_runtime.py "$@"
