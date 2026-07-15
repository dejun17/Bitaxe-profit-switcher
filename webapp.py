#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from bitaxe_switcher import Controller, SwitcherError, atomic_json, load_state, load_yaml, now

APP_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path(os.getenv('BITAXE_SWITCHER_CONFIG', '/etc/bitaxe-switcher/config.yaml'))
DEFAULT_STATE = Path(os.getenv('BITAXE_SWITCHER_STATE', '/var/lib/bitaxe-switcher/state.json'))
DEFAULT_LOG = Path(os.getenv('BITAXE_SWITCHER_LOG', '/var/log/bitaxe-switcher.log'))
DEFAULT_HOST = os.getenv('BITAXE_SWITCHER_WEB_HOST', '0.0.0.0')
DEFAULT_PORT = int(os.getenv('BITAXE_SWITCHER_WEB_PORT', '8088'))

app = FastAPI(title='Bitaxe Profit Switcher', version='1.1.0')
app.mount('/static', StaticFiles(directory=APP_DIR / 'static'), name='static')
templates = Jinja2Templates(directory=APP_DIR / 'templates')
LOCK = threading.RLock()


def app_paths(request: Request) -> tuple[Path, Path, Path]:
    return request.app.state.config_path, request.app.state.state_path, request.app.state.log_path


def flash(request: Request, message: str, level: str = 'success') -> RedirectResponse:
    response = RedirectResponse(request.headers.get('referer') or '/', status_code=303)
    response.set_cookie('flash_message', message, max_age=30, httponly=True, samesite='lax')
    response.set_cookie('flash_level', level, max_age=30, httponly=True, samesite='lax')
    return response


def template(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200):
    context = context or {}
    context.update({
        'request': request,
        'flash_message': request.cookies.get('flash_message'),
        'flash_level': request.cookies.get('flash_level', 'success'),
        'current_year': dt.datetime.now().year,
    })
    response = templates.TemplateResponse(request=request, name=name, context=context, status_code=status_code)
    if request.cookies.get('flash_message'):
        response.delete_cookie('flash_message')
        response.delete_cookie('flash_level')
    return response


def controller_for(request: Request, force_dry_run: bool | None = None) -> tuple[Controller, dict[str, Any], dict[str, Any]]:
    config_path, state_path, _ = app_paths(request)
    config = load_yaml(config_path)
    state = load_state(state_path)
    dry_run = bool(config['general'].get('dry_run', True)) if force_dry_run is None else force_dry_run
    return Controller(config, state, state_path, dry_run), config, state


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2(path, path.with_name(f'{path.name}.{stamp}.bak'))
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(yaml.safe_dump(config, sort_keys=False, width=120), encoding='utf-8')
    os.replace(temporary, path)


def clean_text(value: str | None) -> str:
    return (value or '').strip()


def as_float(value: str, label: str, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise SwitcherError(f'{label} must be numeric.') from exc
    if parsed < minimum:
        raise SwitcherError(f'{label} must be at least {minimum}.')
    return parsed


def as_int(value: str, label: str, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SwitcherError(f'{label} must be a whole number.') from exc
    if parsed < minimum:
        raise SwitcherError(f'{label} must be at least {minimum}.')
    return parsed


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request):
    error = None
    miners: dict[str, Any] = {}
    results: dict[str, Any] = {}
    state: dict[str, Any] = {}
    config: dict[str, Any] = {}
    try:
        with LOCK:
            controller, config, state = controller_for(request)
            miners, results = controller.collect()
    except Exception as exc:
        error = str(exc)
        try:
            config = load_yaml(app_paths(request)[0])
            state = load_state(app_paths(request)[1])
        except Exception:
            pass
    return template(request, 'dashboard.html', {
        'title': 'Dashboard', 'active': 'dashboard', 'miners': miners,
        'results': results, 'state': state, 'config': config, 'error': error,
    })


@app.get('/api/status')
def api_status(request: Request):
    try:
        with LOCK:
            controller, config, state = controller_for(request)
            miners, results = controller.collect()
        return {'ok': True, 'miners': miners, 'results': results, 'state': state, 'dry_run': config['general'].get('dry_run', True)}
    except Exception as exc:
        return JSONResponse({'ok': False, 'error': str(exc)}, status_code=500)


@app.post('/actions/run')
def run_action(request: Request, mode: str = Form('dry-run')):
    try:
        with LOCK:
            force_dry = mode != 'live'
            controller, config, _ = controller_for(request, force_dry_run=force_dry)
            controller.auto()
        label = 'Dry-run evaluation completed.' if force_dry else 'Live automatic evaluation completed.'
        return flash(request, label)
    except Exception as exc:
        return flash(request, f'Run failed: {exc}', 'danger')


@app.post('/actions/switch')
def switch_action(request: Request, miner_id: str = Form(...), profile_id: str = Form(...), live: str | None = Form(None)):
    try:
        with LOCK:
            controller, _, _ = controller_for(request, force_dry_run=(live != 'yes'))
            controller.manual_switch(miner_id, profile_id, force=(live == 'yes'))
        return flash(request, f"{'Applied' if live == 'yes' else 'Simulated'} {profile_id} on {miner_id}.")
    except Exception as exc:
        return flash(request, f'Switch failed: {exc}', 'danger')


@app.post('/actions/restart')
def restart_miner(request: Request, miner_id: str = Form(...)):
    try:
        with LOCK:
            controller, config, _ = controller_for(request)
            miner = config['miners'][miner_id]
            controller.axeos.restart(miner['address'])
        return flash(request, f'Restart command sent to {miner_id}.', 'warning')
    except Exception as exc:
        return flash(request, f'Restart failed: {exc}', 'danger')


@app.get('/miners', response_class=HTMLResponse)
def miners_page(request: Request):
    controller, config, state = controller_for(request)
    live: dict[str, Any] = {}
    for mid, cfg in config['miners'].items():
        try:
            live[mid] = controller.miner_metrics(mid, cfg)
        except Exception as exc:
            live[mid] = {'error': str(exc)}
    return template(request, 'miners.html', {
        'title': 'Miners', 'active': 'miners', 'config': config,
        'miners': config['miners'], 'profiles': config['profiles'], 'live': live,
    })


@app.post('/miners/save')
def save_miner(
    request: Request,
    original_id: str = Form(''),
    miner_id: str = Form(...),
    name: str = Form(...),
    address: str = Form(...),
    expected_hashrate_ths: str = Form(...),
    power_watts: str = Form(...),
    electricity_usd_kwh: str = Form(''),
    allowed_profiles: list[str] = Form(default=[]),
    enabled: str | None = Form(None),
    use_live_metrics: str | None = Form(None),
):
    try:
        miner_id = clean_text(miner_id).replace(' ', '_')
        if not miner_id:
            raise SwitcherError('Miner ID is required.')
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            if original_id and original_id != miner_id:
                config['miners'].pop(original_id, None)
            if not original_id and miner_id in config['miners']:
                raise SwitcherError(f'Miner ID {miner_id} already exists.')
            record: dict[str, Any] = {
                'name': clean_text(name), 'address': clean_text(address),
                'enabled': enabled == 'on',
                'expected_hashrate_ths': as_float(expected_hashrate_ths, 'Expected TH/s', 0.000001),
                'power_watts': as_float(power_watts, 'Power', 0.000001),
                'use_live_metrics': use_live_metrics == 'on',
                'use_live_hashrate': use_live_metrics == 'on',
                'use_live_power': use_live_metrics == 'on',
                'require_live_metrics': False,
                'allowed_profiles': allowed_profiles,
            }
            if clean_text(electricity_usd_kwh):
                record['electricity_usd_kwh'] = as_float(electricity_usd_kwh, 'Electricity rate', 0)
            config['miners'][miner_id] = record
            Controller(config, load_state(app_paths(request)[1]), app_paths(request)[1], True).validate()
            save_config(config_path, config)
        return flash(request, f'Miner {miner_id} saved.')
    except Exception as exc:
        return flash(request, f'Unable to save miner: {exc}', 'danger')


@app.post('/miners/delete')
def delete_miner(request: Request, miner_id: str = Form(...)):
    try:
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            if miner_id not in config['miners']:
                raise SwitcherError('Miner not found.')
            del config['miners'][miner_id]
            save_config(config_path, config)
        return flash(request, f'Miner {miner_id} deleted.', 'warning')
    except Exception as exc:
        return flash(request, f'Unable to delete miner: {exc}', 'danger')


@app.get('/profiles', response_class=HTMLResponse)
def profiles_page(request: Request):
    _, config, _ = controller_for(request)
    return template(request, 'profiles.html', {
        'title': 'Pool Profiles', 'active': 'profiles', 'config': config,
        'profiles': config['profiles'], 'coins': config['coins'],
    })


@app.post('/profiles/save')
def save_profile(
    request: Request,
    original_id: str = Form(''), profile_id: str = Form(...), name: str = Form(...),
    coin: str = Form(...), host: str = Form(...), port: str = Form(...), user: str = Form(...),
    password: str = Form('x'), protocol: str = Form('SV1'), pool_fee_percent: str = Form('0'),
    conversion_fee_percent: str = Form('0'), manual_bias_multiplier: str = Form('1'),
    authority_pubkey: str = Form(''), channel_type: str = Form('extended'),
    enabled: str | None = Form(None), verify_after_switch: str | None = Form(None),
):
    try:
        profile_id = clean_text(profile_id).replace(' ', '_')
        if not profile_id:
            raise SwitcherError('Profile ID is required.')
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            if coin not in config['coins']:
                raise SwitcherError(f'Unknown coin: {coin}')
            if original_id and original_id != profile_id:
                config['profiles'].pop(original_id, None)
                for miner in config['miners'].values():
                    miner['allowed_profiles'] = [profile_id if p == original_id else p for p in miner.get('allowed_profiles', [])]
            if not original_id and profile_id in config['profiles']:
                raise SwitcherError(f'Profile ID {profile_id} already exists.')
            patch: dict[str, Any] = {
                'stratumURL': clean_text(host), 'stratumPort': as_int(port, 'Port', 1),
                'stratumUser': clean_text(user), 'stratumPassword': password,
                'stratumProtocol': protocol, 'stratumDecodeCoinbase': False,
            }
            verify: dict[str, Any] = {
                'stratumURL': clean_text(host), 'stratumPort': as_int(port, 'Port', 1),
                'stratumUser': clean_text(user), 'stratumProtocol': protocol,
            }
            if protocol == 'SV2':
                patch['stratumV2ChannelType'] = channel_type
                patch['stratumV2AuthorityPubkey'] = clean_text(authority_pubkey)
                verify['stratumV2ChannelType'] = channel_type
            config['profiles'][profile_id] = {
                'name': clean_text(name), 'coin': coin, 'enabled': enabled == 'on',
                'pool_fee_percent': as_float(pool_fee_percent, 'Pool fee', 0),
                'conversion_fee_percent': as_float(conversion_fee_percent, 'Conversion fee', 0),
                'manual_bias_multiplier': as_float(manual_bias_multiplier, 'Bias', 0.000001),
                'axeos_patch': patch, 'verify': verify, 'restart_after_patch': True,
                'restart_grace_seconds': 8, 'verify_after_switch': verify_after_switch == 'on',
            }
            Controller(config, load_state(app_paths(request)[1]), app_paths(request)[1], True).validate()
            save_config(config_path, config)
        return flash(request, f'Profile {profile_id} saved.')
    except Exception as exc:
        return flash(request, f'Unable to save profile: {exc}', 'danger')


@app.post('/profiles/delete')
def delete_profile(request: Request, profile_id: str = Form(...)):
    try:
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            for mid, miner in config['miners'].items():
                if profile_id in miner.get('allowed_profiles', []):
                    raise SwitcherError(f'Profile is still assigned to miner {mid}.')
            config['profiles'].pop(profile_id, None)
            save_config(config_path, config)
        return flash(request, f'Profile {profile_id} deleted.', 'warning')
    except Exception as exc:
        return flash(request, f'Unable to delete profile: {exc}', 'danger')


@app.get('/coins', response_class=HTMLResponse)
def coins_page(request: Request):
    _, config, _ = controller_for(request)
    return template(request, 'coins.html', {
        'title': 'Coins', 'active': 'coins', 'coins': config['coins'],
    })


@app.post('/coins/save')
def save_coin(
    request: Request, original_symbol: str = Form(''), symbol: str = Form(...),
    coingecko_id: str = Form(...), block_reward_coins: str = Form(...),
    block_time_seconds: str = Form(...), network_provider: str = Form('static'),
    network_hashrate_hs: str = Form(''), network_url: str = Form(''),
    json_path: str = Form(''), multiplier: str = Form('1'), rpc_method: str = Form('getnetworkhashps'),
    rpc_username_env: str = Form(''), rpc_password_env: str = Form(''), enabled: str | None = Form(None),
):
    try:
        symbol = clean_text(symbol).upper()
        if not symbol:
            raise SwitcherError('Coin symbol is required.')
        network: dict[str, Any] = {'provider': network_provider}
        if network_provider == 'static':
            network['network_hashrate_hs'] = as_float(network_hashrate_hs, 'Network hashrate', 0.000001)
        elif network_provider == 'http_json':
            network.update({'url': clean_text(network_url), 'json_path': clean_text(json_path), 'multiplier': as_float(multiplier, 'Multiplier', 0.000001)})
        elif network_provider == 'json_rpc':
            network.update({'url': clean_text(network_url), 'method': clean_text(rpc_method) or 'getnetworkhashps', 'params': [120], 'username_env': clean_text(rpc_username_env), 'password_env': clean_text(rpc_password_env)})
        else:
            raise SwitcherError('Unsupported network provider.')
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            if original_symbol and original_symbol != symbol:
                config['coins'].pop(original_symbol, None)
                for profile in config['profiles'].values():
                    if profile.get('coin') == original_symbol:
                        profile['coin'] = symbol
            config['coins'][symbol] = {
                'enabled': enabled == 'on',
                'price': {'provider': 'coingecko', 'coingecko_id': clean_text(coingecko_id)},
                'network': network,
                'block_reward_coins': as_float(block_reward_coins, 'Block reward', 0.000001),
                'block_time_seconds': as_float(block_time_seconds, 'Block time', 0.000001),
            }
            Controller(config, load_state(app_paths(request)[1]), app_paths(request)[1], True).validate()
            save_config(config_path, config)
        return flash(request, f'Coin {symbol} saved.')
    except Exception as exc:
        return flash(request, f'Unable to save coin: {exc}', 'danger')


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request):
    _, config, state = controller_for(request)
    return template(request, 'settings.html', {
        'title': 'Settings', 'active': 'settings', 'config': config, 'state': state,
    })


@app.post('/settings/save')
def save_settings(
    request: Request, electricity_usd_kwh: str = Form(...), minimum_advantage_percent: str = Form(...),
    minimum_net_improvement_usd_day: str = Form(...), confirmation_checks: str = Form(...),
    minimum_runtime_hours: str = Form(...), maximum_switches_per_24h: str = Form(...),
    dry_run: str | None = Form(None), adopt_best_when_current_unknown: str | None = Form(None),
):
    try:
        config_path, _, _ = app_paths(request)
        with LOCK:
            config = load_yaml(config_path)
            config['general']['electricity_usd_kwh'] = as_float(electricity_usd_kwh, 'Electricity rate', 0)
            config['general']['dry_run'] = dry_run == 'on'
            rules = config['general'].setdefault('switching', {})
            rules.update({
                'minimum_advantage_percent': as_float(minimum_advantage_percent, 'Minimum advantage', 0),
                'minimum_net_improvement_usd_day': as_float(minimum_net_improvement_usd_day, 'Minimum net improvement', 0),
                'confirmation_checks': as_int(confirmation_checks, 'Confirmation checks', 1),
                'minimum_runtime_hours': as_float(minimum_runtime_hours, 'Minimum runtime', 0),
                'maximum_switches_per_24h': as_int(maximum_switches_per_24h, 'Maximum switches', 1),
                'adopt_best_when_current_unknown': adopt_best_when_current_unknown == 'on',
            })
            save_config(config_path, config)
        return flash(request, 'Settings saved.')
    except Exception as exc:
        return flash(request, f'Unable to save settings: {exc}', 'danger')


@app.get('/config', response_class=HTMLResponse)
def config_page(request: Request):
    config_path, _, _ = app_paths(request)
    raw = config_path.read_text(encoding='utf-8') if config_path.exists() else ''
    return template(request, 'config.html', {
        'title': 'Raw Configuration', 'active': 'config', 'raw_config': raw,
    })


@app.post('/config/save')
def config_save(request: Request, raw_config: str = Form(...)):
    try:
        parsed = yaml.safe_load(raw_config)
        if not isinstance(parsed, dict):
            raise SwitcherError('Top-level YAML must be a mapping.')
        config_path, state_path, _ = app_paths(request)
        Controller(parsed, load_state(state_path), state_path, True).validate()
        with LOCK:
            if config_path.exists():
                stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
                shutil.copy2(config_path, config_path.with_name(f'{config_path.name}.{stamp}.bak'))
            config_path.write_text(raw_config, encoding='utf-8')
        return flash(request, 'Raw configuration validated and saved.')
    except Exception as exc:
        return flash(request, f'Configuration not saved: {exc}', 'danger')


@app.get('/logs', response_class=HTMLResponse)
def logs_page(request: Request, lines: int = 300):
    _, _, log_path = app_paths(request)
    content = ''
    if log_path.exists():
        content = '\n'.join(log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-max(20, min(lines, 2000)):])
    return template(request, 'logs.html', {
        'title': 'Logs', 'active': 'logs', 'log_content': content, 'lines': lines,
    })


@app.get('/health')
def health(request: Request):
    config_path, state_path, _ = app_paths(request)
    try:
        config = load_yaml(config_path)
        Controller(config, load_state(state_path), state_path, True).validate()
        return {'status': 'ok', 'time': now().isoformat()}
    except Exception as exc:
        return JSONResponse({'status': 'error', 'error': str(exc)}, status_code=500)


def main() -> None:
    parser = argparse.ArgumentParser(description='Bitaxe Profit Switcher web interface')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--state', default=str(DEFAULT_STATE))
    parser.add_argument('--log', default=str(DEFAULT_LOG))
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    app.state.config_path = Path(args.config)
    app.state.state_path = Path(args.state)
    app.state.log_path = Path(args.log)
    uvicorn.run('webapp:app', host=args.host, port=args.port, reload=args.reload, app_dir=str(APP_DIR))


app.state.config_path = DEFAULT_CONFIG
app.state.state_path = DEFAULT_STATE
app.state.log_path = DEFAULT_LOG

if __name__ == '__main__':
    main()
