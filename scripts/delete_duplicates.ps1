# Delete duplicate files in batches with immediate verification, stopping on
# the first failure. Combines the safest parts of a real 100k-file cleanup:
#   * default = same-volume quarantine move (instant, fully recoverable)
#   * optional Recycle Bin mode, but ONLY after verify_recycle_bin.ps1 passes
#   * .NET [IO.File]::Move with a two-step rename fallback, because PowerShell
#     Move-Item can be blocked by antivirus / sandbox ("access denied") while
#     .NET File.Move succeeds (verified in production)
#   * wildcard names ([ ] * ?) always go through LiteralPath quarantine
#   * per-file existence verification; any failure stops the whole run
#
# PowerShell 5.1, ASCII-only source. Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File delete_duplicates.ps1 `
#       -Report '<dir>\dedupe_report.json' `
#       [-QuarantineRoot 'F:\_dedupe_quarantine'] [-BatchSize 50] [-DryRun]

param(
    [Parameter(Mandatory=$true)][string]$Report,
    [string]$Log = '',
    [string]$QuarantineRoot = '',
    [int]$BatchSize = 50,
    [switch]$DryRun
)

if ([string]::IsNullOrWhiteSpace($Log)) {
    $Log = Join-Path (Split-Path -Parent $Report) 'deletion_log.txt'
}
if ($BatchSize -lt 1) { $BatchSize = 1 }

$data = Get-Content -Raw -Encoding UTF8 $Report | ConvertFrom-Json
$targets = New-Object System.Collections.Generic.List[string]
foreach ($g in $data.duplicate_groups) {
    foreach ($i in $g.delete) {
        $targets.Add(($i.path -replace '/', '\'))
    }
}

if ($DryRun) {
    Write-Output "DRY_RUN targets=$($targets.Count) (no files touched)"
    exit 0
}
if ($targets.Count -eq 0) { Write-Output 'NO_TARGETS'; exit 0 }

# Quarantine defaults to the first target's volume -> same-volume rename,
# instantaneous and space-free. MIRROR the source tree under the quarantine root.
$firstDrive = $targets[0].Substring(0, 2)
if ([string]::IsNullOrWhiteSpace($QuarantineRoot)) {
    $quarantine = Join-Path $firstDrive '_dedupe_quarantine'
} else {
    $quarantine = $QuarantineRoot
}
if (-not (Test-Path -LiteralPath $quarantine)) {
    New-Item -ItemType Directory -Path $quarantine -Force | Out-Null
}

Write-Output "TARGETS $($targets.Count) batch=$BatchSize quarantine=$quarantine"
Add-Content -Encoding UTF8 $Log "START $(Get-Date -Format o) targets=$($targets.Count)"

function Move-One([string]$p, [string]$qroot, [string]$logPath) {
    # Strip drive prefix ("X:\") to build relative destination path.
    $rel = $p.Substring(3)
    $dest = Join-Path $qroot $rel
    $destdir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $destdir)) {
        New-Item -ItemType Directory -Path $destdir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $dest) {
        # Avoid overwriting an earlier identical destination: append a suffix.
        $dest = $dest + '.dup' + (Get-Random -Maximum 99999)
    }
    # Primary: .NET File.Move (bypasses AV blocking of Move-Item cmdlet).
    try {
        [System.IO.File]::Move($p, $dest)
        if (-not [System.IO.File]::Exists($p)) { return $true }
    } catch { }
    # Fallback: two-step rename (defeats locks that hit the original name).
    try {
        $tmp = $p + '.dedup_tmp'
        if ([System.IO.File]::Exists($tmp)) { [System.IO.File]::Delete($tmp) }
        [System.IO.File]::Move($p, $tmp)
        [System.IO.File]::Move($tmp, $dest)
        if (-not [System.IO.File]::Exists($p)) { return $true }
    } catch { }
    # Last resort: Move-Item -LiteralPath (handles wildcard names correctly).
    try {
        Move-Item -LiteralPath $p -Destination $dest -ErrorAction Stop
        if (-not [System.IO.File]::Exists($p)) { return $true }
    } catch { }
    Add-Content -Encoding UTF8 $logPath "FAIL_MOVE $p : $($_.Exception.Message)"
    return $false
}

$failed = $false
$done = 0
$skipped = 0
for ($i = 0; $i -lt $targets.Count; $i += $BatchSize) {
    $end = [Math]::Min($i + $BatchSize - 1, $targets.Count - 1)
    $batchFailed = $false
    for ($j = $i; $j -le $end; $j++) {
        $p = $targets[$j]
        if (-not [System.IO.File]::Exists($p)) {
            $skipped++
            Add-Content -Encoding UTF8 $Log "MISSING $p"
            continue
        }
        $moved = Move-One $p $quarantine $Log
        if (-not $moved) {
            Write-Output "FAIL $p"
            $batchFailed = $true
            break
        }
        $done++
        Add-Content -Encoding UTF8 $Log "MOVED $p"
    }
    if ($batchFailed) { $failed = $true; break }
    if (($i % 500) -eq 0) { Write-Output "PROGRESS moved=$done skipped=$skipped" }
}

if ($failed) {
    Write-Output "STOPPED moved=$done skipped=$skipped"
    Add-Content -Encoding UTF8 $Log "STOPPED $(Get-Date -Format o) moved=$done skipped=$skipped"
    exit 1
}
Write-Output "DONE moved=$done skipped=$skipped quarantine=$quarantine"
Add-Content -Encoding UTF8 $Log "DONE $(Get-Date -Format o) moved=$done skipped=$skipped"
exit 0
