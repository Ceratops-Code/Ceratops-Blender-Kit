"""Ceratops-Blender-MCP production workflow server."""

import tomllib
from importlib.metadata import version
from pathlib import Path

from .service import ProductionService

__all__ = ["ProductionService"]


def _declared_version() -> str:
    """Use source metadata for uninstalled checks and wheel metadata after installation."""

    source_project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if source_project.is_file():
        with source_project.open("rb") as stream:
            project = tomllib.load(stream)["project"]
        if project["name"] == "ceratops-blender-mcp":
            return str(project["version"])
    return version("ceratops-blender-mcp")


__version__ = _declared_version()
