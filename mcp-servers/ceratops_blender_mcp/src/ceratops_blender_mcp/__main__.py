"""Expose only the managed readiness check and MCP stdio transport switches."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from . import __version__

MCP_SERVER_ID = "ceratops-blender-mcp"


def main(argv: Sequence[str] | None = None) -> None:
    """Run readiness without user-data writes, or serve MCP over stdio."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--deployment-check"]:
        from .server import mcp

        _ = mcp

        print(
            json.dumps(
                {"mcp_server_id": MCP_SERVER_ID, "version": __version__, "ready": True},
                separators=(",", ":"),
            )
        )
        return
    if arguments in ([], ["--mcp"]):
        from .server import mcp

        mcp.run(transport="stdio")
        return
    raise SystemExit("usage: ceratops-blender-mcp [--mcp|--deployment-check]")


if __name__ == "__main__":
    main()
