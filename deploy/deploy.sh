#!/bin/bash
# Copy the dashboard to the Pi and restart it.
#
#   deploy/deploy.sh             sync code, update the venv, restart the service
#   deploy/deploy.sh --install   also install the systemd unit and console
#                                helper (first time only; needs `sudo -v` on
#                                the Pi beforehand)
set -euo pipefail

HOST=${PI_SCREEN_HOST:-octopi@octopi.local}
DEST=pi-screen
cd "$(dirname "$0")/.."

rsync -a --delete \
	--exclude .venv --exclude .git --exclude .superpowers --exclude __pycache__ \
	--exclude docs \
	./ "$HOST:$DEST/"

ssh "$HOST" "set -e
	cd $DEST
	if [ ! -x .venv/bin/python ]; then
		python3 -m venv --system-site-packages .venv
	fi
	.venv/bin/pip install --quiet websocket-client==1.8.0"

if [ "${1:-}" = "--install" ]; then
	ssh "$HOST" "set -e
		cd $DEST
		sudo -n install -m 0755 -o root -g root deploy/pi-screen-console /usr/local/sbin/pi-screen-console
		sudo -n install -m 0644 -o root -g root deploy/pi-screen.service /etc/systemd/system/pi-screen.service
		sudo -n systemctl daemon-reload
		sudo -n systemctl enable --now pi-screen"
else
	ssh "$HOST" "systemctl is-enabled --quiet pi-screen 2>/dev/null && sudo -n systemctl restart pi-screen || true"
fi

ssh "$HOST" "systemctl --no-pager --lines=5 status pi-screen || true"
