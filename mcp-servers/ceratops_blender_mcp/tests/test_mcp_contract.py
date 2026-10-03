from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from pathlib import Path

from ceratops_blender_mcp.server import main, mcp

EXPECTED_TOOLS = {
    "inspect_project",
    "list_assets",
    "inspect_asset",
    "compare_asset_versions",
    "promote_version",
    "archive_version",
    "import_character_reference",
    "create_character",
    "create_character_mesh",
    "retopologize_character",
    "create_uv_and_materials",
    "groom_character",
    "rig_character",
    "build_face_rig",
    "render_character_review",
    "validate_character",
    "create_shot",
    "assemble_shot",
    "setup_camera",
    "light_shot",
    "animate_shot",
    "sync_lips",
    "add_secondary_motion",
    "render_shot_preview",
    "render_shot_final",
    "package_asset",
    "package_episode",
    "get_job_status",
    "cancel_job",
    "resume_job",
}


def test_server_exposes_the_complete_v1_tool_contract() -> None:
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools} == EXPECTED_TOOLS


def test_deployment_manifest_points_to_the_callable_server() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "mcp-server.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest == {"schema": 2, "module": "ceratops_blender_mcp.server"}
    assert callable(mcp.run)


def test_deployment_probe_reports_the_declared_version(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["ceratops-blender-mcp", "--deployment-check"])

    main()

    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    )
    assert json.loads(capsys.readouterr().out) == {
        "mcp_server_id": "ceratops-blender-mcp",
        "version": project["project"]["version"],
        "ready": True,
    }
