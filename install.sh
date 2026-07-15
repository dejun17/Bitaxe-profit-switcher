#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR=/opt/bitaxe-switcher
CONFIG_DIR=/etc/bitaxe-switcher
STATE_DIR=/var/lib/bitaxe-switcher
SERVICE_USER=bitaxe-switcher
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }

apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR"
install -m 0755 "$SCRIPT_DIR/bitaxe_switcher.py" "$INSTALL_DIR/bitaxe_switcher.py"
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
  install -m 0640 "$SCRIPT_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi

touch /var/log/bitaxe-switcher.log
chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
chown "$SERVICE_USER:$SERVICE_USER" /var/log/bitaxe-switcher.log
chown root:"$SERVICE_USER" "$CONFIG_DIR/config.yaml"
chmod 0640 "$CONFIG_DIR/config.yaml"
install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher.service" /etc/systemd/system/
install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable bitaxe-switcher.timer

cat <<'MSG'
Installed.

Next:
  nano /etc/bitaxe-switcher/config.yaml
  cp examples/environment.example /etc/bitaxe-switcher/environment
  chmod 600 /etc/bitaxe-switcher/environment

Dry-run comparison:
  sudo -u bitaxe-switcher /opt/bitaxe-switcher/venv/bin/python /opt/bitaxe-switcher/bitaxe_switcher.py --dry-run compare

Start timer:
  systemctl start bitaxe-switcher.timer

Logs:
  journalctl -u bitaxe-switcher.service -f
MSG
