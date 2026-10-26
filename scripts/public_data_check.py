#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "literal network identifier": re.compile(r"network_id\s*:\s*['\"][0-9a-fA-F]{16}['\"]"),
    "literal node identifier": re.compile(
        r"(?:expected_node_id|node_id)\s*:\s*['\"][0-9a-fA-F]{10}['\"]"
    ),
    "email address": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "assigned credential": re.compile(
        r"(?:token|secret|password)\s*[:=]\s*['\"][^'\"<>]{8,}['\"]",
        re.IGNORECASE,
    ),
    "agent source marker": re.compile(
        r"(?:nonfree/controller|MacEthernetTap|ZeroTierOne\.cpp|LICENSE-MPL)"
    ),
    "machine-local path": re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+/"),
}


def candidate_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip())
    return [ROOT / line for line in completed.stdout.splitlines() if line]


def public_data_check() -> int:
    try:
        files = candidate_files()
    except RuntimeError as exc:
        print(f"cannot enumerate files: {exc}", file=sys.stderr)
        return 1
    findings: list[str] = []
    scanner_files = {
        ROOT / "scripts/public_data_check.py",
        ROOT / "scripts/tests/test_public_data_check.py",
    }
    for path in files:
        if not path.is_file():
            continue
        if path in scanner_files:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for label, pattern in FORBIDDEN.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        print("private-data findings:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(public_data_check())
