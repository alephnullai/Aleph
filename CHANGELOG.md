# Changelog

All notable changes to Aleph are documented here.

## [Unreleased] — 1.1.0 (first public launch, targeted week of 2026-07-21)

### Changed

- **Version set to 1.1.0 for the first Apache-2.0 public launch.** This aligns
  the package version with the public release line (the prior public tag was
  `v1.0.1`); the `0.6.x` series was the internal development line following the
  Apache-2.0 relicense. Publishing a `0.6.0` snapshot would have appeared to
  regress the public version.
- Release tooling hardened for a clean public snapshot: a privacy (PII) gate now
  runs before any public push, and the `.releaseignore` strip is robust to CRLF
  checkouts. Public README scrubbed of stale commercial/pricing language to match
  the Apache-2.0, free-for-everyone model.

## 0.6.0 — 2026-07-05

### Changed

- **License changed to Apache-2.0 — free and open source, for everyone.**
  All features are included, with no paid tiers, no seat licenses, and no
  license checks anywhere in the code paths. Apache-2.0 was chosen over MIT
  for its express patent grant, which composes with the pending patent
  applications described in NOTICE. SPDX identifier: `Apache-2.0`.
- **Prior MIT releases remain under the MIT License.** Anyone who already
  obtained an MIT-licensed version (≤ 0.5.0) keeps those MIT rights for
  that copy; the license change applies from 0.6.0 onward.
- An interim relicense to PolyForm Small Business 1.0.0 (2026-06-18) was
  prepared but **never shipped in a tagged release** — Apache-2.0 is the
  operative change from MIT.

### Removed

- The workspace team-tier license gate (`require_team_license` and the
  `aleph.licensing` package), the license key-generation tooling, and
  `COMMERCIAL-LICENSE.md`. The `aleph workspace ...` commands and
  `aleph_workspace_*` MCP tools now run for everyone, ungated.
