# Installation

## Recommended Proxmox LXC

- Debian 12
- 2 vCPU
- 1 GB RAM
- 8 GB disk
- Unprivileged container
- Bridged LAN access to the AxeOS miners

## Fresh installation

Run as `root` inside the LXC:

```bash
apt update
apt install -y git
git clone --branch main https://github.com/dejun17/Bitaxe-profit-switcher.git /opt/Bitaxe-profit-switcher
cd /opt/Bitaxe-profit-switcher
chmod +x install-web.sh
./install-web.sh
```

Open `http://CONTAINER-IP:8088`.

## Verify

```bash
systemctl status bitaxe-switcher-web.service --no-pager
curl -i http://127.0.0.1:8088/health
journalctl -u bitaxe-switcher-web.service -n 100 --no-pager
```

## Configuration paths

- Application: `/opt/bitaxe-switcher`
- Configuration: `/etc/bitaxe-switcher/config.yaml`
- Optional secrets: `/etc/bitaxe-switcher/environment`
- State: `/var/lib/bitaxe-switcher/state.json`
- Log: `/var/log/bitaxe-switcher.log`

The installer preserves an existing configuration during upgrades.
