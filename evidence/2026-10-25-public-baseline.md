# 2026-10-25 public baseline

## Scope

This receipt records the initial public repository baseline. It does not prove
reachability for any private network and does not include private fleet data.

## Verification

- `python scripts/check_zt_resmp.py`: repository structure, Ruff, unit tests,
  CLI help/version, package metadata, and wheel build.
- `uv build`: source and wheel artifacts.
- Wheel SHA-256:
  `39841ba65ad1ca1ef96010da4ef852918f88e9020471a6fcdc91819f8fb0dbda`.
- Source-distribution SHA-256:
  `da14b28d07eaca08c7eaf40ba712733f83d03d6b5bf326b6a1a06cd0bd5b8afa`.
- Installed the wheel into a clean Python 3.12 environment; both
  `zt-resmp --version` and `python -m zt_resmp --version` returned
  `0.1.0`.
- The dispatcher public-data check passed, followed by a separate local scan
  for production ZeroTier IDs, private overlay/physical addresses, account
  identities, internal interface names, machine-local paths, and ZeroTier
  source markers.

## Non-claims

- No official ZeroTier daemon is bundled, replaced, installed, or endorsed.
- No Tailscale daemon is required or configured.
- No Central API token is stored or used by repository checks.
- macOS and Ubuntu are the supported targets; other Unix-like systems are
  untested.
- The wheel execution proof does not exercise a live ZeroTier network.
