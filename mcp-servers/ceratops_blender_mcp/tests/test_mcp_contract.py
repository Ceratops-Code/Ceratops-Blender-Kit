from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from ceratops_blender_mcp.__main__ import main
from ceratops_blender_mcp.server import mcp

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
}


def test_server_exposes_the_complete_v1_tool_contract() -> None:
    tools = asyncio.run(mcp.list_tools())

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all("request_id" not in tool.input_schema.get("properties", {}) for tool in tools)


def test_deployment_check_reports_exact_release_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_path.open("rb") as stream:
        project = tomllib.load(stream)["project"]

    main(["--deployment-check"])

    output = capsys.readouterr().out
    assert json.loads(output) == {
        "mcp_server_id": "ceratops-blender-mcp",
        "version": project["version"],
        "ready": True,
        "tools": {
            tool.name: {"input_schema": tool.input_schema, "opaque_parameters": []}
            for tool in asyncio.run(mcp.list_tools())
        },
    }


def test_lip_sync_publishes_complete_cue_input_schema() -> None:
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    schema = tools["sync_lips"].input_schema
    cue = schema["properties"]["cues"]["items"]
    if "$ref" in cue:
        cue = schema["$defs"][cue["$ref"].removeprefix("#/$defs/")]

    assert cue["type"] == "object"
    assert cue["required"] == ["frame"]
    assert cue["properties"]["frame"]["type"] == "integer"
    assert cue["properties"]["value"]["type"] == "number"
