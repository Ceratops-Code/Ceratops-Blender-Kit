# Ceratops Blender MCP server

This directory is the complete deployable MCP server source. It owns the
package declaration, canonical `uv.lock`, readiness manifest,
Python module, and focused tests. Repository-wide governance, lifecycle scripts,
the companion skill, and public documentation remain at the repository root.

Run the source server from the repository root:

```powershell
uv sync --project mcp-servers/ceratops_blender_mcp --extra dev --locked
uv run --project mcp-servers/ceratops_blender_mcp --locked ceratops-blender-mcp
```

Managed deployment packages this directory and installs the declared version
under `%USERPROFILE%\.codex\mcp\ceratops-blender-mcp`, using the same canonical
lock as development. A second exported lock is not retained because the manager
rejects ambiguous dependency inputs. The module supports the
manager's fixed `--deployment-check` protocol and the `--mcp` stdio switch.
Readiness reports the package version, tool input schemas, and each tool's
opaque-map allowlist. The MCP transport publishes those same schemas, including
lip-sync cues with an integer frame and optional numeric mouth-open value.

See the repository [`README.md`](../../README.md) for the tool contract and
production workflow.
