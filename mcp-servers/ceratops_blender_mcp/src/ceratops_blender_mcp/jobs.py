"""Bounded current-worker monitoring for Blender and packaging operations."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from filelock import BaseFileLock, FileLock, Timeout

from .storage import (
    DEFAULT_JOB_HISTORY_LIMIT,
    ProductionError,
    ProjectStore,
    payload_hash,
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
        self._active: dict[tuple[str, str], str] = {}
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
        payload: Mapping[str, object],
        executor: JobExecutor,
    ) -> dict[str, object]:
        """Start fresh work; identical active calls share only their live worker."""

        store.initialize()
        self._prune(store)
        job_id = f"job_{uuid.uuid4().hex}"
        path = self._job_path(store, job_id)
        with self._guard, store.lock():
            selection = (
                str(store.root), payload_hash({"operation": operation, "payload": payload})
            )
            active = self._active.get(selection)
            if active is not None:
                return self._public(store.read_json(self._job_path(store, active)))
            record: dict[str, object] = {
                "schema": "ceratops-blender-job.v1",
                "job_id": job_id,
                "operation": operation,
                "status": "queued",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            # The lock covers queueing and execution, including across server instances.
            worker_lock = FileLock(str(path.with_suffix(".worker.lock")),
                                   thread_local=False)
            worker_lock.acquire(timeout=0)
            try:
                store.write_json(path, record)
                self._active[selection] = job_id
                self._launch(store, job_id, executor, dict(payload), selection, worker_lock)
            except BaseException:
                self._active.pop(selection, None)
                worker_lock.release()
                path.with_suffix(".worker.lock").unlink(missing_ok=True)
                raise
        return self._public(record)

    def _launch(self, store: ProjectStore, job_id: str, executor: JobExecutor,
                payload: dict[str, object], selection: tuple[str, str],
                worker_lock: BaseFileLock) -> None:
        key = self._key(store, job_id)
        with self._guard:
            current = self._futures.get(key)
            if current is not None and not current.done():
                return
            cancellation = threading.Event()
            self._cancellations[key] = cancellation
            self._futures[key] = self._pool.submit(
                self._run_job, store, job_id, executor, cancellation, payload, selection,
                worker_lock
            )

    def _run_job(
        self,
        store: ProjectStore,
        job_id: str,
        executor: JobExecutor,
        cancellation: threading.Event,
        payload: dict[str, object],
        selection: tuple[str, str],
        worker_lock: BaseFileLock,
    ) -> None:
        path = self._job_path(store, job_id)
        try:
            with store.lock():
                record = store.read_json(path)
                record.update(
                    {
                        "status": "running",
                        "started_at": utc_now(),
                        "updated_at": utc_now(),
                    }
                )
                record.pop("error", None)
                store.write_json(path, record)
            # Inputs live only in this worker. Monitoring files cannot restart it.
            result = executor(store, {**record, "payload": payload}, cancellation)
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
            with self._guard:
                self._active.pop(selection, None)
                self._futures.pop(self._key(store, job_id), None)
                self._cancellations.pop(self._key(store, job_id), None)
            worker_lock.release()
            path.with_suffix(".worker.lock").unlink(missing_ok=True)
            self._prune(store)

    def status(self, store: ProjectStore, job_id: str) -> dict[str, object]:
        """Return current state and mark abandoned in-process work interrupted."""

        path = self._job_path(store, job_id)
        if not path.is_file():
            raise ProductionError(f"job does not exist: {job_id}")
        with self._guard, store.lock():
            record = store.read_json(path)
            self._mark_abandoned(store, path, record)
        return self._public(record)

    def _mark_abandoned(self, store: ProjectStore, path: Path, record: dict[str, Any]) -> None:
        """Read only liveness locks; never use an old execution as work input."""
        if (record.get("status") not in {"queued", "running"}
                or self._key(store, str(record["job_id"])) in self._futures):
            return
        lock_path = path.with_suffix(".worker.lock")
        try:
            with FileLock(str(lock_path), timeout=0):
                record.update(status="interrupted", finished_at=utc_now(), updated_at=utc_now(),
                              error="worker ended before reaching a terminal state")
                store.write_json(path, record)
            lock_path.unlink(missing_ok=True)
        except Timeout:
            pass

    def _prune(self, store: ProjectStore) -> None:
        """Bound completed operational history; active job records are never pruned."""

        project = store.project_record() or {}
        limit = int(project.get("job_history_limit", DEFAULT_JOB_HISTORY_LIMIT))
        jobs_root = store.state_root / "jobs"
        if not jobs_root.is_dir():
            return
        with self._guard, store.lock():
            terminal: list[tuple[str, Path]] = []
            for path in jobs_root.glob("job_*.json"):
                record = store.read_json(path)
                self._mark_abandoned(store, path, record)
                if record.get("status") in TERMINAL_STATES:
                    terminal.append((str(record.get("updated_at", "")), path))
            terminal.sort(reverse=True)
            for _, path in terminal[limit:]:
                path.unlink(missing_ok=True)

        # An interrupted initial write may leave a lock without a monitoring record.
        for lock_path in jobs_root.glob("job_*.worker.lock"):
            record_path = lock_path.with_name(lock_path.name.removesuffix(".worker.lock") + ".json")
            if not record_path.exists():
                try:
                    with FileLock(str(lock_path), timeout=0):
                        pass
                    lock_path.unlink(missing_ok=True)
                except Timeout:
                    pass

    @staticmethod
    def _public(record: Mapping[str, Any]) -> dict[str, object]:
        keys = (
            "job_id",
            "operation",
            "status",
            "created_at",
            "updated_at",
            "started_at",
            "finished_at",
            "cancel_requested",
            "result",
            "error",
        )
        return {key: record[key] for key in keys if key in record}
