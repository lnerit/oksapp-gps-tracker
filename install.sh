#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo 'Run this as your normal Raspberry Pi user, NOT with sudo.' >&2
  exit 1
fi

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PI_USER="$(id -un)"
cd "$PROJECT_DIR"

NEW_CONFIG=0
if [[ ! -f oksapp.env ]]; then
  cp oksapp.env.example oksapp.env
  NEW_CONFIG=1
  echo 'Created oksapp.env. Review DEVICE_ID, GPS_PORT, GPS_BAUD and API credentials.'
fi
chmod 600 oksapp.env

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

# Allows the service to access the USB GPS serial port.
sudo usermod -aG dialout "$PI_USER"

SERVICE_PATH=/etc/systemd/system/oksapp-gps.service
sudo tee "$SERVICE_PATH" >/dev/null <<EOF
[Unit]
Description=OKSApp GPS receiver and coordinate uploader
After=network.target

[Service]
Type=simple
User=$PI_USER
SupplementaryGroups=dialout
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/oksapp.env
ExecStart=$PROJECT_DIR/.venv/bin/python $PROJECT_DIR/gps_sender.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable oksapp-gps.service
if [[ "$NEW_CONFIG" -eq 0 ]]; then
  sudo systemctl restart oksapp-gps.service
else
  echo 'Service ENABLED but not started until you review the new configuration.'
fi

echo
printf 'Installed OKSApp GPS service for user %s\n' "$PI_USER"
echo "Configuration: $PROJECT_DIR/oksapp.env"
echo 'Edit the configuration if needed, then: sudo systemctl restart oksapp-gps'
echo 'Check service: sudo systemctl status oksapp-gps'
echo 'Live logs:     sudo journalctl -u oksapp-gps -f'
echo 'Manual serial tests may require logging out/in after dialout group change.'
