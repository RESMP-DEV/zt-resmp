#!/usr/bin/env python3
"""Dependency-free operational CLI for official ZeroTier agents.

The daemon remains stock. This module adds the checks and safe configuration
mechanics the stock CLI omits, while keeping every mutating command explicit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT = 8.0
__version__ = "0.1.0"


@dataclass(frozen=True)
class Peer:
    name: str
    address: str
    node_id: str | None = None


@dataclass(frozen=True)
class Config:
    network_id: str
    expected_node_id: str | None = None
    expected_address: str | None = None
    expected_interface: str | None = None
    expected_route: str = ""
    allow_global: bool = False
    allow_default: bool = False
    allow_dns: bool = False
    peers: tuple[Peer, ...] = ()
    physical_probes: tuple[Peer, ...] = ()
    profile: Mapping[str, object] | None = None
    require_tailscale: bool = False
    tailscale_forbidden_prefixes: tuple[str, ...] = ()


class ZtResmpError(RuntimeError):
    """An operational command could not complete."""


def run(argv: list[str], timeout: float = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ZtResmpError(f"{argv[0]} failed: {exc}") from exc


def cli_json(
    cli: str,
    args: list[str],
    home: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> object:
    argv = [cli]
    if home:
        argv.append(f"-D{home}")
    argv.extend(("-j", *args))
    completed = run(argv, timeout)
    if completed.returncode != 0:
        raise ZtResmpError(
            f"{' '.join(argv)} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ZtResmpError(f"invalid JSON from {' '.join(argv)}: {exc}") from exc


def parse_ping_summary(output: str) -> tuple[float | None, float | None]:
    line = next(
        (item for item in output.splitlines() if "round-trip" in item or "rtt min/avg/max" in item),
        None,
    )
    if not line:
        return None, None
    match = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)", line)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def normalize_id(value: object, label: str, length: int) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]+", value):
        raise ZtResmpError(f"{label} must contain only hexadecimal characters")
    normalized = value.lower()
    if len(normalized) != length:
        raise ZtResmpError(f"{label} must contain exactly {length} characters")
    return normalized


def normalize_host(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ZtResmpError(f"{label} must be an IP address string")
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ZtResmpError(f"{label} is not a valid IP address: {value}") from exc
    return value


def normalize_network(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ZtResmpError(f"{label} must be an IPv4 or IPv6 network string")
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as exc:
        raise ZtResmpError(f"{label} is not a valid IP network: {value}") from exc
    return str(network)


def boolean_field(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key, False)
    if not isinstance(value, bool):
        raise ZtResmpError(f"{key} must be boolean")
    return value


def peer_list(mapping: Mapping[str, object], key: str, require_node_id: bool) -> tuple[Peer, ...]:
    values = mapping.get(key, [])
    if not isinstance(values, list):
        raise ZtResmpError(f"{key} must be an array")
    peers: list[Peer] = []
    for item in values:
        if not isinstance(item, dict):
            raise ZtResmpError(f"each item in {key} must be an object")
        name = item.get("name")
        address = normalize_host(item.get("address"), f"{key} address")
        node_id_value = item.get("node_id")
        node_id = (
            normalize_id(node_id_value, f"{key} node_id", 10)
            if node_id_value is not None or require_node_id
            else None
        )
        if not isinstance(name, str) or not name.strip():
            raise ZtResmpError(f"each item in {key} needs a nonempty name")
        peers.append(Peer(name.strip(), address, node_id))
    return tuple(peers)


def config_from_mapping(mapping: Mapping[str, object]) -> Config:
    required = ("network_id",)
    missing = [key for key in required if key not in mapping]
    if missing:
        raise ZtResmpError(f"config missing: {', '.join(missing)}")

    profile = mapping.get("profile")
    if profile is not None and not isinstance(profile, dict):
        raise ZtResmpError("profile must be an object")
    network_id = normalize_id(mapping["network_id"], "network_id", 16)
    expected_node_id_value = mapping.get("expected_node_id")
    expected_address_value = mapping.get("expected_address")
    expected_route_value = mapping.get("expected_route", "")
    expected_interface_value = mapping.get("expected_interface")
    raw_forbidden = mapping.get("tailscale_forbidden_prefixes", [])
    if not isinstance(raw_forbidden, list):
        raise ZtResmpError("tailscale_forbidden_prefixes must be an array")
    forbidden = tuple(
        normalize_network(value, "tailscale_forbidden_prefixes entry") for value in raw_forbidden
    )
    if expected_interface_value is not None and not isinstance(expected_interface_value, str):
        raise ZtResmpError("expected_interface must be a string")
    return Config(
        network_id=network_id,
        expected_node_id=(
            normalize_id(expected_node_id_value, "expected_node_id", 10)
            if expected_node_id_value is not None
            else None
        ),
        expected_address=(
            normalize_host(expected_address_value, "expected_address")
            if expected_address_value is not None
            else None
        ),
        expected_interface=expected_interface_value,
        expected_route=(
            normalize_network(expected_route_value, "expected_route")
            if expected_route_value
            else ""
        ),
        allow_global=boolean_field(mapping, "allow_global"),
        allow_default=boolean_field(mapping, "allow_default"),
        allow_dns=boolean_field(mapping, "allow_dns"),
        peers=peer_list(mapping, "peers", require_node_id=False),
        physical_probes=peer_list(mapping, "physical_probes", require_node_id=False),
        profile=profile,
        require_tailscale=boolean_field(mapping, "require_tailscale"),
        tailscale_forbidden_prefixes=forbidden,
    )


def load_config(path: Path) -> Config:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ZtResmpError(f"cannot load {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ZtResmpError(f"{path} must contain a JSON object")
    return config_from_mapping(data)


def default_home() -> str:
    return (
        "/Library/Application Support/ZeroTier/One"
        if platform.system() == "Darwin"
        else "/var/lib/zerotier-one"
    )


def local_conf_path(home: str | None = None) -> Path:
    return Path(home or default_home()) / "local.conf"


def deep_merge(base: dict[str, object], patch: Mapping[str, object]) -> dict[str, object]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(dict(result[key]), value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def parse_route_interface(output: str, target: str) -> str | None:
    match = re.search(
        rf"^\s*destination:\s*{re.escape(target)}\s*$.*?^\s*interface:\s*(\S+)\s*$",
        output,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def route_interface(target: str, system: str | None = None) -> str | None:
    system = system or platform.system()
    if system == "Darwin":
        completed = run(["/sbin/route", "-n", "get", target])
        if completed.returncode != 0:
            return None
        return parse_route_interface(completed.stdout, target)
    ip_binary = "/usr/sbin/ip" if Path("/usr/sbin/ip").exists() else "ip"
    completed = run([ip_binary, "route", "get", target])
    if completed.returncode != 0:
        return None
    match = re.search(r"\bdev\s+([^\s]+)", completed.stdout)
    return match.group(1) if match else None


def parse_ping(output: str) -> tuple[int, float | None]:
    match = re.search(r"(\d+) (?:packets )?transmitted, (\d+) (?:packets )?received", output)
    sent, received = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
    _, mean_rtt = parse_ping_summary(output)
    return received, mean_rtt


def probe_address(address: str, count: int = 5, interval: float = 0.25) -> dict[str, object]:
    argv = ["ping", "-c", str(count), "-i", str(interval)]
    argv.append("-W1000" if platform.system() == "Darwin" else "-W1")
    argv.append(address)
    completed = run(argv, timeout=max(5.0, count * max(interval, 1.0) + 5.0))
    received, mean_ms = parse_ping(completed.stdout + completed.stderr)
    sent = count
    return {
        "address": address,
        "sent": sent,
        "received": received,
        "loss_percent": round(100.0 * (sent - received) / sent, 2),
        "mean_rtt_ms": mean_ms,
        "passed": received > 0,
        "command_exit": completed.returncode,
        "error": completed.stderr.strip() or None,
    }


def parse_linux_interface_json(output: str) -> tuple[bool, list[str]]:
    try:
        rows = json.loads(output)
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return False, []
        row = rows[0]
        flags = row.get("flags", [])
        up = isinstance(flags, list) and "UP" in flags
        addresses: list[str] = []
        address_info = row.get("addr_info", [])
        if isinstance(address_info, list):
            for info in address_info:
                if isinstance(info, dict) and isinstance(info.get("local"), str):
                    addresses.append(f"{info['local']}/{info.get('prefixlen', '')}".rstrip("/"))
        return up, addresses
    except (ValueError, TypeError, AttributeError):
        return False, []


def kernel_interface(interface: str | None) -> dict[str, object]:
    if not interface:
        return {"present": False, "error": "no expected interface configured"}
    system = platform.system()
    if system == "Darwin":
        completed = run(["/sbin/ifconfig", interface])
        present = completed.returncode == 0
        flags = re.search(r"flags=[^<]*<([^>]*)>", completed.stdout)
        up = present and bool(flags and "UP" in flags.group(1).split(","))
        addresses = re.findall(r"^\s*inet\s+([0-9.]+)\s+netmask", completed.stdout, re.MULTILINE)
    else:
        ip_binary = "/usr/sbin/ip" if Path("/usr/sbin/ip").exists() else "ip"
        completed = run([ip_binary, "-j", "-br", "addr", "show", interface])
        present = completed.returncode == 0
        up, addresses = parse_linux_interface_json(completed.stdout) if present else (False, [])
    return {"interface": interface, "present": present, "up": up, "addresses": addresses}


def tailscale_state() -> dict[str, object]:
    if shutil.which("tailscale") is None:
        return {"present": False, "running": False, "error": "tailscale not installed"}
    completed = run(["tailscale", "status", "--json"], timeout=10)
    if completed.returncode != 0:
        return {
            "present": False,
            "running": False,
            "error": completed.stderr.strip() or "tailscale not installed",
        }
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"present": True, "running": False, "error": f"invalid tailscale JSON: {exc}"}
    routes = sorted(tailscale_route_prefixes(data))
    return {
        "present": True,
        "running": data.get("BackendState") == "Running",
        "backend_state": data.get("BackendState"),
        "health": data.get("Health"),
        "self_ips": data.get("Self", {}).get("TailscaleIPs", []),
        "routes": routes,
    }


def tailscale_route_prefixes(data: object) -> set[str]:
    """Read advertised/primary routes without interpreting other Tailscale state."""
    prefixes: set[str] = set()
    if isinstance(data, dict):
        for key, value in data.items():
            if key in {"AdvertisedRoutes", "PrimaryRoutes"} and isinstance(value, list):
                prefixes.update(str(item) for item in value if isinstance(item, str))
            else:
                prefixes.update(tailscale_route_prefixes(value))
    elif isinstance(data, list):
        for value in data:
            prefixes.update(tailscale_route_prefixes(value))
    return prefixes


def prefixes_overlap(left: str, right: str) -> bool:
    try:
        a = ipaddress.ip_network(left, strict=False)
        b = ipaddress.ip_network(right, strict=False)
    except ValueError:
        return False
    return a.version == b.version and a.overlaps(b)


def tailscale_prefs() -> dict[str, object]:
    if shutil.which("tailscale") is None:
        return {"present": False, "error": "tailscale not installed"}
    completed = run(["tailscale", "debug", "prefs"], timeout=10)
    if completed.returncode != 0:
        return {
            "present": False,
            "error": completed.stderr.strip() or "tailscale prefs unavailable",
        }
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"present": False, "error": f"invalid tailscale prefs JSON: {exc}"}
    return {
        "present": True,
        "exit_node_id": data.get("ExitNodeID"),
        "exit_node_ip": data.get("ExitNodeIP"),
        "route_all": data.get("RouteAll"),
        "corp_dns": data.get("CorpDNS"),
    }


def tailscale_checks(config: Config) -> tuple[dict[str, object], list[dict[str, object]]]:
    state = tailscale_state()
    if not state.get("present"):
        if not config.require_tailscale:
            return state, []
        return state, [
            {"name": "tailscale_installed", "passed": False, "error": state.get("error")}
        ]
    checks = [{"name": "tailscale_installed", "passed": True}]
    if state.get("running"):
        checks.append({"name": "tailscale_backend", "passed": True})
        advertised = set(state.get("routes", []))  # type: ignore[arg-type]
        overlaps: list[dict[str, str]] = []
        for forbidden in config.tailscale_forbidden_prefixes:
            for route in sorted(advertised):
                if prefixes_overlap(forbidden, route):
                    overlaps.append({"forbidden": forbidden, "route": route})
        checks.append(
            {
                "name": "tailscale_route_separation",
                "passed": not overlaps,
                "overlaps": overlaps,
            }
        )
        prefs = tailscale_prefs()
        if prefs.get("present"):
            checks.append(
                {
                    "name": "tailscale_exit_node_boundary",
                    "passed": not prefs.get("exit_node_id") and not prefs.get("route_all"),
                    "exit_node_id": prefs.get("exit_node_id"),
                    "route_all": prefs.get("route_all"),
                }
            )
    else:
        checks.append(
            {
                "name": "tailscale_backend",
                "passed": False,
                "backend_state": state.get("backend_state"),
            }
        )
    return state, checks


def check_network(config: Config, cli: str, home: str | None) -> list[dict[str, object]]:
    info = cli_json(cli, ["info"], home)
    networks = cli_json(cli, ["listnetworks"], home)
    if not isinstance(info, dict) or not isinstance(networks, list):
        raise ZtResmpError("ZeroTier returned an unexpected management shape")
    checks: list[dict[str, object]] = []

    node_ok = info.get("online") is True and (
        config.expected_node_id is None or info.get("address") == config.expected_node_id
    )
    checks.append(
        {
            "name": "daemon_node",
            "passed": node_ok,
            "node": info.get("address"),
            "expected": config.expected_node_id,
            "version": info.get("version"),
            "online": info.get("online"),
        }
    )

    network = next((item for item in networks if item.get("nwid") == config.network_id), None)
    network_ok = bool(network and network.get("status") == "OK" and network.get("portError") == 0)
    if network_ok and config.expected_address:
        network_ok = any(
            str(value).startswith(f"{config.expected_address}/")
            for value in network.get("assignedAddresses", [])
        )
    checks.append(
        {
            "name": "managed_network",
            "passed": network_ok,
            "status": network.get("status") if network else "missing",
            "portError": network.get("portError") if network else None,
            "address": network.get("assignedAddresses") if network else [],
            "expected_address": config.expected_address,
        }
    )

    if network_ok:
        interface = network.get("portDeviceName")
        if config.expected_interface and interface != config.expected_interface:
            network_ok = False
        kernel = kernel_interface(str(interface))
        checks.append(
            {
                "name": "kernel_interface",
                "passed": bool(kernel["present"] and kernel["up"]),
                **kernel,
            }
        )
        managed_routes = network.get("routes", [])
        expected_route = config.expected_route
        if not expected_route and isinstance(managed_routes, list) and managed_routes:
            first_route = managed_routes[0]
            if isinstance(first_route, dict):
                expected_route = str(first_route.get("target") or "")
        route_target = expected_route.split("/")[0]
        actual_interface = route_interface(route_target) if route_target else None
        route_ok = bool(route_target and actual_interface == interface)
        checks.append(
            {
                "name": "kernel_route",
                "passed": route_ok,
                "target": expected_route,
                "interface": actual_interface,
                "expected_interface": interface,
            }
        )
        network_ok = network_ok and route_ok

    flags_ok = (
        (
            network.get("allowGlobal", False) == config.allow_global
            and network.get("allowDefault", False) == config.allow_default
            and network.get("allowDNS", False) == config.allow_dns
        )
        if network
        else False
    )
    checks.append(
        {
            "name": "coexistence_flags",
            "passed": flags_ok,
            "allowGlobal": network.get("allowGlobal") if network else None,
            "allowDefault": network.get("allowDefault") if network else None,
            "allowDNS": network.get("allowDNS") if network else None,
        }
    )
    return checks


def summarize_peers(config: Config, cli: str, home: str | None) -> dict[str, object]:
    peers = cli_json(cli, ["listpeers"], home)
    if not isinstance(peers, list):
        raise ZtResmpError("ZeroTier did not return a peer array")
    wanted: dict[str, dict[str, object]] = {}
    for peer in config.peers:
        match = next(
            (item for item in peers if item.get("address") == (peer.node_id or peer.address)),
            None,
        )
        paths = match.get("paths", []) if match else []
        wanted[peer.name] = {
            "node_id": match.get("address") if match else None,
            "version": match.get("version") if match else None,
            "reported_latency_ms": match.get("latency") if match else None,
            "active_path_count": sum(bool(path.get("active")) for path in paths),
            "active_nonexpired_path_count": sum(
                bool(path.get("active")) and not path.get("expired", True) for path in paths
            ),
            "last_receive_ms_ago": (
                max(
                    (int(time.time() * 1000) - int(path.get("lastReceive", 0)) for path in paths),
                    default=None,
                )
            ),
            "present": match is not None,
        }
    return {"peers": wanted}


class SameOriginHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if new.scheme.lower() != "https" or new.netloc.lower() != old.netloc.lower():
            raise ZtResmpError(f"refusing cross-origin Central redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def central_request(
    path: str,
    token: str,
    *,
    legacy: bool = True,
    method: str = "GET",
    body: Mapping[str, object] | None = None,
    timeout: float = 15.0,
) -> object:
    base = "https://api.zerotier.com/api/v1" if legacy else "https://central.zerotier.com/api/v2"
    if not path.startswith("/") or "://" in path:
        raise ZtResmpError("Central API path must begin with / and must not be absolute")
    scheme = "token" if legacy else "Bearer"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"{scheme} {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"zt-resmp/{__version__}",
        },
    )
    opener = urllib.request.build_opener(SameOriginHTTPSRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ZtResmpError(f"Central API request failed: {exc}") from exc


def atomic_write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def backup_path(path: Path) -> Path:
    timestamp = dt.datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    return path.parent / f"{path.name}.bak-{timestamp}-{secrets.token_hex(4)}"


def apply_profile(
    config: Config,
    home: str | None,
    *,
    write: bool = False,
    replace: bool = False,
) -> dict[str, object]:
    if not config.profile:
        raise ZtResmpError("profile is empty")
    path = local_conf_path(home)
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ZtResmpError(f"cannot read {path}: {exc}") from exc
    merged = (
        dict(config.profile)
        if replace
        else deep_merge(current if isinstance(current, dict) else {}, config.profile)
    )
    rendered = json.dumps(merged, indent=2, sort_keys=True) + "\n"
    backup = None
    if write and current != merged:
        backup = backup_path(path)
        atomic_write(backup, path.read_text(encoding="utf-8") if path.exists() else "{}")
        atomic_write(path, rendered)
    return {
        "path": str(path),
        "write": write,
        "changed": current != merged,
        "backup": str(backup) if backup else None,
        "rendered": rendered,
    }


def restart_service(system: str | None = None) -> dict[str, object]:
    system = system or platform.system()
    if system == "Darwin":
        bootout = run(["/bin/launchctl", "bootout", "system/com.zerotier.one"], timeout=30)
        bootstrap = run(
            [
                "/bin/launchctl",
                "bootstrap",
                "system",
                "/Library/LaunchDaemons/com.zerotier.one.plist",
            ],
            timeout=30,
        )
        return {
            "system": system,
            "exit_code": bootstrap.returncode,
            "stderr": (bootout.stderr.strip() + "\n" + bootstrap.stderr.strip()).strip(),
            "mechanism": "launchctl bootout/bootstrap",
        }
    else:
        completed = run(["systemctl", "restart", "zerotier-one"], timeout=20)
        return {
            "system": system,
            "exit_code": completed.returncode,
            "stderr": completed.stderr.strip(),
            "mechanism": "systemctl restart",
        }


def restore_profile(home: str | None, restore_file: Path) -> dict[str, object]:
    target = local_conf_path(home)
    try:
        rendered = restore_file.read_text(encoding="utf-8")
        json.loads(rendered)
    except (OSError, json.JSONDecodeError) as exc:
        raise ZtResmpError(f"cannot restore {restore_file}: {exc}") from exc
    backup = None
    if target.exists():
        backup = backup_path(target)
        atomic_write(backup, target.read_text(encoding="utf-8"))
    atomic_write(target, rendered)
    return {
        "path": str(target),
        "restored_from": str(restore_file),
        "backup": str(backup) if backup else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operational CLI for official ZeroTier agents")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser(
        "doctor", help="validate daemon, kernel route, peers, and Tailscale boundary"
    )
    doctor.add_argument("--config", type=Path, required=True, help="JSON network profile")
    doctor.add_argument("--cli", default="zerotier-cli", help="ZeroTier management CLI")
    doctor.add_argument("--home", help="ZeroTier home directory")
    doctor.add_argument(
        "--probe-peers",
        action="store_true",
        help="send ICMP probes to configured overlay addresses",
    )
    doctor.add_argument(
        "--probe-underlay",
        action="store_true",
        help="send ICMP probes to configured physical addresses",
    )
    peers = sub.add_parser("peers", help="summarize configured peers and paths")
    peers.add_argument("--config", type=Path, required=True, help="JSON network profile")
    peers.add_argument("--cli", default="zerotier-cli", help="ZeroTier management CLI")
    peers.add_argument("--home", help="ZeroTier home directory")
    sub.add_parser("tailscale", help="show Tailscale state without changing it")

    central = sub.add_parser(
        "central", help="read Legacy Central (token auth) or New Central (bearer auth)"
    )
    central.add_argument(
        "--new", action="store_true", help="target New Central instead of Legacy Central"
    )
    central.add_argument("--path", default="/network", help="API path beginning with /")
    central.add_argument("--network-id", help="optional /network/{id} shortcut")
    central.add_argument(
        "--org-id",
        help="New Central organization ID; appended as the org-id query parameter",
    )

    profile = sub.add_parser("profile", help="show, dry-run, apply, or restore local.conf")
    profile.add_argument("action", choices=("show", "apply", "restore"))
    profile.add_argument("restore_file", nargs="?", type=Path)
    profile.add_argument("--config", type=Path, help="JSON profile; required for show and apply")
    profile.add_argument("--home", help="ZeroTier home directory")
    profile.add_argument("--write", action="store_true", help="actually apply or restore")
    profile.add_argument(
        "--replace",
        action="store_true",
        help="replace local.conf instead of merging; removes keys absent from the profile",
    )
    profile.add_argument("--restart", action="store_true", help="restart service after --write")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "central":
            token = os.environ.get("ZEROTIER_CENTRAL_TOKEN")
            if not token:
                raise ZtResmpError(
                    "set ZEROTIER_CENTRAL_TOKEN; tokens are not accepted as arguments"
                )
            path = f"/network/{args.network_id}" if args.network_id else args.path
            if args.new and args.org_id:
                separator = "&" if "?" in path else "?"
                path = f"{path}{separator}org-id={urllib.parse.quote(args.org_id, safe='')}"
            result = central_request(path, token, legacy=not args.new)
        else:
            if args.command == "profile" and args.action != "restore" and not args.config:
                raise ZtResmpError(f"profile {args.action} requires --config")
            if args.command != "profile" and not args.config:
                raise ZtResmpError(f"{args.command} requires --config")
            config = (
                load_config(args.config)
                if args.command != "profile" or args.action != "restore"
                else None
            )
            if args.command == "doctor":
                checks = check_network(config, args.cli, args.home)
                peer_summary = summarize_peers(config, args.cli, args.home)
                if args.probe_peers:
                    overlay_probes = [probe_peer_value(peer) for peer in config.peers]
                    peer_summary["probes"] = overlay_probes
                    checks.extend(
                        {
                            "name": f"overlay_probe:{probe['name']}",  # type: ignore[index]
                            "passed": probe["passed"],  # type: ignore[index]
                            **probe,  # type: ignore[arg-type]
                        }
                        for probe in overlay_probes
                    )
                if args.probe_underlay:
                    underlay_probes = [probe_peer_value(peer) for peer in config.physical_probes]
                    checks.extend(
                        {
                            "name": f"underlay_probe:{probe['name']}",  # type: ignore[index]
                            "passed": probe["passed"],  # type: ignore[index]
                            **probe,  # type: ignore[arg-type]
                        }
                        for probe in underlay_probes
                    )
                ts, tailscale_checks_result = tailscale_checks(config)
                checks.extend(tailscale_checks_result)
                result = {
                    "command": "doctor",
                    "config": str(args.config),
                    "checks": checks,
                    **peer_summary,
                    "tailscale": ts,
                    "passed": all(bool(item.get("passed")) for item in checks),
                }
            elif args.command == "peers":
                result = {"command": "peers", **summarize_peers(config, args.cli, args.home)}
            elif args.command == "profile":
                if args.action == "restore":
                    if not args.restore_file:
                        raise ZtResmpError("profile restore requires a backup path")
                    if not args.write:
                        raise ZtResmpError("restore requires --write as a deliberate mutation")
                    result = restore_profile(args.home, args.restore_file)
                    if args.restart:
                        result["restart"] = restart_service()
                else:
                    if args.action == "show" and (args.write or args.replace or args.restart):
                        raise ZtResmpError(
                            "profile show is read-only and cannot take mutation flags"
                        )
                    result = apply_profile(
                        config,
                        args.home,
                        write=args.write,
                        replace=args.replace,
                    )
                    if args.restart:
                        if not args.write:
                            raise ZtResmpError("--restart requires --write")
                        result["restart"] = restart_service()
                if args.restart and isinstance(result.get("restart"), dict):
                    restart_result = result["restart"]
                    if restart_result.get("exit_code") != 0:
                        result["passed"] = False
            else:
                raise ZtResmpError(f"unsupported command: {args.command}")

        payload = result if isinstance(result, dict) else {"result": result}
        payload["ok"] = payload.get("passed", True) is not False
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["ok"] else 2
    except (ZtResmpError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1


def probe_peer_value(peer: Peer) -> dict[str, object]:
    result = probe_address(peer.address)
    result["name"] = peer.name
    return result
