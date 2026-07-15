# Bitaxe Profit Switcher

A multi-miner AxeOS controller for a Debian/Ubuntu LXC or VM on Proxmox.

It calculates expected daily results independently for every miner from:

- Live or configured TH/s
- Live or configured wattage
- Electricity price
- Coin price
- SHA-256 network hashrate
- Block reward and block interval
- Pool and conversion fees
- An optional manual bias

It can then switch each miner to its own best eligible pool profile.

## Safety defaults

The supplied configuration starts in **dry-run mode** and requires:

- 30% score advantage
- At least $0.05/day additional expected net revenue
- Four consecutive winning checks
- Six hours minimum runtime before another switch
- No more than two switches per miner per 24 hours
- AxeOS profile verification after a change

For a small Bitaxe, these conservative rules matter because most differences are only pennies.

## Install

```bash
unzip bitaxe-profit-switcher.zip
cd bitaxe-profit-switcher
chmod +x install.sh
sudo ./install.sh
```

Edit the config:

```bash
sudo nano /etc/bitaxe-switcher/config.yaml
```

Set RPC/API secrets:

```bash
sudo cp examples/environment.example /etc/bitaxe-switcher/environment
sudo chmod 600 /etc/bitaxe-switcher/environment
sudo nano /etc/bitaxe-switcher/environment
```

## Test first

```bash
sudo -u bitaxe-switcher \
  /opt/bitaxe-switcher/venv/bin/python \
  /opt/bitaxe-switcher/bitaxe_switcher.py \
  --config /etc/bitaxe-switcher/config.yaml \
  --state /var/lib/bitaxe-switcher/state.json \
  --dry-run compare
```

One automatic dry-run cycle:

```bash
sudo -u bitaxe-switcher \
  /opt/bitaxe-switcher/venv/bin/python \
  /opt/bitaxe-switcher/bitaxe_switcher.py \
  --dry-run auto
```

Manual switch:

```bash
sudo -u bitaxe-switcher \
  /opt/bitaxe-switcher/venv/bin/python \
  /opt/bitaxe-switcher/bitaxe_switcher.py \
  switch bitaxe_602 btc_braiins_sv2
```

## Enable the timer

```bash
sudo systemctl enable --now bitaxe-switcher.timer
systemctl list-timers bitaxe-switcher.timer
journalctl -u bitaxe-switcher.service -f
```

Leave this enabled for at least a week:

```yaml
general:
  dry_run: true
```

Only set it to `false` after confirming the logged decisions are sensible.

## Add miners

Copy a block under `miners` and assign its own expected performance and allowed profiles:

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
    electricity_usd_kwh: 0.12
    allowed_profiles:
      - btc_braiins_sv2
      - dgb_sha256_pool
```

Each miner is evaluated independently, so miners with different efficiency or electricity rates may select different profiles.

## Data providers

### CoinGecko price

```yaml
price:
  provider: coingecko
  coingecko_id: bitcoin
```

### Static price or hashrate

```yaml
price:
  provider: static
  usd: 65000

network:
  provider: static
  network_hashrate_hs: 1000000000000000000000
```

### Generic HTTP JSON

```yaml
network:
  provider: http_json
  url: https://example/api
  json_path: data.hashrate
  multiplier: 1000000000000
```

### Bitcoin-compatible JSON-RPC

```yaml
network:
  provider: json_rpc
  url: http://192.168.50.81:8332
  username_env: BITCOIN_RPC_USER
  password_env: BITCOIN_RPC_PASSWORD
  method: getnetworkhashps
  params: [120]
```

## DigiByte warning

DigiByte uses several mining algorithms. The DGB network hashrate value must be the **SHA-256-specific hashrate**, not total hashrate across all algorithms.

Verify the current block reward and pool details before enabling the DGB profile.

## Predictions and rumors

This program intentionally does not scrape social media or pretend it can reliably predict prices.

A manual bias is available:

```yaml
manual_bias_multiplier: 1.10
```

That gives a profile a temporary 10% score boost. Use small values, keep confirmation rules enabled, document why you changed it, and remove it after the event window.

If your primary belief is that a coin will increase in price, buying a small amount is usually a more direct exposure than mining it early. Mining profitability can be erased by rising difficulty, other miners switching in, payout minimums, and conversion costs.

## Security

- Keep AxeOS and this controller on the trusted LAN.
- Do not expose AxeOS directly to the internet.
- Keep `/etc/bitaxe-switcher/environment` mode `600`.
- Review every `axeos_patch` before leaving dry-run mode.


# Web interface

The non-containerized web interface provides:

- Live dashboard for every miner
- Per-miner profitability tables
- Manual simulated or live profile switching
- Miner restart controls
- Miner, pool profile, and coin editors
- Global safety settings
- Raw YAML editor with timestamped backups
- Controller log viewer
- `/api/status` JSON endpoint and `/health` health check

## Install the web version

```bash
chmod +x install-web.sh
sudo ./install-web.sh
```

Open:

```text
http://SERVER-IP:8088
```

The default configuration remains in dry-run mode. Test the dashboard and simulated switching before enabling live mode.
