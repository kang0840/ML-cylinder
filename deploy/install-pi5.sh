#!/usr/bin/env bash
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/install-pi5.sh"
  exit 1
fi
cd /opt/smart-cylinder-pi5
apt-get update
apt-get install -y mosquitto mosquitto-clients python3-venv libopenblas0
install -m 0644 deploy/mosquitto-smart-cylinder.conf /etc/mosquitto/conf.d/smart-cylinder.conf
if [[ ! -f .env ]]; then install -m 0600 .env.example .env; fi
set -a
source .env
set +a
mosquitto_passwd -b -c /etc/mosquitto/passwd "$MQTT_USERNAME" "$MQTT_PASSWORD"
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 0640 /etc/mosquitto/passwd
systemctl enable mosquitto
systemctl restart mosquitto
python3 -m venv venv
venv/bin/python -m pip install --upgrade pip
venv/bin/python -m pip install -r requirements.txt
install -m 0644 deploy/smart-cylinder.service /etc/systemd/system/smart-cylinder.service
systemctl daemon-reload
echo "Set SUPABASE_KEY in .env, then enable smart-cylinder."
