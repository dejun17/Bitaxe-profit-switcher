#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/bitaxe-switcher"
CONFIG_DIR="/etc/bitaxe-switcher"
STATE_DIR="/var/lib/bitaxe-switcher"
LOG_FILE="/var/log/bitaxe-switcher.log"
USER_NAME="bitaxe-switcher"
SERVICE_NAME="bitaxe-switcher-web.service"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

trap 'echo "Installation failed on line $LINENO." >&2' ERR

[[ ${EUID} -eq 0 ]] || { echo "Run this installer as root." >&2; exit 1; }
[[ -f "$SCRIPT_DIR/webapp.py" ]] || { echo "webapp.py not found in $SCRIPT_DIR" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/bitaxe_switcher.py" ]] || { echo "bitaxe_switcher.py not found in $SCRIPT_DIR" >&2; exit 1; }
[[ -d "$SCRIPT_DIR/templates" ]] || { echo "templates/ not found in $SCRIPT_DIR" >&2; exit 1; }
[[ -d "$SCRIPT_DIR/static" ]] || { echo "static/ not found in $SCRIPT_DIR" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git iputils-ping jq netcat-openbsd \
  python3 python3-pip python3-venv unzip wget

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$USER_NAME"
fi

install -d -m 0755 "$INSTALL_DIR"
install -d -o root -g "$USER_NAME" -m 0770 "$CONFIG_DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$STATE_DIR"

install -m 0755 "$SCRIPT_DIR/bitaxe_switcher.py" "$INSTALL_DIR/bitaxe_switcher.py"
install -m 0755 "$SCRIPT_DIR/webapp.py" "$INSTALL_DIR/webapp.py"
install -m 0644 "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"
rm -rf "$INSTALL_DIR/templates" "$INSTALL_DIR/static"
cp -a "$SCRIPT_DIR/templates" "$SCRIPT_DIR/static" "$INSTALL_DIR/"

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip wheel
"$INSTALL_DIR/venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
  install -o root -g "$USER_NAME" -m 0660 "$SCRIPT_DIR/config.example.yaml" "$CONFIG_DIR/config.yaml"
  echo "Created $CONFIG_DIR/config.yaml"
else
  echo "Keeping existing $CONFIG_DIR/config.yaml"
fi

if [[ ! -f "$CONFIG_DIR/environment" && -f "$SCRIPT_DIR/examples/environment.example" ]]; then
  install -o root -g root -m 0600 "$SCRIPT_DIR/examples/environment.example" "$CONFIG_DIR/environment"
  echo "Created $CONFIG_DIR/environment"
fi

touch "$LOG_FILE"
chown "$USER_NAME:$USER_NAME" "$LOG_FILE"
chmod 0640 "$LOG_FILE"

install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher-web.service" "/etc/systemd/system/$SERVICE_NAME"
install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher.service" /etc/systemd/system/bitaxe-switcher.service
install -m 0644 "$SCRIPT_DIR/systemd/bitaxe-switcher.timer" /etc/systemd/system/bitaxe-switcher.timer

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1:8088/health >/dev/null; then
    break
  fi
  sleep 1
done

if ! curl --fail --silent --show-error http://127.0.0.1:8088/health >/dev/null; then
  echo "The web service started but the health check failed." >&2
  systemctl status "$SERVICE_NAME" --no-pager || true
  journalctl -u "$SERVICE_NAME" -n 100 --no-pager || true
  exit 1
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

Bitaxe Profit Switcher installed successfully.

Web UI:  http://${IP:-SERVER-IP}:8088
Config:  $CONFIG_DIR/config.yaml
Secrets: $CONFIG_DIR/environment
Logs:    $LOG_FILE

Useful commands:
  systemctl status $SERVICE_NAME --no-pager
  journalctl -u $SERVICE_NAME -f
  curl http://127.0.0.1:8088/health

Dry-run mode is enabled by default. Configure and test the application before enabling live switching.
EOF
