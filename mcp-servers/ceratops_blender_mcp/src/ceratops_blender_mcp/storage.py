"""Immutable production-version storage and append-only lifecycle events.

The repository source never owns production media. Each caller-selected Blender
project owns a hidden ``.ceratops-blender`` directory. Completed version records
are immutable; promotion and archival are separate append-only events. Atomic
control-file writes use process-unique siblings and always remove them on exit.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

STATE_DIR = ".ceratops-blender"
VERSION_RE = re.compile(r"^v(?P<number>(?!0+$)[0-9]{4,})$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DEFAULT_ACTIVE_VERSION_LIMIT = 25
DEFAULT_JOB_HISTORY_LIMIT = 100


class ProductionError(RuntimeError):
    """A user-correctable production contract failure."""


def utc_now() -> str:
    """Return an RFC 3339 UTC timestamp with second precision."""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> str:
    """Serialize stable records for hashing, persistence, and comparisons."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(value: object) -> str:
    """Return the SHA-256 identity of a JSON-compatible value."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> dict[str, object]:
    """Describe one completed file without loading it into memory."""

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "size": size}


def require_identifier(value: str, label: str) -> str:
    """Reject path-like or ambiguous caller identifiers."""

    if not IDENTIFIER_RE.fullmatch(value):
        raise ProductionError(
            f"{label} must start with a lowercase letter and contain only "
            "lowercase letters, digits, underscores, or hyphens"
        )
    return value


def require_version(value: str) -> str:
    """Validate the canonical vNNNN production-version form."""

    if not VERSION_RE.fullmatch(value):
        raise ProductionError("version must use the canonical vNNNN form")
    return value


class ProjectStore:
    """Own project-local records while preserving immutable version payloads."""

    def __init__(self, project_root: str | Path) -> None:
        root = Path(project_root).expanduser()
        if not root.is_absolute():
            raise ProductionError("project_root must be an absolute path")
        self.root = root.resolve()
        self.state_root = self.root / STATE_DIR
        self.lock_path = self.state_root / "state.lock"

    @property
    def exists(self) -> bool:
        """Return whether this directory contains an initialized project."""

        return (self.state_root / "project.json").is_file()

    def initialize(self) -> dict[str, object]:
        """Initialize project control data during an authorized write operation."""

        self.root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        project_path = self.state_root / "project.json"
        with self.lock():
            if project_path.is_file():
                return self.read_json(project_path)
            record: dict[str, object] = {
                "schema": "ceratops-blender-project.v1",
                "project_id": str(uuid.uuid5(uuid.NAMESPACE_URL, self.root.as_uri())),
                "created_at": utc_now(),
                "active_version_limit": DEFAULT_ACTIVE_VERSION_LIMIT,
                "job_history_limit": DEFAULT_JOB_HISTORY_LIMIT,
            }
            self.write_json(project_path, record)
            return record

    def lock(self) -> FileLock:
        """Return the project-scoped native file lock for control-plane writes."""

        self.state_root.mkdir(parents=True, exist_ok=True)
        return FileLock(self.lock_path, timeout=30)

    @staticmethod
    def read_json(path: Path) -> dict[str, Any]:
        """Read a JSON object or raise a production-facing contract error."""

        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductionError(f"cannot read record {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ProductionError(f"record must contain a JSON object: {path}")
        return value

    @staticmethod
    def write_json(path: Path, value: Mapping[str, object]) -> None:
        """Atomically replace a mutable control record and remove scratch bytes."""

        path.parent.mkdir(parents=True, exist_ok=True)
        scratch = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with scratch.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(scratch, path)
        finally:
            scratch.unlink(missing_ok=True)

    def project_record(self) -> dict[str, Any] | None:
        """Read project metadata without creating runtime state."""

        path = self.state_root / "project.json"
        return self.read_json(path) if path.is_file() else None

    def entity_root(self, entity_type: str, entity_id: str) -> Path:
        """Resolve one validated character, shot, or delivery directory."""

        if entity_type not in {"character", "shot", "delivery"}:
            raise ProductionError(f"unsupported entity type: {entity_type}")
        require_identifier(entity_id, f"{entity_type}_id")
        plural = {"character": "characters", "shot": "shots", "delivery": "deliveries"}[entity_type]
        return self.state_root / plural / entity_id

    def version_root(self, entity_type: str, entity_id: str, version: str) -> Path:
        """Resolve one exact version directory."""

        require_version(version)
        return self.entity_root(entity_type, entity_id) / "versions" / version

    def version_records(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        """Return completed immutable version records in version order."""

        versions_root = self.entity_root(entity_type, entity_id) / "versions"
        if not versions_root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for child in sorted(versions_root.iterdir(), key=lambda path: path.name):
            if not child.is_dir() or not VERSION_RE.fullmatch(child.name):
                continue
            record = child / "record.json"
            if record.is_file():
                records.append(self.read_json(record))
        return records

    def exact_version(self, entity_type: str, entity_id: str, version: str) -> dict[str, Any]:
        """Read one completed version and never select an ambient latest value."""

        path = self.version_root(entity_type, entity_id, version) / "record.json"
        if not path.is_file():
            raise ProductionError(
                f"completed {entity_type} version does not exist: {entity_id}@{version}"
            )
        return self.read_json(path)

    def events(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        """Read append-only promotion and archive events in creation order."""

        root = self.entity_root(entity_type, entity_id) / "events"
        if not root.is_dir():
            return []
        return [
            self.read_json(path) for path in sorted(root.glob("*.json"), key=lambda item: item.name)
        ]

    def entity_state(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        """Derive mutable lifecycle state from immutable records and events."""

        records = self.version_records(entity_type, entity_id)
        archived: set[str] = set()
        promoted: dict[str, str] = {}
        approvals: list[dict[str, Any]] = []
        for event in self.events(entity_type, entity_id):
            event_type = event.get("event")
            version = event.get("version")
            if event_type == "archive" and isinstance(version, str):
                archived.add(version)
            elif event_type == "promote" and isinstance(version, str):
                gate = event.get("gate")
                if isinstance(gate, str):
                    promoted[gate] = version
                approvals.append(event)
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "versions": records,
            "archived_versions": sorted(archived),
            "promoted_versions": promoted,
            "approvals": approvals,
        }

    def reserve_version(
        self,
        entity_type: str,
        entity_id: str,
        *,
        operation: str,
        parent_version: str | None,
        parameters: Mapping[str, object],
    ) -> tuple[str, Path]:
        """Reserve a never-reused version and record its exact production request."""

        self.initialize()
        entity = self.entity_root(entity_type, entity_id)
        versions = entity / "versions"
        with self.lock():
            state = self.entity_state(entity_type, entity_id)
            # The production lock excludes live writers. Discard all abandoned
            # partials even when a completed output already satisfies the request.
            if versions.is_dir():
                for partial in versions.iterdir():
                    if (partial.is_dir() and VERSION_RE.fullmatch(partial.name)
                            and not (partial / "record.json").exists()):
                        if (partial.is_symlink() or partial.is_junction()
                                or not partial.resolve().is_relative_to(versions.resolve())):
                            raise ProductionError("partial version must stay inside its owner")
                        shutil.rmtree(partial)
            # Match desired output facts, never an earlier worker or failed stage.
            for record in state["versions"]:
                if (record.get("version") not in state["archived_versions"]
                        and record.get("operation") == operation
                        and record.get("parent_version") == parent_version
                        and record.get("parameters") == dict(parameters)):
                    version = str(record["version"])
                    return version, self.version_root(entity_type, entity_id, version)
            archived = set(state["archived_versions"])
            active = [
                record for record in state["versions"] if record.get("version") not in archived
            ]
            project = self.project_record() or {}
            limit = int(project.get("active_version_limit", DEFAULT_ACTIVE_VERSION_LIMIT))
            if len(active) >= limit:
                raise ProductionError(
                    f"{entity_id} has {len(active)} active versions; archive one before "
                    f"creating another (limit {limit})"
                )
            versions.mkdir(parents=True, exist_ok=True)
            numbers = [
                int(match.group("number"))
                for path in versions.iterdir()
                if path.is_dir() and (match := VERSION_RE.fullmatch(path.name))
            ]
            version = f"v{max(numbers, default=0) + 1:04d}"
            destination = versions / version
            destination.mkdir()
            request = {
                "schema": "ceratops-blender-version-request.v1",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "version": version,
                "operation": operation,
                "parent_version": parent_version,
                "parameters": dict(parameters),
                "created_at": utc_now(),
            }
            self.write_json(destination / "request.json", request)
            return version, destination

    def complete_version(
        self,
        version_root: Path,
        *,
        stage: str,
        artifacts: Iterable[Path],
        extra: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Publish an immutable completion record after all output bytes exist."""

        record_path = version_root / "record.json"
        if record_path.exists():
            raise ProductionError(f"completed version is immutable: {version_root.name}")
        request = self.read_json(version_root / "request.json")
        artifact_records: list[dict[str, object]] = []
        for artifact in artifacts:
            if not artifact.is_file():
                raise ProductionError(f"expected output was not produced: {artifact}")
            try:
                relative = artifact.relative_to(version_root).as_posix()
            except ValueError as exc:
                raise ProductionError("version artifacts must stay inside their version") from exc
            artifact_records.append({"path": relative, **file_digest(artifact)})
        record: dict[str, object] = {
            **request,
            "schema": "ceratops-blender-version.v1",
            "stage": stage,
            "artifacts": artifact_records,
            "completed_at": utc_now(),
        }
        if extra:
            record.update(extra)
        self.write_json(record_path, record)
        return record

    def fail_version(self, version_root: Path, message: str) -> None:
        """Retain an explicit failed attempt without making it a completed version."""

        failure = version_root / "failure.json"
        if not failure.exists():
            self.write_json(
                failure,
                {
                    "schema": "ceratops-blender-version-failure.v1",
                    "failed_at": utc_now(),
                    "message": message[:4000],
                },
            )

    def append_event(
        self,
        entity_type: str,
        entity_id: str,
        *,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        """Write one idempotent immutable lifecycle event."""

        self.initialize()
        events_root = self.entity_root(entity_type, entity_id) / "events"
        with self.lock():
            existing_paths = sorted(events_root.glob("*.json")) if events_root.is_dir() else []
            coordinate = "gate" if payload.get("event") == "promote" else "version"
            for existing_path in reversed(existing_paths):
                existing = self.read_json(existing_path)
                if (existing.get("event") == payload.get("event")
                        and existing.get(coordinate) == payload.get(coordinate)):
                    if all(existing.get(key) == value for key, value in payload.items()):
                        return existing
                    break
            sequences = []
            for existing_path in existing_paths:
                prefix = existing_path.name.split("-", 1)[0]
                if prefix.isdigit():
                    sequences.append(int(prefix))
            sequence = max(sequences, default=0) + 1
            event_id = payload_hash(
                {"entity": entity_id, "sequence": sequence, "payload": payload}
            )[:20]
            path = events_root / f"{sequence:08d}-{event_id}.json"
            record = {
                "schema": "ceratops-blender-event.v1",
                "event_id": event_id,
                "sequence": sequence,
                "created_at": utc_now(),
                **payload,
            }
            self.write_json(path, record)
        return record

    def copy_inputs(self, sources: Iterable[str | Path], destination: Path) -> list[Path]:
        """Copy exact reference inputs into a new immutable version."""

        outputs: list[Path] = []
        destination.mkdir(parents=True, exist_ok=True)
        for source_value in sources:
            source = Path(source_value).expanduser().resolve()
            if not source.is_file():
                raise ProductionError(f"reference file does not exist: {source}")
            target = destination / source.name
            if target.exists():
                raise ProductionError(f"duplicate reference filename: {source.name}")
            shutil.copy2(source, target)
            outputs.append(target)
        return outputs
