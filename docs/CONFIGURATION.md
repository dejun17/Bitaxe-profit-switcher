# Configuration

The application reads `/etc/bitaxe-switcher/config.yaml`.

## Safety

Keep `general.dry_run: true` until comparisons and manual simulations are correct.

## Miners

Each miner has its own address, expected hashrate, power draw, optional electricity rate, and allowed pool profiles. Live AxeOS metrics can override configured estimates.

## Profiles

A profile connects a coin definition to an AxeOS pool configuration. Review every `axeos_patch` field against the AxeOS release running on the miner.

## Network statistics

Supported providers include static values, generic HTTP JSON, and Bitcoin-compatible JSON-RPC. The starter BTC value is static so a fresh install does not require a Bitcoin node.

For DigiByte, use SHA-256-specific network hashrate—not aggregate DigiByte hashrate across all mining algorithms.

## Manual bias

`manual_bias_multiplier` changes the decision score. It does not predict prices. Keep the value near `1.0`, document temporary changes, and preserve the switching safeguards.
