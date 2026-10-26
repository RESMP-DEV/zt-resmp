# zt-resmp agent instructions

## Contract

- This is an independent public CLI for machines already running the official
  ZeroTier daemon; it is not a ZeroTier source fork or daemon replacement.
- Keep the runtime dependency list empty.
- Never place a private fleet configuration, API token, account identifier,
  real production node/network identifier, or internal evidence receipt in this
  repository.
- Health checks are read-only unless the command explicitly documents a
  mutation. Profile writes must remain dry-run by default, atomic, and backed
  up.
- Tailscale is inspected, never configured.
- Preserve Legacy Central and New Central as separate authorities. API tokens
  come from `ZEROTIER_CENTRAL_TOKEN`, never command-line arguments.
- A control-plane `ONLINE` response is not reachability evidence until kernel
  interface/route state and a real peer probe agree.

## Verification

Run `python scripts/check_zt_resmp.py` for every change. It checks repository
structure, Ruff, unit tests, CLI help/version, package metadata, and a wheel
build, then writes an ignored receipt under `build/reports/public-check/`.
Do not enable GitHub Actions; all acceptance is local.

Before publication, run a private-data scan for production ZeroTier IDs,
overlay addresses used by RESMP, account emails, token names, and internal
evidence paths.
