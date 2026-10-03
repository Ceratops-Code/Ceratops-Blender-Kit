from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ceratops_blender_mcp.blender_runtime import BlenderRuntime
from ceratops_blender_mcp.service import ProductionService

BLENDER_EXECUTABLE = os.environ.get("CERATOPS_BLENDER_EXECUTABLE")


def wait_for_job(service: ProductionService, project: Path, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = service.get_job_status(str(project), job_id=job_id)
        if status["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return status
        time.sleep(0.1)
    raise AssertionError(f"real Blender job did not finish: {job_id}")


def run_operation(
    service: ProductionService,
    project: Path,
    *,
    operation: str,
    entity_id: str,
    request_id: str,
    source_version: str | None,
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    job = service.submit_operation(
        str(project),
        operation=operation,
        entity_id=entity_id,
        request_id=request_id,
        source_version=source_version,
        parameters=parameters,
    )
    status = wait_for_job(service, project, str(job["job_id"]))
    assert status["status"] == "completed", status
    return dict(status["result"])


@pytest.mark.skipif(
    not BLENDER_EXECUTABLE,
    reason="set CERATOPS_BLENDER_EXECUTABLE to run the real Blender smoke test",
)
def test_real_blender_creates_a_versioned_character(tmp_path: Path) -> None:
    service = ProductionService(blender=BlenderRuntime(BLENDER_EXECUTABLE))
    project = tmp_path / "blender-project"
    job = service.submit_operation(
        str(project),
        operation="create_character",
        entity_id="smoke-hero",
        request_id="smoke-character",
        source_version=None,
        parameters={"height_m": 1.75, "style": "stylized", "body_type": "neutral"},
    )
    status = wait_for_job(service, project, str(job["job_id"]))
    assert status["status"] == "completed", status
    version = str(status["result"]["version"])
    blend = (
        project
        / ".ceratops-blender"
        / "characters"
        / "smoke-hero"
        / "versions"
        / version
        / "scene.blend"
    )
    assert blend.stat().st_size > 1_000


@pytest.mark.skipif(
    not BLENDER_EXECUTABLE,
    reason="set CERATOPS_BLENDER_EXECUTABLE to run the real Blender pipeline test",
)
@pytest.mark.timeout(120)
def test_real_blender_character_and_shot_pipeline(tmp_path: Path) -> None:
    service = ProductionService(blender=BlenderRuntime(BLENDER_EXECUTABLE))
    project = tmp_path / "pipeline-project"
    character = run_operation(
        service,
        project,
        operation="create_character",
        entity_id="hero",
        request_id="character-blockout",
        source_version=None,
        parameters={"height_m": 1.75, "style": "stylized", "body_type": "neutral"},
    )
    character = run_operation(
        service,
        project,
        operation="create_character_mesh",
        entity_id="hero",
        request_id="character-mesh",
        source_version=str(character["version"]),
        parameters={"subdivision_levels": 1},
    )
    character = run_operation(
        service,
        project,
        operation="retopologize_character",
        entity_id="hero",
        request_id="character-retopo",
        source_version=str(character["version"]),
        parameters={"target_faces": 8000},
    )
    character = run_operation(
        service,
        project,
        operation="create_uv_and_materials",
        entity_id="hero",
        request_id="character-lookdev",
        source_version=str(character["version"]),
        parameters={"base_color": [0.4, 0.15, 0.1, 1.0], "roughness": 0.5},
    )
    appearance_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        request_id="appearance-review",
        source_version=str(character["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "angle_count": 1},
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(appearance_review["version"]),
        gate="appearance",
        reviewer="integration-test",
        request_id="approve-appearance",
    )
    groom = run_operation(
        service,
        project,
        operation="groom_character",
        entity_id="hero",
        request_id="character-groom",
        source_version=str(appearance_review["version"]),
        parameters={"style": "short", "strand_count": 8},
    )
    groom_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        request_id="groom-review",
        source_version=str(groom["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "angle_count": 1},
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(groom_review["version"]),
        gate="groom",
        reviewer="integration-test",
        request_id="approve-groom",
    )
    rig = run_operation(
        service,
        project,
        operation="rig_character",
        entity_id="hero",
        request_id="character-rig",
        source_version=str(groom_review["version"]),
        parameters={"rig_type": "biped"},
    )
    rig_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        request_id="rig-review",
        source_version=str(rig["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "angle_count": 1},
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(rig_review["version"]),
        gate="rig",
        reviewer="integration-test",
        request_id="approve-rig",
    )
    face = run_operation(
        service,
        project,
        operation="build_face_rig",
        entity_id="hero",
        request_id="character-face-rig",
        source_version=str(rig_review["version"]),
        parameters={"blendshape_set": "basic"},
    )
    face_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        request_id="face-review",
        source_version=str(face["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "angle_count": 1},
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(face_review["version"]),
        gate="facial_expression",
        reviewer="integration-test",
        request_id="approve-face",
    )
    assert (
        service.validate_character(
            str(project), character_id="hero", version=str(face_review["version"])
        )["valid"]
        is True
    )

    shot = run_operation(
        service,
        project,
        operation="create_shot",
        entity_id="shot-010",
        request_id="shot-layout",
        source_version=None,
        parameters={"frame_start": 1, "frame_end": 2, "fps": 24},
    )
    shot = run_operation(
        service,
        project,
        operation="assemble_shot",
        entity_id="shot-010",
        request_id="shot-assembly",
        source_version=str(shot["version"]),
        parameters={"asset_versions": {"hero": str(face_review["version"])}},
    )
    shot = run_operation(
        service,
        project,
        operation="setup_camera",
        entity_id="shot-010",
        request_id="shot-camera",
        source_version=str(shot["version"]),
        parameters={"lens_mm": 50.0, "position": [4.0, -6.0, 3.0], "target": [0.0, 0.0, 1.0]},
    )
    shot = run_operation(
        service,
        project,
        operation="light_shot",
        entity_id="shot-010",
        request_id="shot-light",
        source_version=str(shot["version"]),
        parameters={"preset": "three_point", "intensity": 500.0},
    )
    shot = run_operation(
        service,
        project,
        operation="animate_shot",
        entity_id="shot-010",
        request_id="shot-animation",
        source_version=str(shot["version"]),
        parameters={"motion": "blocking", "interpolation": "BEZIER"},
    )
    shot = run_operation(
        service,
        project,
        operation="sync_lips",
        entity_id="shot-010",
        request_id="shot-lips",
        source_version=str(shot["version"]),
        parameters={"cues": [{"frame": 1, "value": 0.0}, {"frame": 2, "value": 1.0}]},
    )
    shot = run_operation(
        service,
        project,
        operation="add_secondary_motion",
        entity_id="shot-010",
        request_id="shot-secondary",
        source_version=str(shot["version"]),
        parameters={"strength": 0.1},
    )
    preview = run_operation(
        service,
        project,
        operation="render_shot_preview",
        entity_id="shot-010",
        request_id="shot-preview",
        source_version=str(shot["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "frame_start": 1, "frame_end": 2},
    )
    service.promote_version(
        str(project),
        asset_type="shot",
        asset_id="shot-010",
        version=str(preview["version"]),
        gate="animation",
        reviewer="integration-test",
        request_id="approve-animation",
    )
    final = run_operation(
        service,
        project,
        operation="render_shot_final",
        entity_id="shot-010",
        request_id="shot-final",
        source_version=str(preview["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "frame_start": 1, "frame_end": 2},
    )
    package = service.package_episode(
        str(project),
        episode_id="episode-001",
        shot_versions={"shot-010": str(final["version"])},
        request_id="package-episode",
    )
    packaged = wait_for_job(service, project, str(package["job_id"]))
    assert packaged["status"] == "completed", packaged
