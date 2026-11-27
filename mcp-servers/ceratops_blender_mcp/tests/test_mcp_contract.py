from __future__ import annotations

import asyncio
import hashlib
import json
import os
import runpy
import subprocess
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


def test_local_beta_reads_tagged_alpha_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Path(__file__).resolve().parents[3]
    runner = runpy.run_path(str(repository / "scripts/run-tests.py"))
    select_summaries = runner["select_build_check_summaries"]
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    storage_key = "members/packages/fixture"
    version = "1.0a1"
    tag = f"{storage_key}/{version}"
    receipt = Path(".ceratops/build") / tag / "build_receipt.json"
    summary = receipt.with_name("check_summary.json")
    (tmp_path / receipt.parent).mkdir(parents=True)
    original = {
        "id": "fixture", "kind": "repository-check", "status": "passed",
        "result_id": "original-alpha", "check_version": "original-check",
        "qualificationLevel": "alpha", "completed_ns": 101,
    }
    summary_bytes = json.dumps({
        "schema": "ceratops-check-summary.v1", "inputGroups": {}, "checkGroups": {},
        "checks": [original],
    }).encode()
    (tmp_path / summary).write_bytes(summary_bytes)
    (tmp_path / receipt).write_text(json.dumps({
        "schema": "ceratops-build-result.v4", "status": "passed", "tag": tag,
        "identity": {"storageKey": storage_key, "version": version, "target": "native"},
        "receiptPath": receipt.as_posix(),
        "committedResultPaths": [receipt.as_posix(), summary.as_posix()],
        "checkSummary": {
            "root": "git", "path": summary.as_posix(), "size": len(summary_bytes),
            "sha256": hashlib.sha256(summary_bytes).hexdigest(),
        },
    }), encoding="utf-8")
    commands = [
        ["init", "--quiet"], ["add", "--force", "--", receipt.as_posix(), summary.as_posix()],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={os.devnull}",
         "commit", "--quiet", "-m", "Record fixture alpha checks"],
        ["tag", "--no-sign", tag],
    ]
    for command in commands:
        subprocess.run(["git", *command], cwd=tmp_path, check=True, capture_output=True)

    for _repeat in range(2):
        selected, = select_summaries(tmp_path, qualification_level="beta", target="native")
        assert selected["receipt"]["identity"]["version"] == version
        assert selected["summary"]["checks"] == [original]
    for level in ("rc", "final"):
        assert select_summaries(tmp_path, qualification_level=level, target="native") == []


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
