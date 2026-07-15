#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
import yaml

VERSION = "1.0.0"
DEFAULT_CONFIG = "/etc/bitaxe-switcher/config.yaml"
DEFAULT_STATE = "/var/lib/bitaxe-switcher/state.json"
DEFAULT_LOG = "/var/log/bitaxe-switcher.log"
LOG = logging.getLogger("bitaxe-switcher")


class SwitcherError(RuntimeError):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def number(value: Any, label: str, allow_zero: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SwitcherError(f"{label} must be numeric") from exc
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        raise SwitcherError(f"{label} has an invalid value: {value}")
    return result


def deep_get(data: Any, dotted_path: str) -> Any:
    current = data
    for part in dotted_path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise SwitcherError(f"Cannot traverse JSON path {dotted_path}")
    return current


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SwitcherError(f"Config not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SwitcherError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SwitcherError("Top-level config must be a mapping")
    return data


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "miners": {}, "last_run": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SwitcherError(f"Cannot read state: {exc}") from exc
    data.setdefault("miners", {})
    return data


class HTTP:
    def __init__(self, timeout: float, verify_tls: bool):
        self.timeout = timeout
        self.verify_tls = verify_tls
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"bitaxe-profit-switcher/{VERSION}"

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        response = self.session.get(url, headers=headers, timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise SwitcherError(f"Expected JSON object from {url}")
        return data

    def call(self, method: str, url: str, body: dict[str, Any] | None = None) -> requests.Response:
        response = self.session.request(method, url, json=body, timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        return response


class AxeOS:
    def __init__(self, http: HTTP):
        self.http = http

    @staticmethod
    def base(address: str) -> str:
        address = address.strip().rstrip("/")
        return address if address.startswith(("http://", "https://")) else f"http://{address}"

    def info(self, address: str) -> dict[str, Any]:
        return self.http.get_json(f"{self.base(address)}/api/system/info")

    def patch(self, address: str, payload: dict[str, Any]) -> None:
        self.http.call("PATCH", f"{self.base(address)}/api/system", payload)

    def restart(self, address: str) -> None:
        self.http.call("POST", f"{self.base(address)}/api/system/restart")


def profile_matches(profile: dict[str, Any], info: dict[str, Any]) -> bool:
    expected = profile.get("verify") or {
        key: profile["axeos_patch"].get(key)
        for key in ("stratumURL", "stratumPort", "stratumUser", "stratumProtocol")
    }
    for key, wanted in expected.items():
        if wanted is None:
            continue
        if str(info.get(key)).lower() != str(wanted).lower():
            return False
    return True


def current_profile(info: dict[str, Any], profiles: dict[str, Any]) -> str | None:
    matches = [pid for pid, profile in profiles.items() if profile.get("enabled", True) and profile_matches(profile, info)]
    return matches[0] if len(matches) == 1 else None


class Controller:
    def __init__(self, config: dict[str, Any], state: dict[str, Any], state_path: Path, dry_run: bool):
        self.config = config
        self.state = state
        self.state_path = state_path
        self.dry_run = dry_run
        general = config["general"]
        self.http = HTTP(float(general.get("http_timeout_seconds", 15)), bool(general.get("verify_tls", True)))
        self.axeos = AxeOS(self.http)
        self.validate()

    def validate(self) -> None:
        for section in ("general", "coins", "profiles", "miners"):
            if not isinstance(self.config.get(section), dict):
                raise SwitcherError(f"Missing config section: {section}")
        for pid, profile in self.config["profiles"].items():
            if profile.get("coin") not in self.config["coins"]:
                raise SwitcherError(f"Profile {pid} references unknown coin")
            if not isinstance(profile.get("axeos_patch"), dict):
                raise SwitcherError(f"Profile {pid} needs axeos_patch")
        for mid, miner in self.config["miners"].items():
            for pid in miner.get("allowed_profiles", []):
                if pid not in self.config["profiles"]:
                    raise SwitcherError(f"Miner {mid} allows unknown profile {pid}")

    def fetch_prices(self) -> dict[str, float]:
        output: dict[str, float] = {}
        cg: dict[str, str] = {}
        for symbol, coin in self.config["coins"].items():
            if not coin.get("enabled", True):
                continue
            price = coin.get("price", {})
            provider = price.get("provider", "coingecko")
            if provider == "coingecko":
                cg[symbol] = price["coingecko_id"]
            elif provider == "static":
                output[symbol] = number(price["usd"], f"{symbol} price")
            elif provider == "http_json":
                payload = self.http.get_json(price["url"], price.get("headers"))
                output[symbol] = number(deep_get(payload, price["json_path"]), f"{symbol} price")
            else:
                raise SwitcherError(f"Unsupported price provider {provider}")
        if cg:
            provider = self.config.get("price_provider", {})
            base = provider.get("base_url", "https://api.coingecko.com/api/v3").rstrip("/")
            headers: dict[str, str] = {}
            key = provider.get("api_key") or os.getenv(provider.get("api_key_env", "COINGECKO_API_KEY"), "")
            if key:
                headers[provider.get("api_key_header", "x-cg-demo-api-key")] = key
            response = self.http.session.get(
                f"{base}/simple/price",
                params={"ids": ",".join(sorted(set(cg.values()))), "vs_currencies": "usd"},
                headers=headers,
                timeout=self.http.timeout,
                verify=self.http.verify_tls,
            )
            response.raise_for_status()
            data = response.json()
            for symbol, coin_id in cg.items():
                output[symbol] = number(data[coin_id]["usd"], f"{symbol} CoinGecko price")
        return output

    def network_hashrate(self, symbol: str, network: dict[str, Any]) -> tuple[float, str]:
        provider = network.get("provider", "static")
        if provider == "static":
            return number(network["network_hashrate_hs"], f"{symbol} network hashrate"), "static"
        if provider == "http_json":
            payload = self.http.get_json(network["url"], network.get("headers"))
            value = number(deep_get(payload, network["json_path"]), f"{symbol} network hashrate")
            return value * float(network.get("multiplier", 1)), f"HTTP {network['url']}"
        if provider == "json_rpc":
            username = network.get("username") or os.getenv(network.get("username_env", ""), "")
            password = network.get("password") or os.getenv(network.get("password_env", ""), "")
            method = network.get("method", "getnetworkhashps")
            body = {"jsonrpc": "2.0", "id": f"switcher-{symbol}", "method": method, "params": network.get("params", [120])}
            response = self.http.session.post(
                network["url"], json=body,
                auth=(username, password) if username or password else None,
                timeout=self.http.timeout, verify=self.http.verify_tls,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("error"):
                raise SwitcherError(f"{symbol} RPC error: {payload['error']}")
            value: Any = payload.get("result")
            if network.get("json_path"):
                value = deep_get(value, network["json_path"])
            return number(value, f"{symbol} network hashrate") * float(network.get("multiplier", 1)), f"RPC {method}"
        raise SwitcherError(f"Unsupported network provider {provider}")

    def coin_metrics(self) -> dict[str, dict[str, Any]]:
        prices = self.fetch_prices()
        output: dict[str, dict[str, Any]] = {}
        for symbol, coin in self.config["coins"].items():
            if not coin.get("enabled", True):
                continue
            network_hashrate, source = self.network_hashrate(symbol, coin["network"])
            output[symbol] = {
                "price": prices[symbol],
                "network_hashrate": network_hashrate,
                "reward": number(coin["block_reward_coins"], f"{symbol} reward"),
                "block_time": number(coin["block_time_seconds"], f"{symbol} block time"),
                "source": source,
            }
        return output

    def miner_metrics(self, miner_id: str, miner: dict[str, Any]) -> dict[str, Any]:
        info: dict[str, Any] = {}
        if miner.get("use_live_metrics", True):
            try:
                info = self.axeos.info(miner["address"])
            except Exception as exc:
                if miner.get("require_live_metrics", False):
                    raise SwitcherError(f"{miner_id}: live metrics failed: {exc}") from exc
                LOG.warning("%s: live metrics unavailable; configured values used: %s", miner_id, exc)
        configured_hashrate = number(miner["expected_hashrate_ths"], f"{miner_id} expected_hashrate_ths")
        configured_power = number(miner["power_watts"], f"{miner_id} power_watts")
        live_hashrate = None
        for key in ("hashRate_10m", "hashRate_1m", "hashRate"):
            try:
                if float(info.get(key, 0)) > 0:
                    live_hashrate = float(info[key]) / 1000
                    break
            except (TypeError, ValueError):
                pass
        try:
            live_power = float(info.get("power", 0)) if float(info.get("power", 0)) > 0 else None
        except (TypeError, ValueError):
            live_power = None
        return {
            "id": miner_id,
            "name": miner.get("name", miner_id),
            "address": miner["address"],
            "hashrate_ths": live_hashrate if miner.get("use_live_hashrate", True) and live_hashrate else configured_hashrate,
            "power_watts": live_power if miner.get("use_live_power", True) and live_power else configured_power,
            "current_profile": current_profile(info, self.config["profiles"]) if info else None,
            "info": info,
            "electricity": number(miner.get("electricity_usd_kwh", self.config["general"]["electricity_usd_kwh"]), "electricity rate", True),
        }

    def profit(self, miner: dict[str, Any], pid: str, profile: dict[str, Any], coin: dict[str, Any]) -> dict[str, Any]:
        hashrate_hs = miner["hashrate_ths"] * 1_000_000_000_000
        coins_day = hashrate_hs / coin["network_hashrate"] * (86400 / coin["block_time"]) * coin["reward"]
        gross = coins_day * coin["price"]
        fee_pct = number(profile.get("pool_fee_percent", 0), "pool fee", True) + number(profile.get("conversion_fee_percent", 0), "conversion fee", True)
        fees = gross * fee_pct / 100
        electricity = miner["power_watts"] / 1000 * 24 * miner["electricity"]
        net = gross - fees - electricity
        bias = number(profile.get("manual_bias_multiplier", 1), "manual bias")
        return {
            "profile": pid,
            "coin": profile["coin"],
            "coins_day": coins_day,
            "gross": gross,
            "fees": fees,
            "electricity": electricity,
            "net": net,
            "score": net * bias,
            "bias": bias,
        }

    def collect(self) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        coins = self.coin_metrics()
        miners: dict[str, dict[str, Any]] = {}
        results: dict[str, list[dict[str, Any]]] = {}
        for mid, miner_cfg in self.config["miners"].items():
            if not miner_cfg.get("enabled", True):
                continue
            miner = self.miner_metrics(mid, miner_cfg)
            miners[mid] = miner
            allowed = miner_cfg.get("allowed_profiles") or list(self.config["profiles"])
            rows = []
            for pid in allowed:
                profile = self.config["profiles"][pid]
                if profile.get("enabled", True) and self.config["coins"][profile["coin"]].get("enabled", True):
                    rows.append(self.profit(miner, pid, profile, coins[profile["coin"]]))
            results[mid] = sorted(rows, key=lambda row: row["score"], reverse=True)
        return miners, results

    @staticmethod
    def render(miners: dict[str, dict[str, Any]], results: dict[str, list[dict[str, Any]]]) -> None:
        for mid, rows in results.items():
            miner = miners[mid]
            print(f"\n{miner['name']} [{mid}] — {miner['hashrate_ths']:.3f} TH/s, {miner['power_watts']:.2f} W, current={miner['current_profile'] or 'unknown'}")
            print("Profile                    Coin   Gross/day   Power/day   Fees/day    Net/day     Score")
            print("-------------------------  -----  ----------  ----------  ----------  ----------  ----------")
            for row in rows:
                print(f"{row['profile'][:25]:25}  {row['coin'][:5]:5}  ${row['gross']:9.5f}  ${row['electricity']:9.5f}  ${row['fees']:9.5f}  ${row['net']:9.5f}  ${row['score']:9.5f}")

    def state_for(self, mid: str) -> dict[str, Any]:
        return self.state.setdefault("miners", {}).setdefault(mid, {
            "active_profile": None, "last_switch_at": None,
            "candidate_profile": None, "candidate_checks": 0,
            "switch_history": [], "last_decision": None,
        })

    def limits_ok(self, state: dict[str, Any]) -> tuple[bool, str]:
        rules = self.config["general"]["switching"]
        last = parse_time(state.get("last_switch_at"))
        if last:
            age = (now() - last).total_seconds() / 3600
            minimum = float(rules.get("minimum_runtime_hours", 6))
            if age < minimum:
                return False, f"minimum runtime {age:.2f}/{minimum:.2f} hours"
        cutoff = now() - dt.timedelta(hours=24)
        history = [entry for entry in state.get("switch_history", []) if (parse_time(entry.get("at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)) >= cutoff]
        state["switch_history"] = history
        maximum = int(rules.get("maximum_switches_per_24h", 2))
        if len(history) >= maximum:
            return False, f"maximum switches {len(history)}/{maximum}"
        return True, "ok"

    def decide(self, miner: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        state = self.state_for(miner["id"])
        if not rows:
            return {"action": "none", "reason": "no eligible profiles"}
        current_id = miner["current_profile"] or state.get("active_profile")
        if current_id is None and not self.config["general"]["switching"].get("adopt_best_when_current_unknown", False):
            return {"action": "none", "reason": "current profile unknown; manually select one first"}
        best = rows[0]
        current = next((row for row in rows if row["profile"] == current_id), None)
        if best["profile"] == current_id:
            state.update({"active_profile": current_id, "candidate_profile": None, "candidate_checks": 0})
            return {"action": "stay", "profile": current_id, "reason": "current profile is best"}
        current_net = current["net"] if current else 0
        current_score = current["score"] if current else 0
        usd_gain = best["net"] - current_net
        pct_gain = ((best["score"] - current_score) / current_score * 100) if current_score > 0 else (float("inf") if best["score"] > current_score else 0)
        rules = self.config["general"]["switching"]
        if usd_gain < float(rules.get("minimum_net_improvement_usd_day", 0.05)):
            state.update({"candidate_profile": None, "candidate_checks": 0})
            return {"action": "stay", "reason": f"gain ${usd_gain:.5f}/day below threshold"}
        if pct_gain < float(rules.get("minimum_advantage_percent", 30)):
            state.update({"candidate_profile": None, "candidate_checks": 0})
            return {"action": "stay", "reason": f"gain {pct_gain:.2f}% below threshold"}
        okay, reason = self.limits_ok(state)
        if not okay:
            return {"action": "stay", "candidate": best["profile"], "reason": reason}
        if state.get("candidate_profile") == best["profile"]:
            state["candidate_checks"] = int(state.get("candidate_checks", 0)) + 1
        else:
            state["candidate_profile"] = best["profile"]
            state["candidate_checks"] = 1
        required = int(rules.get("confirmation_checks", 4))
        if state["candidate_checks"] < required:
            return {"action": "wait", "candidate": best["profile"], "checks": state["candidate_checks"], "required": required}
        return {"action": "switch", "from": current_id, "to": best["profile"], "usd_gain_day": usd_gain, "percent_gain": pct_gain}

    def apply(self, mid: str, miner: dict[str, Any], pid: str, force: bool = False) -> None:
        profile = self.config["profiles"][pid]
        LOG.warning("%s: apply %s%s", mid, pid, " [DRY RUN]" if self.dry_run and not force else "")
        if self.dry_run and not force:
            return
        self.axeos.patch(miner["address"], profile["axeos_patch"])
        if profile.get("restart_after_patch", True):
            self.axeos.restart(miner["address"])
            time.sleep(float(profile.get("restart_grace_seconds", 8)))
        if profile.get("verify_after_switch", True):
            rules = self.config["general"]["switching"]
            deadline = time.monotonic() + int(rules.get("verification_timeout_seconds", 180))
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    info = self.axeos.info(miner["address"])
                    if profile_matches(profile, info):
                        break
                except Exception as exc:
                    last_error = exc
                time.sleep(int(rules.get("verification_interval_seconds", 5)))
            else:
                raise SwitcherError(f"{mid}: profile verification timed out: {last_error}")
        state = self.state_for(mid)
        timestamp = now().isoformat()
        previous = state.get("active_profile") or miner["current_profile"]
        state.update({"active_profile": pid, "last_switch_at": timestamp, "candidate_profile": None, "candidate_checks": 0})
        state.setdefault("switch_history", []).append({"at": timestamp, "from": previous, "to": pid})

    def auto(self) -> int:
        miners, results = self.collect()
        self.render(miners, results)
        for mid, rows in results.items():
            decision = self.decide(miners[mid], rows)
            self.state_for(mid)["last_decision"] = {"at": now().isoformat(), **decision}
            LOG.info("%s decision: %s", mid, json.dumps(decision, sort_keys=True))
            if decision.get("action") == "switch":
                self.apply(mid, miners[mid], decision["to"])
        self.state["last_run"] = now().isoformat()
        atomic_json(self.state_path, self.state)
        return 0

    def manual_switch(self, mid: str, pid: str, force: bool) -> int:
        if mid not in self.config["miners"]:
            raise SwitcherError(f"Unknown miner: {mid}")
        if pid not in self.config["profiles"]:
            raise SwitcherError(f"Unknown profile: {pid}")
        allowed = self.config["miners"][mid].get("allowed_profiles") or list(self.config["profiles"])
        if pid not in allowed and not force:
            raise SwitcherError(f"Profile {pid} is not allowed for {mid}")
        miner = self.miner_metrics(mid, self.config["miners"][mid])
        self.apply(mid, miner, pid, force)
        atomic_json(self.state_path, self.state)
        return 0


def setup_logging(path: str, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    except PermissionError:
        pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Multi-miner AxeOS SHA-256 profit switcher")
    result.add_argument("--config", default=DEFAULT_CONFIG)
    result.add_argument("--state", default=DEFAULT_STATE)
    result.add_argument("--log", default=DEFAULT_LOG)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--verbose", action="store_true")
    result.add_argument("--version", action="version", version=VERSION)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("compare")
    commands.add_parser("status")
    commands.add_parser("auto")
    switch = commands.add_parser("switch")
    switch.add_argument("miner")
    switch.add_argument("profile")
    switch.add_argument("--force", action="store_true")
    return result


def main() -> int:
    args = parser().parse_args()
    setup_logging(args.log, args.verbose)
    try:
        config = load_yaml(Path(args.config))
        state = load_state(Path(args.state))
        controller = Controller(config, state, Path(args.state), bool(args.dry_run or config["general"].get("dry_run", True)))
        if args.command == "compare":
            miners, results = controller.collect()
            controller.render(miners, results)
            return 0
        if args.command == "status":
            miners, results = controller.collect()
            controller.render(miners, results)
            print("\nState:\n" + json.dumps(state, indent=2, sort_keys=True))
            return 0
        if args.command == "auto":
            return controller.auto()
        if args.command == "switch":
            return controller.manual_switch(args.miner, args.profile, args.force)
        return 1
    except (SwitcherError, requests.RequestException, KeyError) as exc:
        LOG.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
