# Changelog

All notable changes to Aleph are documented here.

## [1.1.3] — 2026-07-18

### Fixed — C++ symbol extraction

- **Reference-return methods are no longer dropped.** `const Position& location() const`
  nests its `function_declarator` under a `reference_declarator` as a bare child rather
  than under the `declarator` field, so the name walk missed it. The defensive fallback
  now recurses through any `*declarator*` child.
- **Out-of-line qualified definitions keep their class qualifier.** `Depot::maintenanceCost`
  was indexed as just `maintenanceCost` because a `qualified_identifier` name leaf hit the
  bare `name`-field branch first; the leaf branch is now checked before the `name` field.
- **Declaration-only destructors (`~S();`) are now emitted.** A bodyless destructor parses
  as a bare `declaration` node with no `SymbolKind`; when a cpp `declaration` resolves to no
  kind, the declarator chain is walked and a `function_declarator`/`destructor_name` maps to
  FUNCTION.

These three were pinned as strict-xfail regressions in the E4 corpus (#29) and now enforce
as passing assertions (#30). Verified cross-platform: Windows (Pollux), Linux full-suite
gate (Talos), macOS (Atlas).

## [1.1.2] — 2026-07-15

### Changed

- **First PyPI publish via Trusted Publishing (OIDC).** No API tokens; a version tag drives
  an OpenID-Connect publish workflow. Cut alongside null-memory v2.2.5. No library code
  changes in this release.

## [1.1.1] — 2026-07-14

### Fixed — Windows (MCP server)

- **Git children no longer inherit the MCP server's stdin pipe.** An MCP
  server's stdin is the client's JSON-RPC channel; a git child that inherited
  it would block on Windows instead of exiting, burning its full timeout. Every
  `aleph serve` build paid a flat +5s per `git rev-parse` on Windows, which blew
  the selftest budget and produced no artifacts. All git subprocesses now use
  `stdin=subprocess.DEVNULL`. (POSIX never blocked this way, so it was invisible
  on Linux/macOS.)
- **`file://` project roots now parse on Windows.** A conforming
  `file:///C:/Users/x` URI has path `/C:/Users/x`, which is not a usable Windows
  path; the client root was silently discarded and cross-repo following was dead
  on Windows. Now resolved via `url2pathname`, including drive-as-host
  (`file://C:/x`) and UNC (`file://server/share`) forms.

Windows CI (windows-latest 3.11) is green as of this release.

## [1.1.0] — first public launch (Apache-2.0)

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
