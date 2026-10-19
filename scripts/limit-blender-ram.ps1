<#
.SYNOPSIS
Keep a target amount of Windows RAM available by paging Blender more aggressively.
.DESCRIPTION
Run in a Windows PowerShell console and stop with Ctrl+C. All existing and new
blender.exe processes accessible to this user are monitored. Only pageable
working-set limits change; allocation limits, pagefile settings and GPU memory
are untouched. Available RAM is feedback, not a guaranteed system reservation.
Original limits live only in memory and are restored on graceful shutdown if no
other writer has changed them. Force-killing this monitor bypasses restoration;
surviving Blender processes retain their last limits until changed or exited.
No runtime files, logs, scheduled tasks or startup settings are created.
.EXAMPLE
powershell.exe -NoProfile -File .\scripts\limit-blender-ram.ps1
#>
[CmdletBinding()]
param(
    [ValidateRange(0.25, 1024)] [double] $AvailableGiB = 1.5,
    [ValidateRange(0.2, 60)] [double] $IntervalSeconds = 1,
    [ValidateRange(1, 300)] [double] $SettleSeconds = 5
)

function Initialize-BlenderRamNative {
    if ($env:OS -ne 'Windows_NT') { throw 'This monitor requires Windows.' }
    if ('Ceratops.BlenderRam.NativeProcess' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace Ceratops.BlenderRam {
    public sealed class Limits {
        public ulong Minimum;
        public ulong Maximum;
        public uint Flags;
        public Limits(ulong minimum, ulong maximum, uint flags) {
            Minimum = minimum; Maximum = maximum; Flags = flags;
        }
    }
    internal static class Api {
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern IntPtr OpenProcess(uint access, bool inherit, int id);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        internal static extern bool QueryFullProcessImageName(
            IntPtr handle, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool GetProcessWorkingSetSizeEx(
            IntPtr handle, out UIntPtr minimum, out UIntPtr maximum, out uint flags);
        [DllImport("kernel32.dll", SetLastError = true)]
        internal static extern bool SetProcessWorkingSetSizeEx(
            IntPtr handle, UIntPtr minimum, UIntPtr maximum, uint flags);
        [DllImport("psapi.dll", SetLastError = true)]
        internal static extern bool GetProcessMemoryInfo(
            IntPtr handle, out Counters counters, uint size);
        [StructLayout(LayoutKind.Sequential)]
        internal struct Counters {
            public uint Size, PageFaultCount;
            public UIntPtr PeakWorkingSet, WorkingSet, PeakPagedPool, PagedPool;
            public UIntPtr PeakNonPagedPool, NonPagedPool, Pagefile, PeakPagefile;
        }
        internal static Exception Error(string operation) {
            int code = Marshal.GetLastWin32Error();
            return new Win32Exception(code, operation + ": " + new Win32Exception(code).Message);
        }
    }
    public sealed class NativeProcess : IDisposable {
        private IntPtr handle;
        public NativeProcess(int processId) {
            // Stable handles prevent a recycled PID from redirecting an effect.
            // SET_QUOTA + QUERY_LIMITED_INFORMATION + SYNCHRONIZE, no VM write rights.
            handle = Api.OpenProcess(0x101100, false, processId);
            if (handle == IntPtr.Zero) throw Api.Error("OpenProcess");
            try {
                StringBuilder image = new StringBuilder(32768);
                uint size = (uint)image.Capacity;
                if (!Api.QueryFullProcessImageName(handle, 0, image, ref size))
                    throw Api.Error("QueryFullProcessImageName");
                if (!String.Equals(Path.GetFileName(image.ToString()), "blender.exe",
                                   StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("The process is no longer blender.exe.");
            } catch { Dispose(); throw; }
        }
        public bool HasExited() {
            uint result = Api.WaitForSingleObject(handle, 0);
            if (result == 0) return true;
            if (result == 258) return false;
            throw Api.Error("WaitForSingleObject");
        }
        public Limits ReadLimits() {
            UIntPtr minimum, maximum; uint flags;
            if (!Api.GetProcessWorkingSetSizeEx(handle, out minimum, out maximum, out flags))
                throw Api.Error("GetProcessWorkingSetSizeEx");
            return new Limits(minimum.ToUInt64(), maximum.ToUInt64(), flags);
        }
        public ulong WorkingSetBytes() {
            Api.Counters counters;
            if (!Api.GetProcessMemoryInfo(handle, out counters,
                                         (uint)Marshal.SizeOf(typeof(Api.Counters))))
                throw Api.Error("GetProcessMemoryInfo");
            return counters.WorkingSet.ToUInt64();
        }
        public void ApplyLimits(ulong minimum, ulong maximum, uint flags) {
            if (!Api.SetProcessWorkingSetSizeEx(handle, new UIntPtr(minimum),
                                               new UIntPtr(maximum), flags))
                throw Api.Error("SetProcessWorkingSetSizeEx");
        }
        public void Dispose() {
            if (handle != IntPtr.Zero) { Api.CloseHandle(handle); handle = IntPtr.Zero; }
        }
    }
    public static class SystemMemory {
        [StructLayout(LayoutKind.Sequential)]
        private struct Status {
            public uint Length, Load;
            public ulong TotalPhysical, AvailablePhysical, TotalPagefile, AvailablePagefile;
            public ulong TotalVirtual, AvailableVirtual, ExtendedVirtual;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GlobalMemoryStatusEx(ref Status status);
        public static ulong AvailableBytes() {
            Status status = new Status(); status.Length = (uint)Marshal.SizeOf(typeof(Status));
            if (!GlobalMemoryStatusEx(ref status)) throw Api.Error("GlobalMemoryStatusEx");
            return status.AvailablePhysical;
        }
    }
}
'@
}

function Get-BlenderRamProcessIds {
    foreach ($process in [Diagnostics.Process]::GetProcessesByName('blender')) {
        try { $process.Id } finally { $process.Dispose() }
    }
}

function Open-BlenderRamProcess {
    param([int] $ProcessId)
    [Ceratops.BlenderRam.NativeProcess]::new($ProcessId)
}

function Get-BlenderRamAvailableBytes { [Ceratops.BlenderRam.SystemMemory]::AvailableBytes() }

function New-BlenderRamState {
    @{ Processes = @{}; Failures = @{}; LastChangeSeconds = [double]::NegativeInfinity }
}

function Test-BlenderRamLimitsEqual {
    param($Left, $Right)
    $null -ne $Left -and $null -ne $Right -and
        $Left.Minimum -eq $Right.Minimum -and $Left.Maximum -eq $Right.Maximum -and
        ($Left.Flags -band 0x5) -eq ($Right.Flags -band 0x5)
}

function Get-BlenderRamLimitPlan {
    param([object[]] $Samples, [UInt64] $AvailableBytes, [UInt64] $TargetBytes)
    if ($Samples.Count -eq 0) { return }
    $rows = @($Samples | Sort-Object ProcessId | ForEach-Object {
        $floor = [double](64MB)
        $ceiling = [double]::PositiveInfinity
        if ($_.Original.Flags -band 1) { $floor = [Math]::Max($floor, $_.Original.Minimum) }
        if ($_.Original.Flags -band 4) { $ceiling = [double]$_.Original.Maximum }
        [pscustomobject]@{
            Sample = $_; Floor = [Math]::Min($floor, $ceiling); Ceiling = $ceiling
            Weight = [Math]::Max(1.0, [double]$_.WorkingSet); Budget = 0.0
        }
    })
    $resident = [double](($Samples | Measure-Object WorkingSet -Sum).Sum)
    $floorTotal = [double](($rows | Measure-Object Floor -Sum).Sum)
    # Available includes reclaimable cache. Dirty/shared page eviction is not an
    # immediate one-for-one release, so limit each contraction and settle afterward.
    $budget = [Math]::Max($floorTotal, [Math]::Max($resident - 1GB,
        $resident + [double]$AvailableBytes - $TargetBytes - 128MB))
    foreach ($row in $rows) { $row.Budget = $row.Floor }
    $remaining = [Math]::Max(0.0, $budget - $floorTotal)
    $eligible = @($rows | Where-Object { $_.Budget -lt $_.Ceiling })
    while ($remaining -ge 1 -and $eligible.Count -gt 0) {
        $weights = [double](($eligible | Measure-Object Weight -Sum).Sum)
        $assigned = 0.0
        foreach ($row in $eligible) {
            $share = [Math]::Min($row.Ceiling - $row.Budget, $remaining * $row.Weight / $weights)
            $row.Budget += $share
            $assigned += $share
        }
        $remaining -= $assigned
        if ($assigned -lt 1) { break }
        $eligible = @($eligible | Where-Object { $_.Budget -lt $_.Ceiling })
    }
    foreach ($row in $rows) {
        # Windows stores page-sized limits; predict its representation so an
        # ordinary readback is never mistaken for another writer's change.
        $pageSize = [Environment]::SystemPageSize
        $maximum = [UInt64]([Math]::Floor($row.Budget / $pageSize) * $pageSize)
        [pscustomobject]@{
            ProcessId = $row.Sample.ProcessId
            Minimum = [UInt64][Math]::Min($row.Sample.Original.Minimum, $maximum)
            Maximum = $maximum
            Flags = [uint32](4 + $(if ($row.Sample.Original.Flags -band 1) { 1 } else { 2 }))
        }
    }
}

function Update-BlenderRamLimits {
    [CmdletBinding()]
    param($State, [UInt64] $TargetBytes, [double] $NowSeconds, [double] $SettleSeconds = 5)
    $ids = @(Get-BlenderRamProcessIds)
    $changedProcesses = $false
    foreach ($blenderProcessId in @($State.Processes.Keys)) {
        $entry = $State.Processes[$blenderProcessId]
        if ($entry.Native.HasExited()) {
            $entry.Native.Dispose()
            $State.Processes.Remove($blenderProcessId)
            $State.Failures.Remove($blenderProcessId)
            $changedProcesses = $true
        }
    }
    foreach ($blenderProcessId in @($State.Failures.Keys)) {
        if ($blenderProcessId -notin $ids) { $State.Failures.Remove($blenderProcessId) }
    }
    foreach ($blenderProcessId in $ids) {
        if ($State.Processes.ContainsKey($blenderProcessId) -or
            $State.Failures.ContainsKey($blenderProcessId)) { continue }
        $native = $null
        try {
            $native = Open-BlenderRamProcess -ProcessId $blenderProcessId
            $original = $native.ReadLimits()
            $State.Processes[$blenderProcessId] = @{
                Native = $native; Original = $original; Applied = $null; Pending = $null
            }
            $changedProcesses = $true
        } catch {
            if ($null -ne $native) { $native.Dispose() }
            $State.Failures[$blenderProcessId] = $_.Exception.Message
            Write-Warning "Blender PID $blenderProcessId is uncontrolled: $($_.Exception.Message)"
        }
    }
    if ($State.Processes.Count -eq 0) { return }
    $available = Get-BlenderRamAvailableBytes
    $samples = @(
        foreach ($blenderProcessId in @($State.Processes.Keys)) {
            if ($State.Failures.ContainsKey($blenderProcessId)) { continue }
            $entry = $State.Processes[$blenderProcessId]
            try {
                $current = $entry.Native.ReadLimits()
                if ($null -ne $entry.Applied -and
                    -not (Test-BlenderRamLimitsEqual $current $entry.Applied)) {
                    throw 'Working-set settings changed outside this monitor; leaving them alone.'
                }
                [pscustomobject]@{
                    ProcessId = $blenderProcessId; WorkingSet = $entry.Native.WorkingSetBytes()
                    Original = $entry.Original
                }
            } catch {
                $State.Failures[$blenderProcessId] = $_.Exception.Message
                Write-Warning "Blender PID $blenderProcessId is uncontrolled: $($_.Exception.Message)"
            }
        }
    )
    if (-not $changedProcesses -and ($NowSeconds - $State.LastChangeSeconds -lt $SettleSeconds -or
        ($available -ge $TargetBytes -and $available -le $TargetBytes + 256MB))) { return }
    foreach ($plan in @(Get-BlenderRamLimitPlan $samples $available $TargetBytes)) {
        $entry = $State.Processes[$plan.ProcessId]
        try {
            if ($entry.Native.HasExited()) { continue }
            $current = $entry.Native.ReadLimits()
            $expected = if ($null -ne $entry.Applied) { $entry.Applied } else { $entry.Original }
            if (-not (Test-BlenderRamLimitsEqual $current $expected)) {
                throw 'Working-set settings changed before the update; leaving them alone.'
            }
            if (Test-BlenderRamLimitsEqual $current $plan) { continue }
            # Record the intended effect before calling Windows. Cleanup can then
            # inspect and undo an effect even if interruption follows its success.
            $entry.Pending = $plan
            $entry.Native.ApplyLimits($plan.Minimum, $plan.Maximum, $plan.Flags)
            $entry.Applied = $plan
            $entry.Pending = $null
            $State.LastChangeSeconds = $NowSeconds
        } catch {
            $State.Failures[$plan.ProcessId] = $_.Exception.Message
            Write-Warning "Blender PID $($plan.ProcessId) is uncontrolled: $($_.Exception.Message)"
        }
    }
}

function Close-BlenderRamState {
    [CmdletBinding()]
    param($State)
    $failures = [Collections.Generic.List[string]]::new()
    foreach ($blenderProcessId in @($State.Processes.Keys)) {
        $entry = $State.Processes[$blenderProcessId]
        $closed = $false
        try {
            if (($null -ne $entry.Applied -or $null -ne $entry.Pending) -and
                -not $entry.Native.HasExited()) {
                $current = $entry.Native.ReadLimits()
                if ((Test-BlenderRamLimitsEqual $current $entry.Applied) -or
                    (Test-BlenderRamLimitsEqual $current $entry.Pending)) {
                    $original = $entry.Original
                    $flags = $(if ($original.Flags -band 1) { 1 } else { 2 }) +
                        $(if ($original.Flags -band 4) { 4 } else { 8 })
                    $entry.Native.ApplyLimits($original.Minimum, $original.Maximum, $flags)
                } elseif (-not (Test-BlenderRamLimitsEqual $current $entry.Original)) {
                    Write-Warning "Blender PID ${blenderProcessId}: preserving outside changes."
                }
            }
            $closed = $true
        } catch {
            $failures.Add("PID ${blenderProcessId}: $($_.Exception.Message)")
        } finally {
            # A caller can retry cleanup with the same desired originals after
            # correcting a reported failure; do not discard its only owner.
            if ($closed) {
                $entry.Native.Dispose()
                $State.Processes.Remove($blenderProcessId)
                $State.Failures.Remove($blenderProcessId)
            }
        }
    }
    if ($State.Processes.Count -eq 0) { $State.Failures.Clear() }
    if ($failures.Count -gt 0) { throw "Could not restore Blender limits: $($failures -join '; ')" }
}

function Start-BlenderRamMonitor {
    [CmdletBinding()]
    param([double] $AvailableGiB = 1.5, [double] $IntervalSeconds = 1, [double] $SettleSeconds = 5)
    $ErrorActionPreference = 'Stop'
    Initialize-BlenderRamNative
    # Discovery includes every accessible session, so exclusion must also cover
    # the whole machine. A named mutex creates no persistent disk state.
    $mutex = [Threading.Mutex]::new($false, 'Global\CeratopsBlenderRamMonitor')
    $owned = $false
    $state = New-BlenderRamState
    $primaryError = $null
    try {
        try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { throw 'A Blender RAM monitor is already running on this computer.' }
        $clock = [Diagnostics.Stopwatch]::StartNew()
        Write-Host "Monitoring blender.exe; target $AvailableGiB GiB available RAM. Stop with Ctrl+C."
        while ($true) {
            Update-BlenderRamLimits -State $state -TargetBytes ([UInt64]($AvailableGiB * 1GB)) `
                -NowSeconds $clock.Elapsed.TotalSeconds -SettleSeconds $SettleSeconds
            Start-Sleep -Milliseconds ([int]($IntervalSeconds * 1000))
        }
    } catch { $primaryError = $_; throw } finally {
        try { Close-BlenderRamState $state } catch {
            if ($null -ne $primaryError) { Write-Warning $_.Exception.Message } else { throw }
        } finally {
            if ($owned) { $mutex.ReleaseMutex() }
            $mutex.Dispose()
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Start-BlenderRamMonitor -AvailableGiB $AvailableGiB -IntervalSeconds $IntervalSeconds `
            -SettleSeconds $SettleSeconds
    } catch { Write-Error $_; exit 1 }
}
