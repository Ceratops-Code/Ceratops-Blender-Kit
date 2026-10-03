"""Narrow Blender subprocess boundary for fixed production operations.

The runtime never accepts Python source. It serializes an allowlisted operation
request for the bundled worker and launches Blender in background mode. A
caller-provided cancellation event terminates the owned process before return.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .storage import ProductionError, ProjectStore


class BlenderRuntime:
    """Execute bundled Blender operations through a configured local binary."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or os.environ.get("CERATOPS_BLENDER_EXECUTABLE")

    def resolved_executable(self) -> str:
        """Resolve Blender without mutating PATH or installing software."""

        candidate = self.executable or shutil.which("blender")
        if not candidate:
            raise ProductionError(
                "Blender is unavailable; set CERATOPS_BLENDER_EXECUTABLE "
                "to the local Blender binary"
            )
        path = Path(candidate).expanduser().resolve()
        if not path.is_file():
            raise ProductionError(f"Blender executable does not exist: {path}")
        return str(path)

    def run(
        self,
        *,
        store: ProjectStore,
        version_root: Path,
        operation: str,
        source_blend: Path | None,
        parameters: Mapping[str, object],
        cancellation: threading.Event,
        timeout_seconds: int = 7200,
    ) -> list[Path]:
        """Run one allowlisted worker request and return its produced files."""

        request_path = version_root / "blender-request.json"
        result_path = version_root / "blender-result.json"
        output_blend = version_root / "scene.blend"
        request = {
            "schema": "ceratops-blender-worker-request.v1",
            "operation": operation,
            "parameters": dict(parameters),
            "output_blend": str(output_blend),
            "output_root": str(version_root / "outputs"),
        }
        store.write_json(request_path, request)
        worker = Path(__file__).with_name("execute_blender_job.py")
        command = [self.resolved_executable(), "--background"]
        if source_blend is not None:
            if not source_blend.is_file():
                raise ProductionError(f"source Blender file does not exist: {source_blend}")
            command.append(str(source_blend))
        command.extend(
            [
                "--python",
                str(worker),
                "--",
                "--request",
                str(request_path),
                "--result",
                str(result_path),
            ]
        )
        process = subprocess.Popen(
            command,
            cwd=store.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.monotonic() + timeout_seconds
        output: list[str] = []
        while process.poll() is None:
            if cancellation.is_set() or time.monotonic() >= deadline:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                if cancellation.is_set():
                    raise ProductionError("Blender operation was cancelled")
                raise ProductionError(f"Blender operation exceeded {timeout_seconds} seconds")
            if process.stdout is not None:
                line = process.stdout.readline()
                if line:
                    output.append(line.rstrip())
                    output = output[-80:]
            time.sleep(0.05)
        if process.stdout is not None:
            output.extend(line.rstrip() for line in process.stdout.readlines())
            output = output[-80:]
        if process.returncode != 0:
            detail = "\n".join(output[-20:])
            if result_path.is_file():
                worker_result = store.read_json(result_path)
                detail = str(worker_result.get("error", detail))
            raise ProductionError(
                f"Blender operation {operation} failed with exit code "
                f"{process.returncode}: {detail}"
            )
        if not result_path.is_file():
            raise ProductionError("Blender completed without a worker result record")
        result: dict[str, Any] = store.read_json(result_path)
        if result.get("status") != "completed":
            raise ProductionError(str(result.get("error", "Blender worker did not complete")))
        artifacts: list[Path] = []
        for raw_path in result.get("artifacts", []):
            path = Path(str(raw_path)).resolve()
            try:
                path.relative_to(version_root.resolve())
            except ValueError as exc:
                raise ProductionError(
                    "Blender worker returned an output outside its version"
                ) from exc
            artifacts.append(path)
        return artifacts


class RecordingBlenderRuntime(BlenderRuntime):
    """Deterministic test runtime that writes observable stand-in artifacts."""

    def __init__(self) -> None:
        super().__init__(executable="recording-runtime")
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        *,
        store: ProjectStore,
        version_root: Path,
        operation: str,
        source_blend: Path | None,
        parameters: Mapping[str, object],
        cancellation: threading.Event,
        timeout_seconds: int = 7200,
    ) -> list[Path]:
        del timeout_seconds
        if cancellation.is_set():
            raise ProductionError("Blender operation was cancelled")
        call: dict[str, object] = {
            "operation": operation,
            "source_blend": str(source_blend) if source_blend else None,
            "parameters": dict(parameters),
        }
        self.calls.append(call)
        store.write_json(version_root / "blender-request.json", call)
        blend = version_root / "scene.blend"
        source_bytes = source_blend.read_bytes() if source_blend else b"BLENDER"
        blend.write_bytes(source_bytes + b"\n" + json.dumps(call, sort_keys=True).encode("utf-8"))
        artifacts = [blend]
        if operation.startswith("render_"):
            render = version_root / "outputs" / "frame_0001.png"
            render.parent.mkdir(parents=True, exist_ok=True)
            render.write_bytes(b"PNG\r\n" + operation.encode("ascii"))
            artifacts.append(render)
        return artifacts
