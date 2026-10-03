"""Persistent bounded job control for Blender and packaging operations."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .storage import (
    DEFAULT_JOB_HISTORY_LIMIT,
    ProductionError,
    ProjectStore,
    payload_hash,
    require_identifier,
    utc_now,
)

JobExecutor = Callable[[ProjectStore, Mapping[str, Any], threading.Event], dict[str, object]]
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


class JobManager:
    """Run bounded background work while persisting stable caller-visible IDs."""

    def __init__(self, *, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ceratops-blender"
        )
        self._futures: dict[tuple[str, str], Future[None]] = {}
        self._cancellations: dict[tuple[str, str], threading.Event] = {}
        self._guard = threading.RLock()

    @staticmethod
    def _job_path(store: ProjectStore, job_id: str) -> Path:
        return store.state_root / "jobs" / f"{job_id}.json"

    @staticmethod
    def _key(store: ProjectStore, job_id: str) -> tuple[str, str]:
        return (str(store.root), job_id)

    def submit(
        self,
        store: ProjectStore,
        *,
        operation: str,
        request_id: str,
        payload: Mapping[str, object],
        executor: JobExecutor,
    ) -> dict[str, object]:
        """Create or return the job bound to one idempotent request."""

        require_identifier(request_id, "request_id")
        project = store.initialize()
        project_id = str(project["project_id"])
        job_id = f"job_{uuid.uuid5(uuid.NAMESPACE_URL, f'{project_id}:{request_id}').hex}"
        request_hash = payload_hash({"operation": operation, "payload": payload})
        path = self._job_path(store, job_id)
        with store.lock():
            if path.is_file():
                existing = store.read_json(path)
                if existing.get("request_hash") != request_hash:
                    raise ProductionError("request_id is already bound to a different job payload")
                return self._public(existing)
            record: dict[str, object] = {
                "schema": "ceratops-blender-job.v1",
                "job_id": job_id,
                "operation": operation,
                "request_id": request_id,
                "request_hash": request_hash,
                "payload": dict(payload),
                "status": "queued",
                "attempts": 0,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            store.write_json(path, record)
        self._launch(store, job_id, executor)
        return self._public(record)

    def _launch(self, store: ProjectStore, job_id: str, executor: JobExecutor) -> None:
        key = self._key(store, job_id)
        with self._guard:
            current = self._futures.get(key)
            if current is not None and not current.done():
                return
            cancellation = threading.Event()
            self._cancellations[key] = cancellation
            self._futures[key] = self._pool.submit(
                self._run_job, store, job_id, executor, cancellation
            )

    def _run_job(
        self,
        store: ProjectStore,
        job_id: str,
        executor: JobExecutor,
        cancellation: threading.Event,
    ) -> None:
        path = self._job_path(store, job_id)
        try:
            with store.lock():
                record = store.read_json(path)
                record.update(
                    {
                        "status": "running",
                        "attempts": int(record.get("attempts", 0)) + 1,
                        "started_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                record.pop("error", None)
                store.write_json(path, record)
            result = executor(store, record, cancellation)
            status = "cancelled" if cancellation.is_set() else "completed"
            with store.lock():
                record = store.read_json(path)
                record.update(
                    {
                        "status": status,
                        "result": result if status == "completed" else None,
                        "finished_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                store.write_json(path, record)
        except Exception as exc:  # Persist every background-boundary failure.
            with store.lock():
                record = store.read_json(path)
                record.update(
                    {
                        "status": "cancelled" if cancellation.is_set() else "failed",
                        "error": f"{type(exc).__name__}: {exc}"[:4000],
                        "finished_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                store.write_json(path, record)
        finally:
            self._prune(store)

    def status(self, store: ProjectStore, job_id: str) -> dict[str, object]:
        """Return current state and mark abandoned in-process work interrupted."""

        path = self._job_path(store, job_id)
        if not path.is_file():
            raise ProductionError(f"job does not exist: {job_id}")
        with store.lock():
            record = store.read_json(path)
            if record.get("status") in {"queued", "running"}:
                key = self._key(store, job_id)
                with self._guard:
                    future = self._futures.get(key)
                if future is None:
                    record.update(
                        {
                            "status": "interrupted",
                            "error": "server process ended before the job reached a terminal state",
                            "updated_at": utc_now(),
                        }
                    )
                    store.write_json(path, record)
        return self._public(record)

    def cancel(self, store: ProjectStore, job_id: str) -> dict[str, object]:
        """Request cancellation without deleting partial or completed evidence."""

        path = self._job_path(store, job_id)
        if not path.is_file():
            raise ProductionError(f"job does not exist: {job_id}")
        key = self._key(store, job_id)
        with self._guard:
            cancellation = self._cancellations.get(key)
            future = self._futures.get(key)
            if cancellation is not None:
                cancellation.set()
            if future is not None:
                future.cancel()
        with store.lock():
            record = store.read_json(path)
            if record.get("status") in TERMINAL_STATES:
                return self._public(record)
            if record.get("status") == "queued" and (future is None or future.cancelled()):
                record.update(
                    {"status": "cancelled", "finished_at": utc_now(), "updated_at": utc_now()}
                )
            else:
                record.update({"cancel_requested": True, "updated_at": utc_now()})
            store.write_json(path, record)
        return self._public(record)

    def resume(self, store: ProjectStore, job_id: str, executor: JobExecutor) -> dict[str, object]:
        """Rerun a failed, cancelled, or interrupted job under the same stable ID."""

        path = self._job_path(store, job_id)
        if not path.is_file():
            raise ProductionError(f"job does not exist: {job_id}")
        with store.lock():
            record = store.read_json(path)
            if record.get("status") not in {"failed", "cancelled", "interrupted"}:
                raise ProductionError("only failed, cancelled, or interrupted jobs can resume")
            record.update({"status": "queued", "updated_at": utc_now()})
            record.pop("cancel_requested", None)
            record.pop("finished_at", None)
            record.pop("result", None)
            store.write_json(path, record)
        self._launch(store, job_id, executor)
        return self._public(record)

    def _prune(self, store: ProjectStore) -> None:
        """Bound completed operational history; active job records are never pruned."""

        project = store.project_record() or {}
        limit = int(project.get("job_history_limit", DEFAULT_JOB_HISTORY_LIMIT))
        jobs_root = store.state_root / "jobs"
        if not jobs_root.is_dir():
            return
        with store.lock():
            terminal: list[tuple[str, Path]] = []
            for path in jobs_root.glob("job_*.json"):
                record = store.read_json(path)
                if record.get("status") in TERMINAL_STATES:
                    terminal.append((str(record.get("updated_at", "")), path))
            terminal.sort(reverse=True)
            for _, path in terminal[limit:]:
                path.unlink(missing_ok=True)

    @staticmethod
    def _public(record: Mapping[str, Any]) -> dict[str, object]:
        keys = (
            "job_id",
            "operation",
            "request_id",
            "status",
            "attempts",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "cancel_requested",
            "result",
            "error",
        )
        return {key: record[key] for key in keys if key in record}
