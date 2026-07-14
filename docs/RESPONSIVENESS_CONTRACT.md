# Responsiveness Contract — Release Gate

> **We can't release a product that randomly hangs.** Every release must
> pass `aleph selftest` with exit code 0. No exceptions.

Three pre-handshake hangs shipped in one week (the -32000 handshake exit,
the PR #6 pre-handshake auto-migrate rebuild, and a silent 90-minute
pre-handshake auto-build). This contract exists so the fourth one cannot
merge, let alone ship.

## The gate

```sh
aleph selftest            # exit 0 = releasable
```

`aleph selftest` enforces two budgets over a **real** `aleph serve`
subprocess driven over stdio (no mocks):

### 1. Handshake budget — the three boot paths

`aleph serve` must answer MCP `initialize` within **10 seconds**
(`--handshake-budget`) on each of the three boot paths that have each
shipped a hang:

| boot path | what it exercises | the incident it pins |
|---|---|---|
| built project | normal startup, artifact load | baseline |
| unbuilt directory | auto-build must be deferred past the handshake | the silent 90-minute stall |
| multi-repo parent | workspace guard → degraded-but-alive server | the -32000 mid-handshake exit |

### 2. Per-tool budget — all 33 MCP tools

Every registered MCP tool is called once against a tiny pre-built fixture
project and must answer within **10 seconds** (`--budget`). The output is
a budget table (tool, seconds, budget, status).

* **TIMEOUT** — no answer within budget. The hung tool is **abandoned**:
  its server is killed, a fresh one is spawned, and the run continues, so
  one hang never hides the status of the remaining tools. Exit nonzero.
* **FAIL** — JSON-RPC error, tool-level `isError`, or dead pipe. Exit
  nonzero.
* **DEGRADED** — server alive but serving setup instructions instead of
  real tools. Exit 2 (only reachable with `--project` pointing at a
  multi-repo parent).
* Clean run — exit 0.

### Slow CI runners

`ALEPH_SELFTEST_BUDGET_MULT=N` scales **both** budgets uniformly (e.g.
`ALEPH_SELFTEST_BUDGET_MULT=3` on a loaded shared runner). The default
budgets assume a developer laptop; the contract is the multiplied value.

## What enforces it between releases

| guard | file | what it makes unmergeable |
|---|---|---|
| Pre-handshake purity | `tests/unit/test_handshake_purity.py` | Slow/fallible work (auto_build, auto_migrate_ids, git spawns, the rebuild watcher) on the `serve()` → `run()` path. Static AST allowlist over `cli._handle_serve`, `server.serve`, `server.create_server` **plus** live `initialize` timing on the three boot paths. |
| Subprocess hygiene | `tests/unit/test_subprocess_hygiene.py` | Any child process without an authoritative wall-clock bound: `subprocess.run`-family without `timeout=`, `Popen` outside the audited hardened wrappers, `os.system`/`os.popen` outright. |
| Selftest completeness | `tests/unit/test_selftest_contract.py` | Registering an MCP tool without adding it to the selftest's per-tool budget run (and stale/phantom entries). |

## The architecture rule behind it

**Nothing slow or fallible runs before the MCP handshake.** The only
things allowed between process start and `mcp_server.run()` are bounded
local reads (license file, workspace config, artifact version header, the
`maybe_hint_migration` header compare) and in-process bookkeeping. All
slow startup work — the auto-migrate heal, the missing-artifact
auto-build, the rebuild watcher — runs on the
`aleph.mcp.server._deferred_startup` daemon thread *after* `initialize`
is answered. Tool calls that race the deferred build get an explicit
"run `aleph build`" error instead of a hang.

To put something new on the pre-handshake path you must add an audited
allowlist entry in `tests/unit/test_handshake_purity.py` explaining why
it is cheap and infallible. To spawn a new child process you must pass a
finite `timeout=`, or add an audited `POPEN_ALLOWLIST` entry in
`tests/unit/test_subprocess_hygiene.py` naming the mechanism that bounds
the child's lifetime.

## Release checklist

1. `PYTHONPATH=$PWD/src python -m pytest tests/unit -q` — green.
2. `aleph selftest` — exit 0, budget table all OK.
3. Optionally `aleph selftest --project <big-real-project>` for a
   production-shaped per-tool run.
