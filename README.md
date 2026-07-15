# Bitaxe Profit Switcher Web

A Proxmox-friendly Python controller and local web dashboard for comparing SHA-256 mining profitability and independently switching multiple AxeOS miners between pool profiles.

## Features

- Multiple AxeOS/Bitaxe miners
- Per-miner expected or live TH/s and power consumption
- Per-miner electricity-rate overrides
- Independent allowed pool profiles per miner
- BTC, BCH, DGB, and other SHA-256 profile support
- CoinGecko, static, HTTP JSON, and Bitcoin-compatible JSON-RPC data providers
- Pool fees, conversion fees, and controlled manual biasing
- Conservative switch thresholds and confirmation checks
- Dry-run mode
- Manual profile switching and miner restarts
- Local web interface on port `8088`
- Persistent state, decision history, configuration backups, and logs
- systemd services and timer for Debian/Ubuntu

> This application optimizes expected mining revenue. It does not predict prices or guarantee profit.

## Web pages

- **Dashboard** — live status, efficiency, profitability, decisions, and manual actions
- **Miners** — add miners and configure hashrate, power, electricity, and allowed profiles
- **Pool Profiles** — configure Stratum V1/V2 pools, workers, wallets, fees, and AxeOS settings
- **Coins** — configure price, network hashrate, block reward, and block interval data
- **Settings** — configure switching rules and dry-run/live operation
- **Raw YAML** — advanced configuration editor with validation and backups
- **Logs** — view controller activity from the browser

## Install on a Debian Proxmox LXC

```bash
git clone https://github.com/dejun17/Bitaxe-profit-switcher.git
cd Bitaxe-profit-switcher
chmod +x install-web.sh
sudo ./install-web.sh
```

Open:

```text
http://<LXC-IP>:8088
```

Find the LXC address with:

```bash
hostname -I
```

## First-run safety

The example configuration starts with dry-run enabled:

```yaml
general:
  dry_run: true
```

Before enabling automatic changes:

1. Open the web dashboard.
2. Configure every miner.
3. Verify all pool endpoints and AxeOS field names.
4. Configure accurate coin/network statistics.
5. Run dry evaluations for at least several days.
6. Test each profile with a manual switch.
7. Confirm the miner reconnects and submits accepted shares.
8. Only then set `dry_run: false`.

## Service commands

```bash
systemctl status bitaxe-switcher-web
journalctl -u bitaxe-switcher-web -f
systemctl restart bitaxe-switcher-web
```

Controller log:

```text
/var/log/bitaxe-switcher.log
```

Configuration:

```text
/etc/bitaxe-switcher/config.yaml
```

## Add another miner

```yaml
miners:
  second_gamma:
    name: Second Gamma 602
    address: 192.168.50.80
    enabled: true
    expected_hashrate_ths: 1.85
    power_watts: 31.5
    use_live_metrics: true
    use_live_hashrate: true
    use_live_power: true
    allowed_profiles:
      - btc_braiins_sv2
      - dgb_sha256_pool
```

Each miner is evaluated independently, so miners with different efficiency or electricity costs can select different profiles.

## DigiByte warning

DigiByte uses multiple mining algorithms. The controller must be given the **SHA-256-specific** DigiByte network hashrate—not total DigiByte network hashrate across every algorithm.

## Prediction and manual bias

A pool profile may include a controlled score multiplier:

```yaml
manual_bias_multiplier: 1.10
```

That gives the profile a 10% score boost. It is not a market prediction. Keep minimum-advantage, confirmation, minimum-runtime, and maximum-switch protections enabled.

## Security

- Keep AxeOS and this dashboard on a trusted LAN.
- Do not expose AxeOS directly to the internet.
- Protect RPC credentials and API keys.
- Review every `axeos_patch` before disabling dry-run mode.
- Use unique pool worker names.

## Status

This is an early development version. Live switching should be tested carefully against the exact AxeOS release and pool configuration in use.
