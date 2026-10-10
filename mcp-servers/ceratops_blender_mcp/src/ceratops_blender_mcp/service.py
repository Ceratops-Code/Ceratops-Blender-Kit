"""Goal-oriented production service behind the MCP tool surface."""

from __future__ import annotations

import threading
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

from filelock import FileLock

from .blender_runtime import BlenderRuntime
from .jobs import JobManager
from .storage import (
    ProductionError,
    ProjectStore,
    file_digest,
    payload_hash,
    require_identifier,
    require_version,
)


def serialize_production[ProductionResult](
    function: Callable[..., ProductionResult],
) -> Callable[..., ProductionResult]:
    """Serialize output creation so a fresh call can remove abandoned partials."""
    @wraps(function)
    def invoke(
        self: ProductionService, project_root: str | ProjectStore, *args: Any, **kwargs: Any
    ) -> ProductionResult:
        store = (
            project_root if isinstance(project_root, ProjectStore) else self._store(project_root)
        )
        store.initialize()
        with FileLock(store.state_root / "production.lock", timeout=7200):
            return function(self, project_root, *args, **kwargs)
    return invoke


@dataclass(frozen=True)
class OperationSpec:
    """One fixed workflow transition; never an arbitrary Blender command."""

    entity_type: str
    stage: str
    allowed_parameters: frozenset[str]
    requires_source: bool = True
    uses_blender: bool = True
    required_gate: str | None = None


OPERATIONS: dict[str, OperationSpec] = {
    "create_character": OperationSpec(
        "character", "blockout", frozenset({"height_m", "style", "body_type"}), False
    ),
    "create_character_mesh": OperationSpec("character", "mesh", frozenset({"subdivision_levels"})),
    "retopologize_character": OperationSpec("character", "retopology", frozenset({"target_faces"})),
    "create_uv_and_materials": OperationSpec(
        "character", "lookdev", frozenset({"base_color", "roughness"})
    ),
    "groom_character": OperationSpec(
        "character",
        "groom",
        frozenset({"style", "strand_count"}),
        required_gate="appearance",
    ),
    "rig_character": OperationSpec(
        "character", "rig", frozenset({"rig_type"}), required_gate="groom"
    ),
    "build_face_rig": OperationSpec(
        "character",
        "face_rig",
        frozenset({"blendshape_set"}),
        required_gate="rig",
    ),
    "render_character_review": OperationSpec(
        "character",
        "review",
        frozenset({"resolution_x", "resolution_y", "angle_count"}),
    ),
    "create_shot": OperationSpec(
        "shot", "layout", frozenset({"frame_start", "frame_end", "fps"}), False
    ),
    "assemble_shot": OperationSpec("shot", "assembly", frozenset({"asset_versions"})),
    "setup_camera": OperationSpec("shot", "camera", frozenset({"lens_mm", "position", "target"})),
    "light_shot": OperationSpec("shot", "lighting", frozenset({"preset", "intensity"})),
    "animate_shot": OperationSpec("shot", "animation", frozenset({"motion", "interpolation"})),
    "sync_lips": OperationSpec("shot", "lip_sync", frozenset({"cues"})),
    "add_secondary_motion": OperationSpec("shot", "secondary_motion", frozenset({"strength"})),
    "render_shot_preview": OperationSpec(
        "shot",
        "preview",
        frozenset({"resolution_x", "resolution_y", "frame_start", "frame_end"}),
    ),
    "render_shot_final": OperationSpec(
        "shot",
        "final_render",
        frozenset({"resolution_x", "resolution_y", "frame_start", "frame_end"}),
        required_gate="animation",
    ),
}

GATE_REQUIREMENTS: dict[str, tuple[str, set[str]]] = {
    "appearance": ("character", {"create_uv_and_materials", "render_character_review"}),
    "groom": ("character", {"groom_character", "render_character_review"}),
    "rig": ("character", {"rig_character", "render_character_review"}),
    "facial_expression": (
        "character",
        {"build_face_rig", "render_character_review"},
    ),
    "animation": ("shot", {"animate_shot", "render_shot_preview"}),
}


class ProductionService:
    """Coordinate exact versions, reviews, Blender jobs, and delivery packages."""

    def __init__(
        self,
        *,
        blender: BlenderRuntime | None = None,
        jobs: JobManager | None = None,
    ) -> None:
        self.blender = blender or BlenderRuntime()
        self.jobs = jobs or JobManager()

    @staticmethod
    def _store(project_root: str) -> ProjectStore:
        return ProjectStore(project_root)

    def inspect_project(self, project_root: str) -> dict[str, object]:
        """Inspect project state without initializing or modifying it."""

        store = self._store(project_root)
        project = store.project_record()
        if project is None:
            return {"exists": False, "project_root": str(store.root), "characters": 0, "shots": 0}
        characters = self._entity_ids(store, "character")
        shots = self._entity_ids(store, "shot")
        jobs_root = store.state_root / "jobs"
        active_jobs = 0
        if jobs_root.is_dir():
            for path in jobs_root.glob("job_*.json"):
                if store.read_json(path).get("status") in {"queued", "running"}:
                    active_jobs += 1
        return {
            "exists": True,
            "project_root": str(store.root),
            "project": project,
            "characters": len(characters),
            "shots": len(shots),
            "active_jobs": active_jobs,
        }

    def list_assets(
        self,
        project_root: str,
        *,
        asset_type: str | None = None,
        include_archived: bool = False,
    ) -> dict[str, object]:
        """List characters and shots without choosing mutable latest versions."""

        store = self._store(project_root)
        if not store.exists:
            return {"assets": []}
        types = [asset_type] if asset_type else ["character", "shot"]
        if any(value not in {"character", "shot"} for value in types):
            raise ProductionError("asset_type must be character or shot")
        assets: list[dict[str, object]] = []
        for kind in types:
            for entity_id in self._entity_ids(store, kind):
                state = store.entity_state(kind, entity_id)
                archived = set(state["archived_versions"])
                versions = [
                    str(record["version"])
                    for record in state["versions"]
                    if include_archived or record["version"] not in archived
                ]
                assets.append(
                    {
                        "asset_type": kind,
                        "asset_id": entity_id,
                        "versions": versions,
                        "promoted_versions": state["promoted_versions"],
                    }
                )
        return {"assets": assets}

    def inspect_asset(
        self,
        project_root: str,
        *,
        asset_type: str,
        asset_id: str,
        version: str | None = None,
    ) -> dict[str, object]:
        """Inspect an entity or one exact immutable version."""

        store = self._store(project_root)
        require_identifier(asset_id, "asset_id")
        if version is not None:
            return store.exact_version(asset_type, asset_id, require_version(version))
        return store.entity_state(asset_type, asset_id)

    def compare_asset_versions(
        self,
        project_root: str,
        *,
        asset_type: str,
        asset_id: str,
        left_version: str,
        right_version: str,
    ) -> dict[str, object]:
        """Compare exact metadata and artifact identities for two versions."""

        store = self._store(project_root)
        left = store.exact_version(asset_type, asset_id, require_version(left_version))
        right = store.exact_version(asset_type, asset_id, require_version(right_version))
        fields = ("operation", "stage", "parent_version", "parameters", "artifacts")
        differences = {
            field: {"left": left.get(field), "right": right.get(field)}
            for field in fields
            if left.get(field) != right.get(field)
        }
        return {
            "asset_type": asset_type,
            "asset_id": asset_id,
            "left_version": left_version,
            "right_version": right_version,
            "equal": not differences,
            "differences": differences,
        }

    @serialize_production
    def import_character_reference(
        self,
        project_root: str,
        *,
        character_id: str,
        reference_files: Sequence[str],
    ) -> dict[str, object]:
        """Copy exact reference files into a new immutable character version."""

        if not reference_files:
            raise ProductionError("at least one reference file is required")
        store = self._store(project_root)
        require_identifier(character_id, "character_id")
        inputs = []
        for value in reference_files:
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise ProductionError(f"reference file does not exist: {path}")
            inputs.append({"name": path.name, **file_digest(path)})
        parameters = {"references": inputs}
        _, root = store.reserve_version(
            "character",
            character_id,
            operation="import_character_reference",
            parent_version=None,
            parameters=parameters,
        )
        if (root / "record.json").is_file():
            return store.read_json(root / "record.json")
        try:
            outputs = store.copy_inputs(reference_files, root / "references")
            return store.complete_version(root, stage="reference", artifacts=outputs)
        except Exception as exc:
            store.fail_version(root, str(exc))
            raise

    def submit_operation(
        self,
        project_root: str,
        *,
        operation: str,
        entity_id: str,
        source_version: str | None,
        parameters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Submit one fixed production transition under a stable job identity."""

        spec = OPERATIONS.get(operation)
        if spec is None:
            raise ProductionError(f"unsupported production operation: {operation}")
        require_identifier(entity_id, f"{spec.entity_type}_id")
        if spec.requires_source and source_version is None:
            raise ProductionError(f"{operation} requires an exact source_version")
        if source_version is not None:
            require_version(source_version)
        provided = dict(parameters or {})
        unknown = sorted(set(provided) - spec.allowed_parameters)
        if unknown:
            raise ProductionError(f"unsupported parameters for {operation}: {', '.join(unknown)}")
        self._validate_parameters(operation, provided)
        store = self._store(project_root)
        if source_version is not None:
            store.exact_version(spec.entity_type, entity_id, source_version)
            if spec.required_gate:
                promoted = store.entity_state(spec.entity_type, entity_id)["promoted_versions"]
                if promoted.get(spec.required_gate) != source_version:
                    raise ProductionError(
                        f"{operation} requires source {source_version} to pass the "
                        f"{spec.required_gate} review gate"
                    )
        payload: dict[str, object] = {
            "entity_type": spec.entity_type,
            "entity_id": entity_id,
            "source_version": source_version,
            "parameters": provided,
        }
        return self.jobs.submit(
            store,
            operation=operation,
            payload=payload,
            executor=self._execute_job,
        )

    def promote_version(
        self,
        project_root: str,
        *,
        asset_type: str,
        asset_id: str,
        version: str,
        gate: str,
        reviewer: str,
        notes: str = "",
    ) -> dict[str, object]:
        """Record an explicit review approval without changing version bytes."""

        if gate not in GATE_REQUIREMENTS:
            raise ProductionError(f"unsupported review gate: {gate}")
        expected_type, required_operations = GATE_REQUIREMENTS[gate]
        if asset_type != expected_type:
            raise ProductionError(f"{gate} is a {expected_type} review gate")
        if not reviewer.strip():
            raise ProductionError("reviewer is required")
        store = self._store(project_root)
        record = store.exact_version(asset_type, asset_id, require_version(version))
        lineage = self._lineage_operations(store, asset_type, asset_id, record)
        missing = sorted(required_operations - lineage)
        if missing:
            raise ProductionError(
                f"{version} cannot pass {gate}; lineage is missing: {', '.join(missing)}"
            )
        return store.append_event(
            asset_type,
            asset_id,
            payload={
                "event": "promote",
                "version": version,
                "gate": gate,
                "reviewer": reviewer.strip(),
                "notes": notes.strip(),
            },
        )

    def archive_version(
        self,
        project_root: str,
        *,
        asset_type: str,
        asset_id: str,
        version: str,
        reason: str,
    ) -> dict[str, object]:
        """Archive a version without deleting its files or immutable record."""

        if not reason.strip():
            raise ProductionError("archive reason is required")
        store = self._store(project_root)
        store.exact_version(asset_type, asset_id, require_version(version))
        state = store.entity_state(asset_type, asset_id)
        in_use = [
            gate for gate, selected in state["promoted_versions"].items() if selected == version
        ]
        if in_use:
            raise ProductionError(
                f"cannot archive promoted version {version}; replace gates first: "
                f"{', '.join(in_use)}"
            )
        return store.append_event(
            asset_type,
            asset_id,
            payload={"event": "archive", "version": version, "reason": reason.strip()},
        )

    def validate_character(
        self, project_root: str, *, character_id: str, version: str
    ) -> dict[str, object]:
        """Validate exact character evidence without changing project state."""

        store = self._store(project_root)
        record = store.exact_version("character", character_id, require_version(version))
        root = store.version_root("character", character_id, version)
        checks: list[dict[str, object]] = []
        for artifact in record.get("artifacts", []):
            path = root / str(artifact["path"])
            observed = file_digest(path) if path.is_file() else None
            expected = {"sha256": artifact.get("sha256"), "size": artifact.get("size")}
            checks.append(
                {
                    "check": f"artifact:{artifact['path']}",
                    "passed": observed == expected,
                    "expected": expected,
                    "observed": observed,
                }
            )
        lineage = self._lineage_operations(store, "character", character_id, record)
        for operation in ("create_character", "create_character_mesh", "create_uv_and_materials"):
            checks.append({"check": f"lineage:{operation}", "passed": operation in lineage})
        return {
            "character_id": character_id,
            "version": version,
            "valid": all(bool(check["passed"]) for check in checks),
            "checks": checks,
        }

    def package_asset(
        self,
        project_root: str,
        *,
        asset_type: str,
        asset_id: str,
        version: str,
    ) -> dict[str, object]:
        """Submit packaging for one exact character or shot version."""

        if asset_type not in {"character", "shot"}:
            raise ProductionError("asset_type must be character or shot")
        store = self._store(project_root)
        store.exact_version(asset_type, asset_id, require_version(version))
        delivery_id = f"asset-{asset_id}"
        payload = {
            "delivery_kind": "asset",
            "delivery_id": delivery_id,
            "asset_type": asset_type,
            "asset_id": asset_id,
            "version": version,
        }
        return self.jobs.submit(
            store,
            operation="package_asset",
            payload=payload,
            executor=self._execute_job,
        )

    def package_episode(
        self,
        project_root: str,
        *,
        episode_id: str,
        shot_versions: Mapping[str, str],
    ) -> dict[str, object]:
        """Submit a package containing exact, caller-selected shot versions."""

        require_identifier(episode_id, "episode_id")
        if not shot_versions:
            raise ProductionError("shot_versions cannot be empty")
        store = self._store(project_root)
        selected: dict[str, str] = {}
        for shot_id, version in sorted(shot_versions.items()):
            require_identifier(shot_id, "shot_id")
            store.exact_version("shot", shot_id, require_version(version))
            selected[shot_id] = version
        payload = {
            "delivery_kind": "episode",
            "delivery_id": f"episode-{episode_id}",
            "episode_id": episode_id,
            "shot_versions": selected,
        }
        return self.jobs.submit(
            store,
            operation="package_episode",
            payload=payload,
            executor=self._execute_job,
        )

    def get_job_status(self, project_root: str, *, job_id: str) -> dict[str, object]:
        """Read a persistent job record."""

        return self.jobs.status(self._store(project_root), job_id)

    @serialize_production
    def _execute_job(
        self, store: ProjectStore, job: Mapping[str, Any], cancellation: threading.Event
    ) -> dict[str, object]:
        operation = str(job["operation"])
        payload = dict(job["payload"])
        if operation in OPERATIONS:
            return self._execute_version_operation(store, operation, payload, job, cancellation)
        if operation in {"package_asset", "package_episode"}:
            return self._execute_package(store, operation, payload, job, cancellation)
        raise ProductionError(f"job operation is unsupported: {operation}")

    def _execute_version_operation(
        self,
        store: ProjectStore,
        operation: str,
        payload: Mapping[str, Any],
        job: Mapping[str, Any],
        cancellation: threading.Event,
    ) -> dict[str, object]:
        spec = OPERATIONS[operation]
        entity_id = str(payload["entity_id"])
        source_version = payload.get("source_version")
        parameters = dict(payload.get("parameters", {}))
        source_blend: Path | None = None
        if isinstance(source_version, str):
            source_record = store.exact_version(spec.entity_type, entity_id, source_version)
            source_root = store.version_root(spec.entity_type, entity_id, source_version)
            blend_artifact = next(
                (
                    source_root / str(artifact["path"])
                    for artifact in source_record.get("artifacts", [])
                    if str(artifact.get("path", "")).endswith(".blend")
                ),
                None,
            )
            if blend_artifact is not None:
                source_blend = blend_artifact
            elif operation != "create_character":
                raise ProductionError(f"source {entity_id}@{source_version} has no Blender file")
        if operation == "assemble_shot":
            asset_versions = parameters.pop("asset_versions", {})
            if not isinstance(asset_versions, Mapping) or not asset_versions:
                raise ProductionError("assemble_shot requires asset_versions")
            asset_files: list[str] = []
            for character_id, version in sorted(asset_versions.items()):
                record = store.exact_version("character", str(character_id), str(version))
                root = store.version_root("character", str(character_id), str(version))
                selected = next(
                    (
                        root / str(artifact["path"])
                        for artifact in record.get("artifacts", [])
                        if str(artifact.get("path", "")).endswith(".blend")
                    ),
                    None,
                )
                if selected is None:
                    raise ProductionError(f"character {character_id}@{version} has no Blender file")
                asset_files.append(str(selected))
            parameters["asset_files"] = asset_files
        version, root = store.reserve_version(
            spec.entity_type,
            entity_id,
            operation=operation,
            parent_version=str(source_version) if source_version else None,
            parameters=dict(payload.get("parameters", {})),
        )
        if (root / "record.json").is_file():
            record = store.read_json(root / "record.json")
            return {"entity_type": spec.entity_type, "entity_id": entity_id,
                    "version": version, "stage": record["stage"],
                    "record_hash": payload_hash(record)}
        try:
            artifacts = self.blender.run(
                store=store,
                version_root=root,
                operation=operation,
                source_blend=source_blend,
                parameters=parameters,
                cancellation=cancellation,
            )
            record = store.complete_version(root, stage=spec.stage, artifacts=artifacts)
            return {
                "entity_type": spec.entity_type,
                "entity_id": entity_id,
                "version": version,
                "stage": spec.stage,
                "record_hash": payload_hash(record),
            }
        except Exception as exc:
            store.fail_version(root, str(exc))
            raise

    def _execute_package(
        self,
        store: ProjectStore,
        operation: str,
        payload: Mapping[str, Any],
        job: Mapping[str, Any],
        cancellation: threading.Event,
    ) -> dict[str, object]:
        if cancellation.is_set():
            raise ProductionError("packaging was cancelled")
        delivery_id = str(payload["delivery_id"])
        version, root = store.reserve_version(
            "delivery",
            delivery_id,
            operation=operation,
            parent_version=None,
            parameters=dict(payload),
        )
        if (root / "record.json").is_file():
            record = store.read_json(root / "record.json")
            return {"entity_type": "delivery", "entity_id": delivery_id,
                    "version": version, "package": f"{delivery_id}-{version}.zip",
                    "record_hash": payload_hash(record)}
        package_path = root / f"{delivery_id}-{version}.zip"
        manifest_path = root / "package-manifest.json"
        try:
            manifest: dict[str, object] = {
                "schema": "ceratops-blender-package.v1",
                "delivery_id": delivery_id,
                "version": version,
                "selection": dict(payload),
            }
            store.write_json(manifest_path, manifest)
            with zipfile.ZipFile(package_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(manifest_path, "package-manifest.json")
                if operation == "package_asset":
                    asset_type = str(payload["asset_type"])
                    asset_id = str(payload["asset_id"])
                    selected_version = str(payload["version"])
                    source = store.version_root(asset_type, asset_id, selected_version)
                    self._add_version_to_zip(
                        archive, source, f"{asset_type}s/{asset_id}/{selected_version}"
                    )
                else:
                    for shot_id, selected_version in sorted(payload["shot_versions"].items()):
                        source = store.version_root("shot", str(shot_id), str(selected_version))
                        self._add_version_to_zip(
                            archive, source, f"shots/{shot_id}/{selected_version}"
                        )
            record = store.complete_version(
                root,
                stage="packaged",
                artifacts=[manifest_path, package_path],
            )
            return {
                "entity_type": "delivery",
                "entity_id": delivery_id,
                "version": version,
                "package": package_path.name,
                "record_hash": payload_hash(record),
            }
        except Exception as exc:
            store.fail_version(root, str(exc))
            raise

    @staticmethod
    def _add_version_to_zip(archive: zipfile.ZipFile, source: Path, prefix: str) -> None:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                archive.write(path, f"{prefix}/{path.relative_to(source).as_posix()}")

    @staticmethod
    def _entity_ids(store: ProjectStore, entity_type: str) -> list[str]:
        plural = {"character": "characters", "shot": "shots", "delivery": "deliveries"}[entity_type]
        root = store.state_root / plural
        if not root.is_dir():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    @staticmethod
    def _validate_parameters(operation: str, parameters: Mapping[str, object]) -> None:
        allowed_values = {
            "style": {"stylized", "realistic"}
            if operation == "create_character"
            else {"short", "bob", "mohawk"},
            "body_type": {"slender", "neutral", "heroic"},
            "rig_type": {"biped"},
            "blendshape_set": {"basic"},
            "preset": {"three_point"},
            "motion": {"blocking"},
            "interpolation": {"BEZIER", "LINEAR", "CONSTANT"},
        }
        for key, accepted in allowed_values.items():
            if key in parameters and parameters[key] not in accepted:
                raise ProductionError(f"{key} must be one of: {', '.join(sorted(accepted))}")
        if operation == "assemble_shot":
            selected = parameters.get("asset_versions")
            if not isinstance(selected, Mapping) or not selected:
                raise ProductionError("asset_versions must map character IDs to exact versions")
            for character_id, version in selected.items():
                require_identifier(str(character_id), "character_id")
                require_version(str(version))
        if operation == "sync_lips":
            cues = parameters.get("cues")
            if not isinstance(cues, Sequence) or isinstance(cues, (str, bytes)):
                raise ProductionError("cues must be a list of frame/value objects")
            for cue in cues:
                if not isinstance(cue, Mapping) or "frame" not in cue:
                    raise ProductionError("each lip-sync cue requires a frame")
                frame = cue["frame"]
                value = cue.get("value", 1.0)
                if isinstance(frame, bool) or not isinstance(frame, int) or frame <= 0:
                    raise ProductionError("lip-sync cue frames must be positive integers")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not 0.0 <= float(value) <= 1.0
                ):
                    raise ProductionError("lip-sync cue values must be between 0 and 1")
        for key in ("resolution_x", "resolution_y", "frame_start", "frame_end", "fps"):
            if key in parameters:
                value = parameters[key]
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ProductionError(f"{key} must be a positive integer")
        for key in ("target_faces", "strand_count", "angle_count"):
            if key in parameters:
                value = parameters[key]
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ProductionError(f"{key} must be a positive integer")
        if "subdivision_levels" in parameters:
            levels = parameters["subdivision_levels"]
            if isinstance(levels, bool) or not isinstance(levels, int) or not 0 <= levels <= 2:
                raise ProductionError("subdivision_levels must be between 0 and 2")
        for key, size in (("base_color", 4), ("position", 3), ("target", 3)):
            if key in parameters:
                values = parameters[key]
                if (
                    not isinstance(values, Sequence)
                    or isinstance(values, (str, bytes))
                    or len(values) != size
                    or any(
                        isinstance(item, bool) or not isinstance(item, (int, float))
                        for item in values
                    )
                ):
                    raise ProductionError(f"{key} must contain {size} numeric values")
        color = parameters.get("base_color")
        if isinstance(color, Sequence) and any(not 0.0 <= float(value) <= 1.0 for value in color):
            raise ProductionError("base_color values must be between 0 and 1")
        numeric_ranges = {
            "height_m": (0.5, 3.0),
            "roughness": (0.0, 1.0),
            "lens_mm": (10.0, 300.0),
            "intensity": (0.01, 1_000_000.0),
            "strength": (0.0, 1.0),
        }
        for key, (minimum, maximum) in numeric_ranges.items():
            if key not in parameters:
                continue
            value = parameters[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not minimum <= float(value) <= maximum
            ):
                raise ProductionError(f"{key} must be between {minimum} and {maximum}")
        target_faces = parameters.get("target_faces")
        if isinstance(target_faces, int) and target_faces < 500:
            raise ProductionError("target_faces must be at least 500")
        strand_count = parameters.get("strand_count")
        if isinstance(strand_count, int) and strand_count > 200:
            raise ProductionError("strand_count cannot exceed 200")
        angle_count = parameters.get("angle_count")
        if isinstance(angle_count, int) and angle_count > 8:
            raise ProductionError("angle_count cannot exceed 8")
        frame_start = parameters.get("frame_start")
        frame_end = parameters.get("frame_end")
        if isinstance(frame_start, int) and isinstance(frame_end, int) and frame_start > frame_end:
            raise ProductionError("frame_start cannot exceed frame_end")

    @staticmethod
    def _lineage_operations(
        store: ProjectStore,
        entity_type: str,
        entity_id: str,
        record: Mapping[str, Any],
    ) -> set[str]:
        operations: set[str] = set()
        current = dict(record)
        visited: set[str] = set()
        while True:
            operation = current.get("operation")
            if isinstance(operation, str):
                operations.add(operation)
            version = current.get("version")
            if not isinstance(version, str) or version in visited:
                break
            visited.add(version)
            parent = current.get("parent_version")
            if not isinstance(parent, str):
                break
            current = store.exact_version(entity_type, entity_id, parent)
        return operations
