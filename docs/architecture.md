# zt-resmp architecture

## Philosophy

Truthful reachability outranks convenience. The official ZeroTier daemon owns
the protocol and network state; this CLI observes the daemon, kernel, peers,
and optional Tailscale state, and makes every local configuration mutation
explicit and reversible.

## Ownership

| Boundary | Owner |
|---|---|
| ZeroTier protocol and daemon | Official ZeroTier distribution |
| CLI checks, profiles, Central reads | This repository |
| Tailscale configuration | Official Tailscale CLI |
| Private deployment configurations | Each operator; never this repository |

## Open choices

| Topic | Current position | Decision gate |
|---|---|---|
| Packaging | Source repository and wheel; no Homebrew formula | Sustained demand and a maintainer for the formula |
| Tailscale integration | Read-only state/route checks | An explicit decision to support configuration or remediation |
| Central write operations | Not implemented | Strong demand plus a narrowly scoped API contract |
| Desktop tray integration | Not part of this repository | A separately documented desktop distribution case |

## Work log

### 2026-10-25: Initial public CLI

Created a standalone MIT-licensed Python CLI from the internal operational
layer without copying ZeroTier source code. It supports daemon/kernel checks,
real peer probes, Tailscale separation checks, Legacy/New Central reads, and
dry-run atomic local profiles. Public examples use placeholders only.
The initial package is `zt-resmp 0.1.0`, with no runtime Python dependencies.

Tracked public evidence: `evidence/2026-10-25-public-baseline.md`.
