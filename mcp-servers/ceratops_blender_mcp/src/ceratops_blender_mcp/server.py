"""Ceratops-Blender-MCP tool registration and stdio entry point."""

from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server import MCPServer

from . import __version__
from .service import ProductionService

mcp = MCPServer("Ceratops-Blender-MCP")
service = ProductionService()


@mcp.tool()
def inspect_project(project_root: str) -> dict[str, object]:
    """Read project counts, configuration, and active-job state without modifying it."""

    return service.inspect_project(project_root)


@mcp.tool()
def list_assets(
    project_root: str,
    asset_type: str | None = None,
    include_archived: bool = False,
) -> dict[str, object]:
    """List character and shot identities plus their explicit versions."""

    return service.list_assets(
        project_root, asset_type=asset_type, include_archived=include_archived
    )


@mcp.tool()
def inspect_asset(
    project_root: str,
    asset_type: str,
    asset_id: str,
    version: str | None = None,
) -> dict[str, object]:
    """Read one asset's lifecycle state or one exact immutable version."""

    return service.inspect_asset(
        project_root, asset_type=asset_type, asset_id=asset_id, version=version
    )


@mcp.tool()
def compare_asset_versions(
    project_root: str,
    asset_type: str,
    asset_id: str,
    left_version: str,
    right_version: str,
) -> dict[str, object]:
    """Compare the metadata and artifact hashes of two exact asset versions."""

    return service.compare_asset_versions(
        project_root,
        asset_type=asset_type,
        asset_id=asset_id,
        left_version=left_version,
        right_version=right_version,
    )


@mcp.tool()
def promote_version(
    project_root: str,
    asset_type: str,
    asset_id: str,
    version: str,
    gate: str,
    reviewer: str,
    request_id: str,
    notes: str = "",
) -> dict[str, object]:
    """Approve an exact version at a named review gate without changing its bytes."""

    return service.promote_version(
        project_root,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        gate=gate,
        reviewer=reviewer,
        request_id=request_id,
        notes=notes,
    )


@mcp.tool()
def archive_version(
    project_root: str,
    asset_type: str,
    asset_id: str,
    version: str,
    request_id: str,
    reason: str,
) -> dict[str, object]:
    """Archive an unpromoted version while retaining every file and record."""

    return service.archive_version(
        project_root,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        request_id=request_id,
        reason=reason,
    )


@mcp.tool()
def import_character_reference(
    project_root: str,
    character_id: str,
    reference_files: list[str],
    request_id: str,
) -> dict[str, object]:
    """Copy reference images or files into a new immutable character version."""

    return service.import_character_reference(
        project_root,
        character_id=character_id,
        reference_files=reference_files,
        request_id=request_id,
    )


@mcp.tool()
def create_character(
    project_root: str,
    character_id: str,
    request_id: str,
    reference_version: str | None = None,
    height_m: float = 1.75,
    style: str = "stylized",
    body_type: str = "neutral",
) -> dict[str, object]:
    """Create a deterministic character blockout, optionally from an exact reference version."""

    return service.submit_operation(
        project_root,
        operation="create_character",
        entity_id=character_id,
        request_id=request_id,
        source_version=reference_version,
        parameters={"height_m": height_m, "style": style, "body_type": body_type},
    )


@mcp.tool()
def create_character_mesh(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    subdivision_levels: int = 1,
) -> dict[str, object]:
    """Create a mesh version from one exact character version."""

    return service.submit_operation(
        project_root,
        operation="create_character_mesh",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"subdivision_levels": subdivision_levels},
    )


@mcp.tool()
def retopologize_character(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    target_faces: int = 12000,
) -> dict[str, object]:
    """Create a non-destructive retopology version with a target face budget."""

    return service.submit_operation(
        project_root,
        operation="retopologize_character",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"target_faces": target_faces},
    )


@mcp.tool()
def create_uv_and_materials(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    base_color: list[float] | None = None,
    roughness: float = 0.5,
) -> dict[str, object]:
    """Create UVs and a material in a new look-development version."""

    return service.submit_operation(
        project_root,
        operation="create_uv_and_materials",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"base_color": base_color or [0.45, 0.18, 0.12, 1.0], "roughness": roughness},
    )


@mcp.tool()
def groom_character(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    style: str = "short",
    strand_count: int = 24,
) -> dict[str, object]:
    """Create a groom from an appearance-approved exact source version."""

    return service.submit_operation(
        project_root,
        operation="groom_character",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"style": style, "strand_count": strand_count},
    )


@mcp.tool()
def rig_character(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    rig_type: str = "biped",
) -> dict[str, object]:
    """Create a rig from a groom-approved exact source version."""

    return service.submit_operation(
        project_root,
        operation="rig_character",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"rig_type": rig_type},
    )


@mcp.tool()
def build_face_rig(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    blendshape_set: str = "basic",
) -> dict[str, object]:
    """Create facial shape keys from a rig-approved exact source version."""

    return service.submit_operation(
        project_root,
        operation="build_face_rig",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"blendshape_set": blendshape_set},
    )


@mcp.tool()
def render_character_review(
    project_root: str,
    character_id: str,
    source_version: str,
    request_id: str,
    resolution_x: int = 960,
    resolution_y: int = 960,
    angle_count: int = 1,
) -> dict[str, object]:
    """Render a review artifact in a new version for an explicit approval gate."""

    return service.submit_operation(
        project_root,
        operation="render_character_review",
        entity_id=character_id,
        request_id=request_id,
        source_version=source_version,
        parameters={
            "resolution_x": resolution_x,
            "resolution_y": resolution_y,
            "angle_count": angle_count,
        },
    )


@mcp.tool()
def validate_character(project_root: str, character_id: str, version: str) -> dict[str, object]:
    """Read and validate exact character lineage plus recorded artifact hashes."""

    return service.validate_character(project_root, character_id=character_id, version=version)


@mcp.tool()
def create_shot(
    project_root: str,
    shot_id: str,
    request_id: str,
    frame_start: int = 1,
    frame_end: int = 120,
    fps: int = 24,
) -> dict[str, object]:
    """Create a new shot layout with an explicit frame range."""

    return service.submit_operation(
        project_root,
        operation="create_shot",
        entity_id=shot_id,
        request_id=request_id,
        source_version=None,
        parameters={"frame_start": frame_start, "frame_end": frame_end, "fps": fps},
    )


@mcp.tool()
def assemble_shot(
    project_root: str,
    shot_id: str,
    source_version: str,
    asset_versions: dict[str, str],
    request_id: str,
) -> dict[str, object]:
    """Assemble exact character versions into a new shot version."""

    return service.submit_operation(
        project_root,
        operation="assemble_shot",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"asset_versions": asset_versions},
    )


@mcp.tool()
def setup_camera(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    lens_mm: float = 50.0,
    position: list[float] | None = None,
    target: list[float] | None = None,
) -> dict[str, object]:
    """Create a camera version from one exact shot source."""

    return service.submit_operation(
        project_root,
        operation="setup_camera",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={
            "lens_mm": lens_mm,
            "position": position or [4.0, -6.0, 3.0],
            "target": target or [0.0, 0.0, 1.0],
        },
    )


@mcp.tool()
def light_shot(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    preset: str = "three_point",
    intensity: float = 1000.0,
) -> dict[str, object]:
    """Create a lit shot version using a fixed production preset."""

    return service.submit_operation(
        project_root,
        operation="light_shot",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"preset": preset, "intensity": intensity},
    )


@mcp.tool()
def animate_shot(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    motion: str = "blocking",
    interpolation: str = "BEZIER",
) -> dict[str, object]:
    """Create a deterministic blocking-animation version."""

    return service.submit_operation(
        project_root,
        operation="animate_shot",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"motion": motion, "interpolation": interpolation},
    )


@mcp.tool()
def sync_lips(
    project_root: str,
    shot_id: str,
    source_version: str,
    cues: list[dict[str, Any]],
    request_id: str,
) -> dict[str, object]:
    """Apply explicit frame/value mouth cues to an exact shot version."""

    return service.submit_operation(
        project_root,
        operation="sync_lips",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"cues": cues},
    )


@mcp.tool()
def add_secondary_motion(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    strength: float = 0.25,
) -> dict[str, object]:
    """Add bounded procedural secondary motion in a new shot version."""

    return service.submit_operation(
        project_root,
        operation="add_secondary_motion",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={"strength": strength},
    )


@mcp.tool()
def render_shot_preview(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    resolution_x: int = 960,
    resolution_y: int = 540,
    frame_start: int = 1,
    frame_end: int = 120,
) -> dict[str, object]:
    """Render a review sequence into a new preview version."""

    return service.submit_operation(
        project_root,
        operation="render_shot_preview",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={
            "resolution_x": resolution_x,
            "resolution_y": resolution_y,
            "frame_start": frame_start,
            "frame_end": frame_end,
        },
    )


@mcp.tool()
def render_shot_final(
    project_root: str,
    shot_id: str,
    source_version: str,
    request_id: str,
    resolution_x: int = 1920,
    resolution_y: int = 1080,
    frame_start: int = 1,
    frame_end: int = 120,
) -> dict[str, object]:
    """Render an animation-approved source into a new final-render version."""

    return service.submit_operation(
        project_root,
        operation="render_shot_final",
        entity_id=shot_id,
        request_id=request_id,
        source_version=source_version,
        parameters={
            "resolution_x": resolution_x,
            "resolution_y": resolution_y,
            "frame_start": frame_start,
            "frame_end": frame_end,
        },
    )


@mcp.tool()
def package_asset(
    project_root: str,
    asset_type: str,
    asset_id: str,
    version: str,
    request_id: str,
) -> dict[str, object]:
    """Package one exact character or shot version into a versioned ZIP."""

    return service.package_asset(
        project_root,
        asset_type=asset_type,
        asset_id=asset_id,
        version=version,
        request_id=request_id,
    )


@mcp.tool()
def package_episode(
    project_root: str,
    episode_id: str,
    shot_versions: dict[str, str],
    request_id: str,
) -> dict[str, object]:
    """Package caller-selected exact shot versions into a versioned episode ZIP."""

    return service.package_episode(
        project_root,
        episode_id=episode_id,
        shot_versions=shot_versions,
        request_id=request_id,
    )


@mcp.tool()
def get_job_status(project_root: str, job_id: str) -> dict[str, object]:
    """Read the persistent status and result of a long production job."""

    return service.get_job_status(project_root, job_id=job_id)


@mcp.tool()
def cancel_job(project_root: str, job_id: str) -> dict[str, object]:
    """Request cancellation of an exact queued or running production job."""

    return service.cancel_job(project_root, job_id=job_id)


@mcp.tool()
def resume_job(project_root: str, job_id: str) -> dict[str, object]:
    """Resume failed, cancelled, or interrupted work under the same stable job ID."""

    return service.resume_job(project_root, job_id=job_id)


def main() -> None:
    """Answer the manager's readiness probe or run the local stdio transport."""

    if sys.argv[1:] == ["--deployment-check"]:
        ready = {"mcp_server_id": "ceratops-blender-mcp", "version": __version__, "ready": True}
        print(json.dumps(ready))
        return
    mcp.run()


if __name__ == "__main__":
    main()
