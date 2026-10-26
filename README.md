# zt-resmp

`zt-resmp` is a dependency-free operational CLI for people running the official
[ZeroTier](https://www.zerotier.com/) daemon on modern macOS and Ubuntu Linux.
It distinguishes three states that are easy to conflate:

1. the local ZeroTier HTTP control plane is responding;
2. the operating-system interface and route exist;
3. an overlay peer actually answers a real ICMP probe.

A daemon that reports `ONLINE` and a network that reports `OK` are useful
evidence, but neither alone proves reachability.

This project is an independent operational tool. It is not affiliated with,
endorsed by, or supported by ZeroTier, Inc. It does not distribute ZeroTier
source or binaries and does not replace the official daemon.

## What it checks

- expected node and network identity;
- daemon version and online state;
- network status and port errors;
- assigned managed addresses;
- interface presence and state;
- managed kernel route selection;
- `allowGlobal`, `allowDefault`, and `allowDNS` boundaries;
- configured peer presence, versions, paths, and latency;
- actual overlay and physical-path ICMP probes;
- Tailscale backend state, route overlap, exit-node use, and route-all state;
- Legacy Central and New Central read-only API responses.

The `doctor` command is read-only unless you explicitly add `--probe-peers`.
Peer probing sends ICMP traffic to the addresses in your configuration.

## Install

Python 3.11 or newer is required. The package has no Python dependencies.

```bash
git clone https://github.com/RESMP-DEV/zt-resmp.git
cd zt-resmp
python -m venv .venv
. .venv/bin/activate
python -m pip install .
zt-resmp --version
```

`uv`, `pipx`, or another isolated Python installer also works. The official
ZeroTier daemon and `zerotier-cli` must already be installed and running.
Tailscale checks require the official `tailscale` CLI, but Tailscale is
optional.

## Configure

Copy `examples/macos.example.json` or `examples/ubuntu.example.json`, then
replace every placeholder with values from your own deployment:

```bash
zt-resmp doctor --config ./my-network.json
zt-resmp doctor --config ./my-network.json --probe-peers
zt-resmp doctor --config ./my-network.json --probe-peers --probe-underlay
zt-resmp peers --config ./my-network.json
```

Set `expected_route` to a managed route announced by your ZeroTier network.
If you omit it, `doctor` uses the first managed route reported by the daemon
for the selected network.

The complete configuration shape is documented in
[`schemas/config.schema.json`](schemas/config.schema.json).

Set `tailscale_forbidden_prefixes` to routes that Tailscale must not capture,
typically your ZeroTier managed route and any physical LAN prefix that must
stay on the physical adapter. Tailscale is never configured or modified by
this project.

If Tailscale is not installed, it is reported but does not fail `doctor` unless
your configuration sets `require_tailscale: true`.

## Safe local profiles

`profile show` is always a dry run:

```bash
zt-resmp profile show --config ./my-network.json
```

`profile apply --write` merges the configured `profile` object into ZeroTier's
`local.conf`, creates a timestamped mode-0600 backup, and writes atomically:

```bash
sudo zt-resmp profile apply --config ./my-network.json --write
```

Add `--restart` only when you intentionally want the ZeroTier service restarted
after writing. A restart can interrupt active overlay connections. A clean
service restart is generally safer than forcibly killing the daemon.
On macOS, the explicit restart path uses launchd boot-out/bootstrap rather than
forcing a signal through `launchctl kickstart -k`.

`--replace` writes only the keys in your configured profile and removes keys
that are absent from it. It is deliberate and should be reviewed with `profile
show` first.

Restore a backup explicitly:

```bash
sudo zt-resmp profile restore /path/to/local.conf.bak-... --write
```

Keep backups outside a synchronized directory if they contain deployment
secrets or private routing policy.

## Central API reads

For Legacy Central:

```bash
export ZEROTIER_CENTRAL_TOKEN='<your Legacy Central API token>'
zt-resmp central --network-id '<network-id>'
```

For New Central:

```bash
export ZEROTIER_CENTRAL_TOKEN='<your New Central service-account token>'
zt-resmp central --new --org-id '<org-id>' --network-id '<network-id>'
```

Tokens are accepted only from the `ZEROTIER_CENTRAL_TOKEN` environment
variable; they are deliberately not accepted as command arguments to avoid
shell history exposure. This command performs HTTP `GET` requests only.

Legacy Central and New Central are separate authorities. Do not assume a
network visible in one is visible in the other.

## Exit codes

- `0`: command completed and all checks passed;
- `1`: operational command failed or input was invalid;
- `2`: command completed, but at least one health check failed.

## Development

```bash
python -m unittest discover -s tests -v
ruff check src tests scripts
python scripts/check_zt_resmp.py
```

The dispatcher writes a machine receipt to
`build/reports/public-check/receipt.json`. All verification is local.
