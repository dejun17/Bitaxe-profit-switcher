#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR=/opt/bitaxe-switcher
CONFIG_DIR=/etc/bitaxe-switcher
STATE_DIR=/var/lib/bitaxe-switcher
USER_NAME=bitaxe-switcher
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$STATE_DIR"
install -m 0755 "$SCRIPT_DIR/bitaxe_switcher.py" "$INSTALL_DIR/bitaxe_switcher.py"
install -m 0755 "$SCRIPT_DIR/webapp.py" "$INSTALL_DIR/webapp.py"
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
rm -rf "$INSTALL_DIR/templates" "$INSTALL_DIR/static"
cp -a "$SCRIPT_DIR/templates" "$SCRIPT_DIR/static" "$INSTALL_DIR/"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
[[ -f "$CONFIG_DIR/config.yaml" ]] || install -m 0640 "$SCRIPT_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
touch /var/log/bitaxe-switcher.log
chown -R "$USER_NAME:$USER_NAME" "$STATE_DIR" /var/log/bitaxe-switcher.log
chown -R root:"$USER_NAME" "$CONFIG_DIR"
chmod 0770 "$CONFIG_DIR"
chmod 0660 "$CONFIG_DIR/config.yaml"
install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher-web.service" /etc/systemd/system/bitaxe-switcher-web.service
systemctl daemon-reload
systemctl enable --now bitaxe-switcher-web.service
IP=$(hostname -I | awk '{print $1}')
echo
echo "Web UI installed: http://${IP:-SERVER-IP}:8088"
echo "Service: systemctl status bitaxe-switcher-web"
echo "Logs: journalctl -u bitaxe-switcher-web -f"
