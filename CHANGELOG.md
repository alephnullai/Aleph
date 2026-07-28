# Changelog

All notable changes to Aleph are documented here.

## [1.2.1] — 2026-07-28

### Fixed — C++ regression introduced in 1.2.0

- **Nested definitions inside a C++ field declaration are no longer dropped.**
  1.2.0 added `field_declaration` to the GLOBAL leaf-symbol set as a Java
  optimization, but C++ maps that node type too — and a C++ field can legally
  contain a definition:

  ```cpp
  struct Outer {
      struct Inner { int a; } nested;
      enum Mode { FAST, SLOW } mode;
  };
  ```

  is a `field_declaration` wrapping a `struct_specifier`. Treating it as a leaf
  stopped the walk there, silently dropping `Inner`, its members, the inline
  `enum`, and its enumerators — five symbols in that four-line example. The leaf
  set is now language-keyed, so the optimization applies to Java, where a field
  cannot nest a definition, and nowhere else.

  The committed C++ corpus contains no such shape, so a full-suite run and a
  corpus-wide A/B both showed no difference — the case had to be constructed to
  see it. It is now a committed regression test.

- **Call-graph `name`-field resolution is gated to `method_invocation`.**
  It happened to be a no-op elsewhere only because every other shipped grammar
  names its callee field `function` (or `constructor` for TS `new_expression`).
  That is a property of today's grammars, not a guarantee; the gate removes the
  cross-language blast radius.

## [1.2.0] — 2026-07-28

### Added — Java support

- **Java is now a first-class language** (`.java`), implementing
  [alephnullai/Aleph#1](https://github.com/alephnullai/Aleph/issues/1) — classes,
  interfaces, enums, records, `@interface` declarations and their elements,
  methods, constructors, fields, enum constants, imports and packages.
- **`static final` fields classify as CONSTANT**, not VARIABLE. The node type
  alone cannot separate `private int count;` from
  `private static final int MAX = 100;`; the refinement reads modifiers.
- **The package scopes its compilation unit.** Java's `package` is a file-level
  sibling statement, not a container node, so mapping it to MODULE does not by
  itself qualify anything — every class would land as a bare `Account`, and two
  same-named classes in different packages would be indistinguishable to the
  call-graph resolver, which disambiguates on scope.
- **Constructor calls are call-graph edges.** `new Foo()` targets an extracted
  symbol; without `object_creation_expression` every Java constructor looked
  uncalled. `this(...)`/`super(...)` is deliberately not mapped — its callee is
  a bare keyword that can never resolve.
- **Call resolution reads the `name` field.** Java's `method_invocation` is
  `[object, ".", name, arguments]`, so the previous positional scan resolved
  `lines.size()` to the *receiver* — a confidently wrong edge rather than a
  missing one. No other language is affected: cpp/rust/python/ts all name that
  field `function`.

Known limitation, pinned by a test: `int a, b;` is one `field_declaration` with
two declarators, and the extractor's contract is one symbol per node, so `a` is
indexed and `b` is dropped — the same shape C++ already has.

Verified with 22 regression tests over a committed corpus
(`tests/fixtures/java/extraction_patterns.java`), a full suite of 1282 passed /
3 skipped, and an end-to-end `aleph build` on a Java project.

### Fixed

- **`mcp` pinned to `<2`.** mcp 2.0.0 (released 2026-07-28) removed
  `mcp.server.fastmcp`, which `aleph/mcp/server.py` imports at module scope.
  With the dependency unpinned, every fresh `pip install aleph-compiler`
  resolved 2.x and the MCP server failed to import — including installs of the
  already-published 1.1.3. Migrating to the 2.x API is deliberately left as its
  own future change.

### Note

1.1.4 was tagged in the changelog but never published to PyPI; its docs-only
patent-claim scrub ships here.

## [1.1.4] — 2026-07-23

### Changed
- Removed all live "Patent Pending" / "pending patent applications" claims from
  the ship set (README, README.public, NOTICE, CONTRIBUTING, docs/LICENSING,
  packaging metadata) — no patent applications were filed. Apache-2.0's §3
  express patent grant applies as ever. No code changes.

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
  *(Correction 2026-07-23: no patent applications were ever filed; the
  pending-applications language above was inaccurate when written. The
  Apache-2.0 §3 grant stands on its own. See [1.1.4].)*
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
