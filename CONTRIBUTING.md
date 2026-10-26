# Contributing to zt-resmp

Keep changes small, dependency-free, and operational. Prefer checking state
and making mutations explicit over adding a second network control plane.

## Required checks

| Change | Required local check |
|---|---|
| Python implementation | `python scripts/check_zt_resmp.py` |
| Tests only | Same dispatcher |
| Packaging/metadata | Same dispatcher plus `uv build` review |
| Documentation | Same dispatcher plus private-data scan |

The dispatcher must leave `build/reports/public-check/receipt.json`. It is
ignored machine state, not durable evidence.

## Work records

Append factual entries to `docs/architecture.md`. A dated public evidence file
under `evidence/` may record generic verification commands and non-claims, but
must not expose private networks, tokens, accounts, or customer infrastructure.

## Style

- Python 3.11+, modern builtin generics, type hints on public functions.
- Ruff line length 100.
- No runtime dependencies.
- CLI output is JSON on stdout; errors are JSON on stderr.
- Exit `2` for completed but failing health checks.
