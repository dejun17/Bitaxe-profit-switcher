# Bitaxe Profit Switcher

A multi-miner AxeOS profitability controller with a local web dashboard. It evaluates SHA-256 pool profiles independently for each miner using hashrate, power draw, electricity cost, network conditions, rewards, and fees.

> This project optimizes expected mining revenue. It does not guarantee profit or predict cryptocurrency prices.

## Features

- Multiple AxeOS miners
- Per-miner TH/s, wattage, and electricity rates
- Optional live AxeOS metrics
- BTC, BCH, DGB, and custom SHA-256 profiles
- Pool and conversion fees
- Dry-run mode and manual simulation
- Confirmation checks and minimum-runtime safeguards
- Manual or automatic pool switching
- Web configuration editor, status API, health endpoint, and logs
- systemd installation for Debian/Ubuntu

## Quick install on a fresh Debian 12 Proxmox LXC

```bash
apt update
apt install -y git
git clone --branch main https://github.com/dejun17/Bitaxe-profit-switcher.git /opt/Bitaxe-profit-switcher
cd /opt/Bitaxe-profit-switcher
chmod +x install-web.sh
./install-web.sh
```

Open:

```text
http://CONTAINER-IP:8088
```

The example configuration starts in **dry-run mode** and uses a static BTC network-hashrate estimate. This avoids requiring a Bitcoin Core node just to load the application.

## Verify the installation

```bash
systemctl status bitaxe-switcher-web.service --no-pager
curl -i http://127.0.0.1:8088/health
journalctl -u bitaxe-switcher-web.service -f
```

## Important paths

| Purpose | Path |
|---|---|
| Installed application | `/opt/bitaxe-switcher` |
| Configuration | `/etc/bitaxe-switcher/config.yaml` |
| Optional secrets | `/etc/bitaxe-switcher/environment` |
| State | `/var/lib/bitaxe-switcher/state.json` |
| Controller log | `/var/log/bitaxe-switcher.log` |

## Safe first-run workflow

1. Leave `general.dry_run: true`.
2. Add or verify every miner.
3. Confirm AxeOS is reachable from the controller LXC.
4. Replace all `CHANGE_ME` pool credentials.
5. Add only verified pool endpoints.
6. Run comparisons and simulated switches.
7. Review at least several days of decisions.
8. Enable live switching only after the results are sensible.

## Documentation

- [Installation](docs/INSTALL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## DigiByte warning

DigiByte uses multiple mining algorithms. Profit calculations for a Bitaxe require the **DigiByte SHA-256-specific** network hashrate.

## License

MIT
