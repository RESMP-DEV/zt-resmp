#!/usr/bin/env python3
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "build/reports/public-check"
SCOPE = (
    "Checks public repository structure, lint, unit behavior, CLI metadata, "
    "and wheel packaging; it does not probe a live network or Central account."
)


def structure_check() -> int:
    expected = (
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "LICENSE",
        ROOT / "README.md",
        ROOT / "docs/architecture.md",
        ROOT / "evidence/2026-10-25-public-baseline.md",
        ROOT / "schemas/config.schema.json",
        ROOT / "src/zt_resmp/__init__.py",
    )
    defects = [str(path.relative_to(ROOT)) for path in expected if not path.is_file()]
    claude = ROOT / "CLAUDE.md"
    agents = ROOT / "AGENTS.md"
    if not claude.is_symlink() or claude.resolve() != agents.resolve():
        defects.append("CLAUDE.md -> AGENTS.md")
    if defects:
        print(f"structure defects: {', '.join(defects)}", file=sys.stderr)
        return 1
    try:
        json.loads((ROOT / "schemas/config.schema.json").read_text(encoding="utf-8"))
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        if pyproject["project"]["name"] != "zt-resmp":
            raise ValueError("unexpected package name")
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"metadata defect: {exc}", file=sys.stderr)
        return 1
    return 0


def source_state() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return revision.stdout.strip() or "uncommitted", bool(status.stdout.strip())


def run_check(
    records: list[dict[str, object]], command: list[str], environment: dict[str, str]
) -> bool:
    print(f"==> {' '.join(command)}", file=sys.stderr)
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    elapsed = time.monotonic() - started
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    log_path = REPORT / f"check-{len(records)}.log"
    log_path.write_text(
        f"$ {' '.join(command)}\n{completed.stdout}\n{completed.stderr}",
        encoding="utf-8",
    )
    records.append(
        {
            "command": " ".join(command),
            "exit": completed.returncode,
            "elapsed": round(elapsed, 6),
            "log": str(log_path.relative_to(ROOT)),
        }
    )
    return completed.returncode == 0


def write_receipt(
    records: list[dict[str, object]],
    passed: bool,
    started: float,
    revision: str,
    dirty: bool,
) -> None:
    receipt = {
        "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
        "platform": platform.platform(),
        "source_revision": revision,
        "dirty": dirty,
        "scope": SCOPE,
        "checks": records,
        "passed": passed,
        "elapsed": round(time.monotonic() - started, 6),
    }
    (REPORT / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    receipt_path = REPORT / "receipt.json"
    receipt_path.unlink(missing_ok=True)
    started = time.monotonic()
    revision, dirty = source_state()
    records: list[dict[str, object]] = []
    environment = os.environ.copy()
    environment["PYTHONPATH"] = f"{ROOT / 'src'}:{ROOT / 'scripts'}"

    structure_exit = structure_check()
    records.append({"command": "structure_check", "exit": structure_exit, "elapsed": 0.0})
    passed = structure_exit == 0

    commands = [
        ["python3", str(ROOT / "scripts/public_data_check.py")],
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["python3", "-m", "unittest", "scripts.tests.test_check_zt_resmp", "-v"],
        ["python3", "-m", "unittest", "scripts.tests.test_public_data_check", "-v"],
        ["ruff", "check", "src", "tests", "scripts"],
        [
            "python3",
            "-c",
            "from zt_resmp import __version__; assert __version__ == '0.1.0'",
        ],
        ["uv", "build", "--offline"],
    ]
    try:
        for command in commands:
            passed = run_check(records, command, environment) and passed
    except BaseException as exc:
        records.append({"command": "dispatcher", "exit": 1, "error": str(exc)})
        passed = False
    finally:
        write_receipt(records, passed, started, revision, dirty)
    print(f"receipt: {receipt_path}", file=sys.stderr)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
