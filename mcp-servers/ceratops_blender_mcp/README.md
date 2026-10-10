# Ceratops Blender MCP server

This directory is the complete deployable MCP server source. It owns the
package declaration, development and deployment locks, readiness manifest,
Python module, and focused tests. Repository-wide governance, lifecycle scripts,
the companion skill, and public documentation remain at the repository root.

Run the source server from the repository root:

```powershell
uv sync --project mcp-servers/ceratops_blender_mcp --extra dev --locked
uv run --project mcp-servers/ceratops_blender_mcp --locked ceratops-blender-mcp
```

Managed deployment packages this directory and installs the declared version
under `%USERPROFILE%\.codex\mcp\ceratops-blender-mcp`. The module supports the
manager's fixed `--deployment-check` protocol and the `--mcp` stdio switch.

See the repository [`README.md`](../../README.md) for the tool contract and
production workflow.
