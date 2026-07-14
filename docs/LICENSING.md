# Aleph Licensing

The model in plain words: **Aleph is free and open source, for everyone.**

As of 2026-07-05, Aleph is licensed under the
[Apache License 2.0](../LICENSE) — an OSI-approved open-source license.
Every feature is included: the complete single-project experience *and* the
workspace/collaboration layer (`aleph workspace build` / `aleph workspace
status` and the `aleph_workspace_search` / `aleph_workspace_brief` /
`aleph_workspace_status` MCP tools). There are:

- **no paid tiers** and no seat licenses,
- **no license files** and no license checks anywhere in the code paths,
- **no telemetry** and no phone-home,
- no difference between what individuals, small teams, and large
  organizations may use.

## Why Apache-2.0?

Apache-2.0 was chosen over MIT for its **express patent grant** (Section 3
of the License), which composes cleanly with the pending patent
applications described in [NOTICE](../NOTICE): every user receives a patent
license from the contributors for the covered methods as embodied in this
software.

## History

- **≤ 0.5.0** — dual-licensed: MIT for the single-project experience, with
  the workspace layer and licensing code as commercial components. Prior
  MIT releases remain MIT for anyone who already obtained them.
- **2026-06-18** — an interim relicense to PolyForm Small Business 1.0.0
  was prepared but **never shipped in a tagged release**.
- **2026-07-05** — Apache-2.0, everything free. The workspace license gate
  and the license-validation machinery were removed from the codebase
  entirely.
