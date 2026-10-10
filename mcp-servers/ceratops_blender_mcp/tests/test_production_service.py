from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from threading import Event

import pytest

from ceratops_blender_mcp.blender_runtime import RecordingBlenderRuntime
from ceratops_blender_mcp.service import ProductionService
from ceratops_blender_mcp.storage import ProductionError, ProjectStore


class FailingOnceRuntime(RecordingBlenderRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def run(
        self,
        *,
        store: ProjectStore,
        version_root: Path,
        operation: str,
        source_blend: Path | None,
        parameters: Mapping[str, object],
        cancellation: Event,
        timeout_seconds: int = 7200,
    ) -> list[Path]:
        if not self.failed:
            self.failed = True
            raise ProductionError("simulated Blender interruption")
        return super().run(
            store=store,
            version_root=version_root,
            operation=operation,
            source_blend=source_blend,
            parameters=parameters,
            cancellation=cancellation,
            timeout_seconds=timeout_seconds,
        )


def wait_for_job(service: ProductionService, project: Path, job_id: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = service.get_job_status(str(project), job_id=job_id)
        if status["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return status
        time.sleep(0.01)
    raise AssertionError(f"job did not finish: {job_id}")


def submit_and_wait(
    service: ProductionService,
    project: Path,
    *,
    operation: str,
    entity_id: str,
    source_version: str | None,
    parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    job = service.submit_operation(
        str(project),
        operation=operation,
        entity_id=entity_id,
        source_version=source_version,
        parameters=parameters,
    )
    completed = wait_for_job(service, project, str(job["job_id"]))
    assert completed["status"] == "completed", completed
    return dict(completed["result"])


def test_reads_do_not_initialize_a_project(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())

    result = service.inspect_project(str(project))

    assert result["exists"] is False
    assert not project.exists()


def test_repeated_desired_state_reuses_immutable_versions(tmp_path: Path) -> None:
    project = tmp_path / "production"
    runtime = RecordingBlenderRuntime()
    service = ProductionService(blender=runtime)

    first = service.submit_operation(
        str(project),
        operation="create_character",
        entity_id="hero",
        source_version=None,
        parameters={"height_m": 1.8, "style": "stylized", "body_type": "neutral"},
    )
    repeated = service.submit_operation(
        str(project),
        operation="create_character",
        entity_id="hero",
        source_version=None,
        parameters={"height_m": 1.8, "style": "stylized", "body_type": "neutral"},
    )
    repeated_result = wait_for_job(service, project, str(repeated["job_id"]))
    assert repeated_result["status"] == "completed"
    first_result = wait_for_job(service, project, str(first["job_id"]))
    assert first_result["status"] == "completed"
    assert first_result["result"]["version"] == "v0001"
    original = (
        project / ".ceratops-blender" / "characters" / "hero" / "versions" / "v0001" / "scene.blend"
    ).read_bytes()

    mesh = submit_and_wait(
        service,
        project,
        operation="create_character_mesh",
        entity_id="hero",
        source_version="v0001",
        parameters={"subdivision_levels": 1},
    )

    assert mesh["version"] == "v0002"
    assert (
        project / ".ceratops-blender" / "characters" / "hero" / "versions" / "v0001" / "scene.blend"
    ).read_bytes() == original
    comparison = service.compare_asset_versions(
        str(project),
        asset_type="character",
        asset_id="hero",
        left_version="v0001",
        right_version="v0002",
    )
    assert comparison["equal"] is False
    assert set(comparison["differences"]) >= {"operation", "stage", "artifacts"}


def test_review_gates_control_downstream_character_work(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())
    blockout = submit_and_wait(
        service,
        project,
        operation="create_character",
        entity_id="hero",
        source_version=None,
        parameters={"height_m": 1.75},
    )
    mesh = submit_and_wait(
        service,
        project,
        operation="create_character_mesh",
        entity_id="hero",
        source_version=str(blockout["version"]),
    )
    lookdev = submit_and_wait(
        service,
        project,
        operation="create_uv_and_materials",
        entity_id="hero",
        source_version=str(mesh["version"]),
    )

    with pytest.raises(ProductionError, match="appearance review gate"):
        service.submit_operation(
            str(project),
            operation="groom_character",
            entity_id="hero",
            source_version=str(lookdev["version"]),
        )

    review = submit_and_wait(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        source_version=str(lookdev["version"]),
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(review["version"]),
        gate="appearance",
        reviewer="art-director",
    )
    replacement_review = submit_and_wait(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
        source_version=str(lookdev["version"]),
    )
    service.promote_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(replacement_review["version"]),
        gate="appearance",
        reviewer="art-director",
    )
    state = service.inspect_asset(str(project), asset_type="character", asset_id="hero")
    assert state["promoted_versions"]["appearance"] == replacement_review["version"]
    groom = submit_and_wait(
        service,
        project,
        operation="groom_character",
        entity_id="hero",
        source_version=str(replacement_review["version"]),
    )
    assert groom["stage"] == "groom"


def test_archive_retains_files_but_hides_active_version(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())
    created = submit_and_wait(
        service,
        project,
        operation="create_character",
        entity_id="hero",
        source_version=None,
    )
    service.archive_version(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(created["version"]),
        reason="superseded concept",
    )

    active = service.list_assets(str(project), asset_type="character")
    retained = service.list_assets(str(project), asset_type="character", include_archived=True)

    assert active["assets"][0]["versions"] == []
    assert retained["assets"][0]["versions"] == ["v0001"]
    assert (
        project / ".ceratops-blender" / "characters" / "hero" / "versions" / "v0001" / "record.json"
    ).is_file()


def test_package_uses_exact_selected_asset_version(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())
    created = submit_and_wait(
        service,
        project,
        operation="create_character",
        entity_id="hero",
        source_version=None,
    )
    package = service.package_asset(
        str(project),
        asset_type="character",
        asset_id="hero",
        version=str(created["version"]),
    )
    completed = wait_for_job(service, project, str(package["job_id"]))

    assert completed["status"] == "completed"
    result = completed["result"]
    package_path = (
        project
        / ".ceratops-blender"
        / "deliveries"
        / "asset-hero"
        / "versions"
        / str(result["version"])
        / str(result["package"])
    )
    assert package_path.is_file()


def test_shot_final_requires_animation_review(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())
    character = submit_and_wait(
        service,
        project,
        operation="create_character",
        entity_id="hero",
        source_version=None,
    )
    shot = submit_and_wait(
        service,
        project,
        operation="create_shot",
        entity_id="shot-010",
        source_version=None,
        parameters={"frame_start": 1, "frame_end": 12, "fps": 24},
    )
    assembled = submit_and_wait(
        service,
        project,
        operation="assemble_shot",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"asset_versions": {"hero": str(character["version"])}},
    )
    animated = submit_and_wait(
        service,
        project,
        operation="animate_shot",
        entity_id="shot-010",
        source_version=str(assembled["version"]),
    )
    preview = submit_and_wait(
        service,
        project,
        operation="render_shot_preview",
        entity_id="shot-010",
        source_version=str(animated["version"]),
        parameters={"frame_start": 1, "frame_end": 12},
    )

    with pytest.raises(ProductionError, match="animation review gate"):
        service.submit_operation(
            str(project),
            operation="render_shot_final",
            entity_id="shot-010",
            source_version=str(preview["version"]),
        )

    service.promote_version(
        str(project),
        asset_type="shot",
        asset_id="shot-010",
        version=str(preview["version"]),
        gate="animation",
        reviewer="director",
    )
    final = submit_and_wait(
        service,
        project,
        operation="render_shot_final",
        entity_id="shot-010",
        source_version=str(preview["version"]),
        parameters={"frame_start": 1, "frame_end": 12},
    )
    assert final["stage"] == "final_render"


def test_fresh_call_after_failure_needs_no_previous_job(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=FailingOnceRuntime())
    submitted = service.submit_operation(
        str(project),
        operation="create_character",
        entity_id="hero",
        source_version=None,
    )
    failed = wait_for_job(service, project, str(submitted["job_id"]))
    assert failed["status"] == "failed"

    # Remove monitoring history: the next invocation cannot consume it.
    (project / ".ceratops-blender" / "jobs" / f"{submitted['job_id']}.json").unlink()
    service = ProductionService(blender=service.blender)
    fresh = service.submit_operation(str(project), operation="create_character",
                                     entity_id="hero", source_version=None)
    assert fresh["job_id"] != submitted["job_id"]
    completed = wait_for_job(service, project, str(fresh["job_id"]))

    assert completed["status"] == "completed"
    assert completed["result"]["version"] == "v0001"
    assert "payload" not in ProjectStore.read_json(
        project / ".ceratops-blender" / "jobs" / f"{fresh['job_id']}.json")



def test_promotion_tracks_current_gate_state_when_an_older_version_is_selected_again(
    tmp_path: Path,
) -> None:
    store = ProjectStore(tmp_path / "production")
    first = {"event": "promote", "gate": "appearance", "version": "v0001", "reviewer": "director"}
    second = {**first, "version": "v0002"}
    initial = store.append_event("character", "hero", payload=first)
    assert store.append_event("character", "hero", payload=first) == initial
    store.append_event("character", "hero", payload=second)
    selected = store.append_event("character", "hero", payload=first)
    assert selected["event_id"] != initial["event_id"]
    assert store.entity_state("character", "hero")["promoted_versions"]["appearance"] == "v0001"


def test_completed_output_reuse_discards_abandoned_partial_output(tmp_path: Path) -> None:
    project = tmp_path / "production"
    service = ProductionService(blender=RecordingBlenderRuntime())
    created = submit_and_wait(service, project, operation="create_character",
                              entity_id="hero", source_version=None)
    versions = project / ".ceratops-blender" / "characters" / "hero" / "versions"
    original = (versions / str(created["version"]) / "scene.blend").read_bytes()
    partial = versions / "v0002"
    partial.mkdir()
    (partial / "request.json").write_text('{"operation":"interrupted"}', encoding="utf-8")
    (partial / "incomplete.blend").write_bytes(b"incomplete output")
    repeated = submit_and_wait(service, project, operation="create_character",
                               entity_id="hero", source_version=None)
    assert repeated["version"] == created["version"]
    assert not partial.exists()
    assert (versions / str(created["version"]) / "scene.blend").read_bytes() == original
