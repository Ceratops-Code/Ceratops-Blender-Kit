from __future__ import annotations

import json
import os
import shutil
import subprocess
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
        source_version=None,
        parameters={"height_m": 1.75, "style": "stylized", "body_type": "neutral"},
    )
    character = run_operation(
        service,
        project,
        operation="create_character_mesh",
        entity_id="hero",
        source_version=str(character["version"]),
        parameters={"subdivision_levels": 1},
    )
    character = run_operation(
        service,
        project,
        operation="retopologize_character",
        entity_id="hero",
        source_version=str(character["version"]),
        parameters={"target_faces": 8000},
    )
    character = run_operation(
        service,
        project,
        operation="create_uv_and_materials",
        entity_id="hero",
        source_version=str(character["version"]),
        parameters={"base_color": [0.4, 0.15, 0.1, 1.0], "roughness": 0.5},
    )
    appearance_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
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
    )
    groom = run_operation(
        service,
        project,
        operation="groom_character",
        entity_id="hero",
        source_version=str(appearance_review["version"]),
        parameters={"style": "short", "strand_count": 8},
    )
    groom_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
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
    )
    rig = run_operation(
        service,
        project,
        operation="rig_character",
        entity_id="hero",
        source_version=str(groom_review["version"]),
        parameters={"rig_type": "biped"},
    )
    rig_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
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
    )
    face = run_operation(
        service,
        project,
        operation="build_face_rig",
        entity_id="hero",
        source_version=str(rig_review["version"]),
        parameters={"blendshape_set": "basic"},
    )
    face_review = run_operation(
        service,
        project,
        operation="render_character_review",
        entity_id="hero",
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
        source_version=None,
        parameters={"frame_start": 1, "frame_end": 2, "fps": 24},
    )
    shot = run_operation(
        service,
        project,
        operation="assemble_shot",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"asset_versions": {"hero": str(face_review["version"])}},
    )
    shot = run_operation(
        service,
        project,
        operation="setup_camera",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"lens_mm": 50.0, "position": [4.0, -6.0, 3.0], "target": [0.0, 0.0, 1.0]},
    )
    shot = run_operation(
        service,
        project,
        operation="light_shot",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"preset": "three_point", "intensity": 500.0},
    )
    shot = run_operation(
        service,
        project,
        operation="animate_shot",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"motion": "blocking", "interpolation": "BEZIER"},
    )
    shot = run_operation(
        service,
        project,
        operation="sync_lips",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"cues": [{"frame": 1, "value": 0.0}, {"frame": 2, "value": 1.0}]},
    )
    shot = run_operation(
        service,
        project,
        operation="add_secondary_motion",
        entity_id="shot-010",
        source_version=str(shot["version"]),
        parameters={"strength": 0.1},
    )
    preview = run_operation(
        service,
        project,
        operation="render_shot_preview",
        entity_id="shot-010",
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
    )
    final = run_operation(
        service,
        project,
        operation="render_shot_final",
        entity_id="shot-010",
        source_version=str(preview["version"]),
        parameters={"resolution_x": 64, "resolution_y": 64, "frame_start": 1, "frame_end": 2},
    )
    package = service.package_episode(
        str(project),
        episode_id="episode-001",
        shot_versions={"shot-010": str(final["version"])},
    )
    packaged = wait_for_job(service, project, str(package["job_id"]))
    assert packaged["status"] == "completed", packaged


RAM_GUARD_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "limit-blender-ram.ps1"
POWERSHELL_EXECUTABLE = shutil.which("powershell.exe") or shutil.which("pwsh")
RAM_GUARD_WINDOWS_ONLY = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL_EXECUTABLE,
    reason="the Blender working-set controller uses Windows memory APIs",
)


def run_ram_guard_powershell(body: str, *, fake_adapter: bool = True) -> dict[str, object]:
    """Exercise the public script without attaching to the user's Blender processes."""
    script_path = str(RAM_GUARD_SCRIPT).replace("'", "''")
    command = (
        "$ErrorActionPreference = 'Stop'; "
        f". '{script_path}';\n"
        + (RAM_GUARD_FAKE_ADAPTER if fake_adapter else "")
        + body
    )
    completed = subprocess.run(
        [
            str(POWERSHELL_EXECUTABLE),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = completed.stdout.splitlines()
    result_lines = [
        line.removeprefix("GUARD_RESULT:") for line in lines if line.startswith("GUARD_RESULT:")
    ]
    assert len(result_lines) == 1, completed.stdout + completed.stderr
    return json.loads(result_lines[0])


# The adapter is deliberately independent of the native implementation. It exposes
# interruptions on either side of the memory API effect, exited handles, and an
# outside writer without imposing any memory limit on real Blender instances.
RAM_GUARD_FAKE_ADAPTER = r"""
class TestBlenderRamProcess {
    [int] $ProcessId
    [UInt64] $ResidentBytes
    [bool] $Exited = $false
    [bool] $ThrowRead = $false
    [string] $ThrowOnApply = ''
    [int] $ApplyCount = 0
    [int] $DisposeCount = 0
    [object] $Limits
    TestBlenderRamProcess([int] $processId, [UInt64] $residentBytes) {
        $this.ProcessId = $processId
        $this.ResidentBytes = $residentBytes
        $this.Limits = [pscustomobject]@{ Minimum=[UInt64](1MB); Maximum=[UInt64](16GB); Flags=[UInt32]2 }
    }
    [object] ReadLimits() {
        if ($this.ThrowRead) { throw 'mock ReadLimits failure' }
        return [pscustomobject]@{ Minimum=$this.Limits.Minimum; Maximum=$this.Limits.Maximum; Flags=$this.Limits.Flags }
    }
    [UInt64] WorkingSetBytes() { return $this.ResidentBytes }
    [bool] HasExited() { return $this.Exited }
    [void] ApplyLimits([UInt64] $minimum, [UInt64] $maximum, [UInt32] $flags) {
        if ($this.ThrowOnApply -eq 'Before') { throw 'mock interruption before SetProcessWorkingSetSizeEx' }
        $this.Limits = [pscustomobject]@{ Minimum=$minimum; Maximum=$maximum; Flags=$flags }
        $this.ApplyCount += 1
        if ($this.ThrowOnApply -eq 'After') { throw 'mock interruption after SetProcessWorkingSetSizeEx' }
    }
    [void] Dispose() { $this.DisposeCount += 1 }
}
$script:GuardProcessFakes = @{}
$script:GuardOpenFailures = @{}
$script:GuardOpenCalls = @{}
$script:GuardAvailableBytes = [UInt64](1GB)
function Get-BlenderRamProcessIds { return @($script:GuardProcessFakes.Keys) }
function Open-BlenderRamProcess {
    param([int] $ProcessId)
    if (-not $script:GuardOpenCalls.ContainsKey($ProcessId)) { $script:GuardOpenCalls[$ProcessId] = 0 }
    $script:GuardOpenCalls[$ProcessId] += 1
    if ($script:GuardOpenFailures.ContainsKey($ProcessId)) { throw 'mock access denied' }
    return $script:GuardProcessFakes[$ProcessId]
}
function Get-BlenderRamAvailableBytes { return $script:GuardAvailableBytes }
function Assert-Guard {
    param([bool] $Condition, [string] $Message)
    if (-not $Condition) { throw $Message }
}
function Write-GuardResult {
    param([object] $Result)
    Write-Output ('GUARD_RESULT:' + (ConvertTo-Json -InputObject $Result -Compress -Depth 5))
}
"""  # noqa: E501 -- embedded PowerShell follows its own formatting conventions


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_covers_existing_new_and_multiple_processes() -> None:
    result = run_ram_guard_powershell(r"""
$first = [TestBlenderRamProcess]::new(101, 2GB)
$second = [TestBlenderRamProcess]::new(102, 1GB)
$script:GuardProcessFakes = @{101=$first; 102=$second}
$state = New-BlenderRamState
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    Assert-Guard ($first.ApplyCount -eq 1 -and $second.ApplyCount -eq 1) 'all existing Blender processes must receive caps'
    Assert-Guard (($first.Limits.Flags -band 4) -ne 0) 'maximum must be enforced'
    Assert-Guard ($first.Limits.Maximum -lt 2GB -and $second.Limits.Maximum -lt 1GB) 'low memory must reduce both resident allowances'
    $firstCount = $first.ApplyCount
    $secondCount = $second.ApplyCount
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 1 -SettleSeconds 5
    Assert-Guard ($first.ApplyCount -eq $firstCount -and $second.ApplyCount -eq $secondCount) 'settling must avoid repeated changes'
    $third = [TestBlenderRamProcess]::new(103, 1GB)
    $script:GuardProcessFakes[103] = $third
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5
    Assert-Guard ($third.ApplyCount -eq 1) 'new Blender process must be discovered'
} finally { Close-BlenderRamState -State $state }
Assert-Guard ($first.Limits.Maximum -eq 16GB -and ($first.Limits.Flags -band 5) -eq 0) 'cleanup must restore original sizes and enforcement flags'
Assert-Guard ($first.DisposeCount -eq 1 -and $second.DisposeCount -eq 1 -and $third.DisposeCount -eq 1) 'cleanup must release process handles'
Write-GuardResult @{covered=3; restored=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"covered": 3, "restored": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_hysteresis_and_available_headroom() -> None:
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$state = New-BlenderRamState
try {
    $script:GuardAvailableBytes = [UInt64](2GB)
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    Assert-Guard ($process.Limits.Maximum -ge 2GB) 'ample memory must allow the existing resident set'
    $script:GuardAvailableBytes = [UInt64](1GB)
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5
    $lowMaximum = $process.Limits.Maximum
    Assert-Guard ($lowMaximum -lt 2GB) 'low memory must reduce the resident allowance after settling'
    $process.ResidentBytes = $lowMaximum
    $afterShrink = $process.ApplyCount
    $script:GuardAvailableBytes = [UInt64](1600MB)
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 12 -SettleSeconds 5
    Assert-Guard ($process.ApplyCount -eq $afterShrink) 'hysteresis must avoid oscillating near target'
    $script:GuardAvailableBytes = [UInt64](2GB)
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 18 -SettleSeconds 5
    Assert-Guard ($process.Limits.Maximum -gt $lowMaximum) 'extra available RAM must permit cap growth'
    Assert-Guard (($process.Limits.Maximum - $lowMaximum) -le (512MB)) 'growth must not consume reserved headroom'
} finally { Close-BlenderRamState -State $state }
Write-GuardResult @{hysteresis=$true; growth=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"hysteresis": True, "growth": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_respects_existing_caps_and_bounded_shrink() -> None:
    result = run_ram_guard_powershell(r"""
$first = [TestBlenderRamProcess]::new(101, 1536MB)
$second = [TestBlenderRamProcess]::new(102, 2GB)
$first.Limits = [pscustomobject]@{Minimum=[UInt64](1GB); Maximum=[UInt64](1536MB); Flags=[UInt32]5}
$script:GuardProcessFakes = @{101=$first; 102=$second}
$script:GuardAvailableBytes = [UInt64]0
$state = New-BlenderRamState
try {
    Update-BlenderRamLimits -State $state -TargetBytes (3GB) -NowSeconds 0 -SettleSeconds 5
    Assert-Guard ($first.Limits.Minimum -eq 1GB -and $first.Limits.Maximum -ge 1GB -and $first.Limits.Maximum -le 1536MB) 'original hard minimum and maximum must be respected'
    Assert-Guard ($second.Limits.Maximum -ge 64MB) 'resident cap must retain the safety floor'
    $baseline = [UInt64](1536MB + 2GB)
    $shrink = $baseline - ($first.Limits.Maximum + $second.Limits.Maximum)
    Assert-Guard ($shrink -le 1GB) 'one pass must not shrink more than 1 GiB'
} finally { Close-BlenderRamState -State $state }
Assert-Guard ($first.Limits.Minimum -eq 1GB -and $first.Limits.Maximum -eq 1536MB -and $first.Limits.Flags -eq 5) 'original hard-limit flags must survive cleanup'
Write-GuardResult @{bounded=$true; originalLimits=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"bounded": True, "originalLimits": True}


@RAM_GUARD_WINDOWS_ONLY
@pytest.mark.parametrize("interruption", ["Before", "After"])
def test_ram_guard_restores_across_native_effect_interruption(interruption: str) -> None:
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$state = New-BlenderRamState
""" + f"$process.ThrowOnApply = '{interruption}'\n" + r"""
$warnings = @()
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5 -WarningVariable +warnings
    Assert-Guard ($warnings.Count -gt 0) 'failed native effect must be visible'
    $effectCount = $process.ApplyCount
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5
    Assert-Guard ($process.ApplyCount -eq $effectCount) 'failed process must not be retried each poll'
} finally {
    $process.ThrowOnApply = ''
    Close-BlenderRamState -State $state
}
Assert-Guard ($process.Limits.Maximum -eq 16GB -and ($process.Limits.Flags -band 5) -eq 0) 'cleanup must restore any completed native effect'
Assert-Guard ($process.DisposeCount -eq 1) 'failed process handle must be closed'
Write-GuardResult @{restored=$true; visible=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"restored": True, "visible": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_access_failure_is_visible_and_does_not_block_other_processes() -> None:
    result = run_ram_guard_powershell(r"""
$first = [TestBlenderRamProcess]::new(101, 2GB)
$second = [TestBlenderRamProcess]::new(102, 2GB)
$script:GuardProcessFakes = @{101=$first; 102=$second}
$script:GuardOpenFailures = @{101=$true}
$state = New-BlenderRamState
$warnings = @()
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5 -WarningVariable +warnings
    Assert-Guard ($warnings.Count -gt 0) 'access denied must be reported'
    Assert-Guard ($second.ApplyCount -eq 1) 'one inaccessible process must not stop the others'
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5
    Assert-Guard ($script:GuardOpenCalls[101] -eq 1) 'access failure must not repeat on every poll'
} finally { Close-BlenderRamState -State $state }
Write-GuardResult @{isolated=$true; visible=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"isolated": True, "visible": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_restores_last_successful_cap_when_next_effect_fails() -> None:
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$state = New-BlenderRamState
$warnings = @()
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    $successfulMaximum = $process.Limits.Maximum
    $process.ThrowOnApply = 'Before'
    $script:GuardAvailableBytes = [UInt64]0
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5 -WarningVariable +warnings
    Assert-Guard ($warnings.Count -gt 0) 'second setter failure must be visible'
    Assert-Guard ($process.Limits.Maximum -eq $successfulMaximum) 'before-effect failure must leave the previous successful cap intact'
} finally {
    $process.ThrowOnApply = ''
    Close-BlenderRamState -State $state
}
Assert-Guard ($process.Limits.Maximum -eq 16GB -and ($process.Limits.Flags -band 5) -eq 0) 'cleanup must still own and restore the last successful cap'
Write-GuardResult @{previousSuccessRestored=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"previousSuccessRestored": True}


@RAM_GUARD_WINDOWS_ONLY
@pytest.mark.parametrize("interruption", ["Before", "After"])
def test_ram_guard_repeated_cleanup_converges_after_restore_interruption(
    interruption: str,
) -> None:
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$state = New-BlenderRamState
$mode = 'RESTORE_INTERRUPTION'
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    $process.ThrowOnApply = $mode
    $fault = ''
    try { Close-BlenderRamState -State $state }
    catch { $fault = $_.Exception.Message }
    Assert-Guard ($fault -like "*mock interruption $mode *") 'cleanup must report the original native failure cause'
    Assert-Guard ($state.Processes.Count -eq 1 -and $process.DisposeCount -eq 0) 'failed cleanup must retain ownership of the original limits'
    $effectsBeforeRetry = $process.ApplyCount
    $process.ThrowOnApply = ''
    Close-BlenderRamState -State $state
    $expectedEffects = $effectsBeforeRetry + $(if ($mode -eq 'Before') { 1 } else { 0 })
    Assert-Guard ($process.ApplyCount -eq $expectedEffects) 'fresh cleanup must perform only an unmet restore effect'
    Assert-Guard ($process.Limits.Maximum -eq 16GB -and ($process.Limits.Flags -band 5) -eq 0) 'fresh cleanup must converge on original limits'
    Assert-Guard ($state.Processes.Count -eq 0 -and $process.DisposeCount -eq 1) 'successful cleanup must release owned state and handles'
    Close-BlenderRamState -State $state
    Assert-Guard ($process.ApplyCount -eq $expectedEffects -and $process.DisposeCount -eq 1) 'repeated empty cleanup must have no effect'
} finally {
    $process.ThrowOnApply = ''
    Close-BlenderRamState -State $state
}
Write-GuardResult @{restoreConverges=$true; faultPreserved=$true; emptyCloseHasNoEffect=$true}
""".replace("RESTORE_INTERRUPTION", interruption))  # noqa: E501 -- embedded PowerShell formatting
    assert result == {
        "restoreConverges": True,
        "faultPreserved": True,
        "emptyCloseHasNoEffect": True,
    }


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_plans_page_aligned_limits_for_uneven_resident_sizes() -> None:
    result = run_ram_guard_powershell(r"""
$first = [TestBlenderRamProcess]::new(101, (2GB + 12345))
$second = [TestBlenderRamProcess]::new(102, (1GB + 34567))
$script:GuardProcessFakes = @{101=$first; 102=$second}
$state = New-BlenderRamState
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB + 777) -NowSeconds 0 -SettleSeconds 5
    $pageSize = [Environment]::SystemPageSize
    Assert-Guard (($first.Limits.Maximum % $pageSize) -eq 0 -and ($second.Limits.Maximum % $pageSize) -eq 0) 'Windows rounds limits to pages; plans must use that same granularity'
    $count = $first.ApplyCount + $second.ApplyCount
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB + 777) -NowSeconds 6 -SettleSeconds 5
    Assert-Guard (($first.ApplyCount + $second.ApplyCount) -eq $count) 'page-rounded API readback must not trigger another setter'
} finally { Close-BlenderRamState -State $state }
Write-GuardResult @{pageAligned=$true; repeatedEffectSkipped=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"pageAligned": True, "repeatedEffectSkipped": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_process_exit_and_pid_reuse_get_fresh_original_limits() -> None:
    result = run_ram_guard_powershell(r"""
$old = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$old}
$state = New-BlenderRamState
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    $old.Exited = $true
    $replacement = [TestBlenderRamProcess]::new(101, 2GB)
    $replacement.Limits = [pscustomobject]@{Minimum=[UInt64](2MB); Maximum=[UInt64](12GB); Flags=[UInt32]2}
    $script:GuardProcessFakes[101] = $replacement
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 6 -SettleSeconds 5
    Assert-Guard ($old.DisposeCount -eq 1) 'exited handle must be released'
    Assert-Guard ($replacement.ApplyCount -eq 1) 'PID reuse must open the new process'
} finally { Close-BlenderRamState -State $state }
Assert-Guard ($replacement.Limits.Minimum -eq 2MB -and $replacement.Limits.Maximum -eq 12GB) 'PID reuse must never restore the old process limits onto its replacement'
Write-GuardResult @{freshOriginal=$true; oldDisposed=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"freshOriginal": True, "oldDisposed": True}


@RAM_GUARD_WINDOWS_ONLY
@pytest.mark.parametrize("outside_change_before_update", [False, True])
def test_ram_guard_preserves_outside_limit_changes(outside_change_before_update: bool) -> None:
    next_update = (
        "Update-BlenderRamLimits -State $state -TargetBytes (1536MB) "
        "-NowSeconds 6 -SettleSeconds 5\n"
        if outside_change_before_update
        else ""
    )
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$state = New-BlenderRamState
try {
    Update-BlenderRamLimits -State $state -TargetBytes (1536MB) -NowSeconds 0 -SettleSeconds 5
    $effectCount = $process.ApplyCount
    $process.Limits = [pscustomobject]@{Minimum=[UInt64](8MB); Maximum=[UInt64](768MB); Flags=[UInt32]6}
""" + next_update + r"""
} finally { Close-BlenderRamState -State $state }
Assert-Guard ($process.ApplyCount -eq $effectCount) 'outside writer must not be overwritten by update or cleanup'
Assert-Guard ($process.Limits.Minimum -eq 8MB -and $process.Limits.Maximum -eq 768MB -and $process.Limits.Flags -eq 6) 'outside limits must remain intact'
Write-GuardResult @{outsidePreserved=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"outsidePreserved": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_continuous_monitor_cleans_up_when_stopped() -> None:
    result = run_ram_guard_powershell(r"""
$process = [TestBlenderRamProcess]::new(101, 2GB)
$script:GuardProcessFakes = @{101=$process}
$script:GuardSleeps = 0
function Start-Sleep {
    param([int] $Seconds, [int] $Milliseconds)
    $script:GuardSleeps += 1
    if ($script:GuardSleeps -ge 2) { throw 'stop-monitor-test' }
}
$stopped = $false
try { Start-BlenderRamMonitor -AvailableGiB 1.5 -IntervalSeconds 1 -SettleSeconds 5 }
catch { if ($_.Exception.Message -notlike '*stop-monitor-test*') { throw }; $stopped = $true }
Assert-Guard ($stopped -and $script:GuardSleeps -eq 2) 'monitor must run repeatedly until stopped'
Assert-Guard ($process.ApplyCount -ge 2 -and $process.Limits.Maximum -eq 16GB -and ($process.Limits.Flags -band 5) -eq 0) 'stop must restore original limits'
Assert-Guard ($process.DisposeCount -eq 1) 'stop must release handles'
Write-GuardResult @{continuous=$true; stoppedCleanly=$true}
""")  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    assert result == {"continuous": True, "stoppedCleanly": True}


@RAM_GUARD_WINDOWS_ONLY
def test_ram_guard_native_adapter_caps_disposable_process_and_restores_flags(
    tmp_path: Path,
) -> None:
    # The exact blender.exe basename is required by the adapter's target check.
    # This disposable worker touches its own pages; it is never the user's Blender.
    worker_path = str(tmp_path / "blender.exe").replace("'", "''")
    body = r"""
Initialize-BlenderRamNative
$source = @'
using System;
using System.Threading;
public static class BlenderMemoryWorker {
    public static void Main() {
        byte[] pages = new byte[160 * 1024 * 1024];
        for (int index = 0; index < pages.Length; index += 4096) pages[index] = 1;
        Console.WriteLine("READY");
        while (true) {
            for (int index = 0; index < pages.Length; index += 4096) pages[index]++;
            Thread.Sleep(10);
        }
    }
}
'@
""" + f"$workerPath = '{worker_path}'\n" + r"""
Add-Type -TypeDefinition $source -OutputAssembly $workerPath -OutputType ConsoleApplication
$info = [Diagnostics.ProcessStartInfo]::new($workerPath)
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$worker = [Diagnostics.Process]::new()
$worker.StartInfo = $info
$native = $null
$original = $null
$restoreFlags = [UInt32]0
try {
    [void]$worker.Start()
    if ($worker.StandardOutput.ReadLine() -ne 'READY') { throw 'disposable worker did not become ready' }
    $native = [Ceratops.BlenderRam.NativeProcess]::new($worker.Id)
    $original = $native.ReadLimits()
    $restoreFlags = [UInt32]($(if ($original.Flags -band 1) { 1 } else { 2 }) + $(if ($original.Flags -band 4) { 4 } else { 8 }))
    $native.ApplyLimits([UInt64](1MB), [UInt64](64MB), [UInt32]6)
    $capped = $native.ReadLimits()
    if ($capped.Maximum -ne 64MB -or ($capped.Flags -band 4) -eq 0) { throw 'native API did not enforce requested resident maximum' }
    Start-Sleep -Milliseconds 200
    $resident = $native.WorkingSetBytes()
    if ($resident -gt (65MB)) { throw "disposable process resident memory exceeded cap: $resident" }
    $native.ApplyLimits($original.Minimum, $original.Maximum, $restoreFlags)
    $restored = $native.ReadLimits()
    if ($restored.Minimum -ne $original.Minimum -or $restored.Maximum -ne $original.Maximum -or $restored.Flags -ne $original.Flags) { throw 'native API did not restore original sizes and flags' }
    Write-Output ('GUARD_RESULT:' + (ConvertTo-Json -Compress @{enforced=$true; restored=$true}))
} finally {
    if ($null -ne $native) {
        if ($null -ne $original -and -not $native.HasExited()) { $native.ApplyLimits($original.Minimum, $original.Maximum, $restoreFlags) }
        $native.Dispose()
    }
    if (-not $worker.HasExited) { $worker.Kill(); $worker.WaitForExit() }
    $worker.Dispose()
}
"""  # noqa: E501 -- embedded PowerShell follows its own formatting conventions
    result = run_ram_guard_powershell(body, fake_adapter=False)
    assert result == {"enforced": True, "restored": True}
