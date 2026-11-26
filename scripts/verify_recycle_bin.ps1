# Pre-flight / post-flight check for the Windows Recycle Bin on a given volume.
#
# CRITICAL: FileSystem.DeleteFile(..., SendToRecycleBin) silently degrades to a
# PERMANENT delete when the volume's Recycle Bin has no quota configured or the
# files exceed the quota. It reports success. Always run this check BEFORE
# deleting and AFTER deleting; if post-check finds no files, tell the user the
# deletion was permanent, not recoverable from the Recycle Bin.
#
# Usage (PowerShell 5.1):
#   powershell -NoProfile -ExecutionPolicy Bypass -File verify_recycle_bin.ps1 -Drive 'G:'
#   powershell -NoProfile -ExecutionPolicy Bypass -File verify_recycle_bin.ps1 -Drive 'G:' -ExpectFiles 100

param(
    [Parameter(Mandatory=$true)][string]$Drive,
    [int]$ExpectFiles = -1
)

$drive = $Drive.TrimEnd('\', ':') + ':'
$rb = Join-Path $drive '$RECYCLE.BIN'

Write-Output "RECYCLE_BIN_PATH $rb"
if (-not (Test-Path -LiteralPath $rb)) {
    Write-Output 'RECYCLE_BIN_MISSING'
    exit 2
}

# Volume quota configuration (HKLM BitBucket\Volume\{GUID}). Absence means the
# Recycle Bin is effectively unconfigured -> SendToRecycleBin will permanently
# delete rather than move to the bin.
$volKeys = Get-ChildItem -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume' -ErrorAction SilentlyContinue
$quotaFound = $false
foreach ($k in $volKeys) {
    $p = Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction SilentlyContinue
    if ($null -ne $p -and $null -ne $p.MaxCapacity) {
        Write-Output ("QUOTA_KEY {0} MaxCapacity={1}" -f $k.PSChildName, $p.MaxCapacity)
        $quotaFound = $true
    }
}
if (-not $quotaFound) {
    Write-Output 'QUOTA_NOT_CONFIGURED'
    Write-Output 'WARNING SendToRecycleBin may permanently delete files on this volume.'
}

# Count actual recycled payload files. Windows stores "$R<random>.<ext>" as the
# payload and "$I<random>.<ext>" as metadata; desktop.ini is ignored.
$payload = @()
Get-ChildItem -LiteralPath $rb -Force -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    $payload += Get-ChildItem -LiteralPath $_.FullName -Force -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like '$R*' }
}
$bytes = ($payload | Measure-Object -Property Length -Sum).Sum
Write-Output ("RECYCLED_FILES {0}" -f $payload.Count)
Write-Output ("RECYCLED_GB {0:N2}" -f ($bytes / 1GB))

if ($ExpectFiles -ge 0) {
    if ($payload.Count -lt $ExpectFiles) {
        Write-Output ("CHECK_FAIL expected_at_least={0} actual={1}" -f $ExpectFiles, $payload.Count)
        exit 3
    }
    Write-Output ("CHECK_OK expected_at_least={0} actual={1}" -f $ExpectFiles, $payload.Count)
}
exit 0
