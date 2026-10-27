#!/usr/bin/env bash
# Install or update the bot on an Ubuntu server. Run as the default user with sudo:
#     sudo bash /tmp/setup.sh /tmp/cryptobot.tar.gz
# Safe to run again: code is replaced, but state/, logs/, data/, .env and STOP are kept.
set -euo pipefail

ARCHIVE="${1:?usage: setup.sh <cryptobot.tar.gz>}"
APP=/opt/cryptobot

echo "== System packages"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -yq python3-venv python3-pip rsync \
    fail2ban unattended-upgrades chrony
systemctl enable --now fail2ban unattended-upgrades chrony

echo "== Swap (helps a 1 GB server when the ML model retrains)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "== Bot user and code"
id cryptobot >/dev/null 2>&1 || useradd --system --create-home --home-dir "$APP" --shell /usr/sbin/nologin cryptobot
STAGE=$(mktemp -d)
tar -xzf "$ARCHIVE" -C "$STAGE"
rsync -a --delete \
    --exclude state/ --exclude logs/ --exclude data/ --exclude reports/ \
    --exclude .venv/ --exclude .env --exclude STOP \
    "$STAGE"/ "$APP"/
rm -rf "$STAGE"

if [ -f /tmp/.env ]; then
    mv /tmp/.env "$APP/.env"
    echo "   .env installed"
fi
[ -f "$APP/.env" ] || echo "   WARNING: no .env yet - email alerts will only be logged"

chown -R cryptobot:cryptobot "$APP"
[ -f "$APP/.env" ] && chmod 600 "$APP/.env"

echo "== Python environment"
sudo -u cryptobot python3 -m venv "$APP/.venv"
sudo -u cryptobot "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u cryptobot "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
sudo -u cryptobot "$APP/.venv/bin/pip" install -q -e "$APP"

echo "== Tests"
(cd "$APP" && sudo -u cryptobot .venv/bin/python -m pytest -q)

echo "== Service"
cp "$APP/deploy/cryptobot.service" /etc/systemd/system/cryptobot.service
systemctl daemon-reload
systemctl enable cryptobot
systemctl restart cryptobot
sleep 5
systemctl --no-pager --lines=15 status cryptobot || true
echo "== Done"
