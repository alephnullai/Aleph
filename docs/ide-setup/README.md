# Aleph IDE Setup Guide

Aleph's MCP server works with any editor that supports the [Model Context Protocol](https://modelcontextprotocol.io). One server, all editors.

## Prerequisites

1. Install Aleph:
   ```bash
   cd /path/to/aleph
   pip install -e ".[dev]"
   ```

2. Build artifacts for your project:
   ```bash
   cd /path/to/your-project
   aleph build .
   ```

3. Note the path to your Aleph Python environment (the one with `mcp` installed):
   ```bash
   which python  # or use the venv path directly
   ```

## Editor Configuration

All editors use the same MCP server entry — just different config file locations.

### Cursor

**Project-level** (recommended — share with team):
```bash
mkdir -p .cursor
```

Create `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "aleph": {
      "command": "/path/to/aleph-venv/bin/python",
      "args": ["-m", "aleph.cli", "serve", "."]
    }
  }
}
```

**Global** (all Cursor projects):

Create `~/.cursor/mcp.json` with the same content.

Verify: **Settings > Tools & MCP** — you should see "aleph" with 31 tools.

### Claude Code

Create `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "aleph": {
      "command": "/path/to/aleph-venv/bin/python",
      "args": ["-m", "aleph.cli", "serve", "."]
    }
  }
}
```

**Global (recommended):** Edit `~/.claude/.mcp.json` — Aleph will be available in every Claude Code session regardless of directory. This is the recommended approach.

### Windsurf (Codeium)

Create or edit `~/.codeium/windsurf/mcp_config.json`:
```json
{
  "mcpServers": {
    "aleph": {
      "command": "/path/to/aleph-venv/bin/python",
      "args": ["-m", "aleph.cli", "serve", "."]
    }
  }
}
```

### VS Code (GitHub Copilot)

Create `.vscode/mcp.json`:
```json
{
  "mcpServers": {
    "aleph": {
      "command": "/path/to/aleph-venv/bin/python",
      "args": ["-m", "aleph.cli", "serve", "."]
    }
  }
}
```

Requires VS Code 1.99+ with Copilot.

## Quick Setup Script

Run from your project root to auto-configure for all editors:

```bash
# Set this to your Aleph Python path
ALEPH_PYTHON="/path/to/aleph-venv/bin/python"

# Generate configs for all editors
for dir in .cursor .vscode; do
  mkdir -p "$dir"
  cat > "$dir/mcp.json" << EOF
{
  "mcpServers": {
    "aleph": {
      "command": "$ALEPH_PYTHON",
      "args": ["-m", "aleph.cli", "serve", "."]
    }
  }
}
EOF
done

# Claude Code
cp .cursor/mcp.json .mcp.json

echo "MCP configs created for Cursor, VS Code, and Claude Code"
```

## Available Tools (26)

Once connected, the following tools are available:

| Tool | Description |
|------|-------------|
| `aleph_map` | Project manifest — start here |
| `aleph_fs` | Filesystem layout and module boundaries |
| `aleph_attention` | What matters, in what order |
| `aleph_struct` | Call graph and architecture |
| `aleph_bodies` | Compressed function bodies for a file |
| `aleph_errors` | Error flow analysis for a file |
| `aleph_intents` | Intent and invariant annotations |
| `aleph_tests` | Test coverage map for a file |
| `aleph_coverage` | Project-wide test coverage gaps |
| `aleph_salience` | How load-bearing a symbol is |
| `aleph_temporal` | Age, churn, stability |
| `aleph_search` | Find symbols by name |
| `aleph_resolve` | Look up a symbol ID |
| `aleph_expand` | Full function body |
| `aleph_callers` | Who calls this? |
| `aleph_context` | Symbol + neighborhood |
| `aleph_impact` | Pre-modification blast radius analysis |
| `aleph_brief` | Task-aware context optimizer |
| `aleph_epistemic` | Prior inferences |
| `aleph_infer` | Record a conclusion |
| `aleph_flag` | Mark something uncertain |
| `aleph_verify` | Clear a flag |
| `aleph_patch` | Create a semantic patch |
| `aleph_patch_propose` | Propose a semantic change |
| `aleph_patch_list` | List pending patches |
| `aleph_patch_apply` | Apply a patch |
| `aleph_patch_reject` | Reject a patch |
| `aleph_memory_resume` | Load prior session state |
| `aleph_session_summary` | Auto-save review trail |
| `aleph_workspace_search` | Cross-project search |
| `aleph_workspace_brief` | Cross-project task briefing |

## Workflow

1. **Start a task**: `aleph_brief "your task description"` (one call replaces five)
2. **Or orient manually**: `aleph_map` → `aleph_attention` → `aleph_struct`
3. **Navigate**: `aleph_search` → `aleph_resolve` → `aleph_callers`
4. **Before modifying**: `aleph_impact` (blast radius + risk)
5. **Record knowledge**: `aleph_infer`, `aleph_flag`
6. **Resume sessions**: `aleph_memory_resume` or `aleph_epistemic`
7. **End session**: `aleph_session_summary` (auto-saves review trail)

## Troubleshooting

**"Failed to connect" / server crashes:**
- Ensure the Python path points to an environment with `mcp` installed: `pip install mcp`
- The server starts even without `.aleph/` artifacts — tools will return guidance to run `aleph build`

**No tools appearing:**
- Check the config JSON is valid
- Restart the editor after adding the config
- Verify: `python -m aleph.cli serve . 2>&1` should not show import errors

**Stale data:**
- Run `aleph build .` to regenerate artifacts after code changes
- The build system caches aggressively — incremental rebuilds are fast
