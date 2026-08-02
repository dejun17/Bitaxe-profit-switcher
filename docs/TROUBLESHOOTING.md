# Troubleshooting

## Service status

```bash
systemctl status bitaxe-switcher-web.service --no-pager
journalctl -u bitaxe-switcher-web.service -n 100 --no-pager
```

## Web health

```bash
curl -i http://127.0.0.1:8088/health
ss -lntp | grep 8088
```

## AxeOS connectivity

```bash
ping -c 3 192.168.50.79
curl -sS http://192.168.50.79/api/system/info | jq
```

## `No route to host` for port 8332

The configured Bitcoin Core RPC host is unreachable. Correct the IP/routing/firewall or use a static network-hashrate source until the node exists.

## Invalid systemd unit name

Type only the service name. Do not copy tree-drawing characters from `systemctl status` output:

```bash
systemctl restart bitaxe-switcher-web.service
```

## Configuration backup

```bash
cp /etc/bitaxe-switcher/config.yaml /etc/bitaxe-switcher/config.yaml.bak
```
