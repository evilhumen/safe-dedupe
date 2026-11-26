# Windows Battlefield Notes — production-tested gotchas

Lessons from a real cleanup of ~110k duplicate files / ~450 GB on a Windows
machine with an overzealous antivirus and a full system drive. Read before any
Windows deletion.

## 1. Recycle Bin quota degradation (most important)

`[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile(path, ..., SendToRecycleBin, ...)`
**reports success but permanently deletes** when the volume's Recycle Bin has no
quota configured or the payload exceeds the quota. No exception, no dialog —
the path just disappears, so a naive `Test-Path` check passes and the operator
wrongly believes the files are recoverable.

Detection:
- Check `HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\*`
  for a `MaxCapacity` value matching the volume. Absence = effectively unconfigured.
- Run `scripts/verify_recycle_bin.ps1` before and after deletion.
- Compare free space: a large increase consistent with deleted bytes means
  permanent deletion (quarantine/recycle moves keep space occupied).

Mitigation: use same-volume quarantine (the default in `delete_duplicates.ps1`).
Renames on one volume are instantaneous, consume no extra space, fully recoverable.

## 2. Antivirus / sandbox blocking `Move-Item` -> use .NET File.Move

In a large run, PowerShell `Move-Item` failed ~71k times with "对路径的访问被拒绝"
(Access Denied) while files were not locked, not read-only, and the directory was
writable. Root cause: the system drive was full (0 GB) and real-time AV scanning
intercepted the cmdlet. What worked, in order:

1. `[System.IO.File]::Move($src, $dest)` — succeeded for ~71k files.
2. Two-step rename for the few stubborn ones:
   `[IO.File]::Move($src, "$src.dedup_tmp")` then `Move($tmp, $dest)`.
3. `robocopy EMPTY_DIR TARGET /MIR /MT:32` — fastest for purging a whole tree of
   100k+ files (1065 files in 62s vs ~0.45s/file for individual deletes). Redirect
   its output to a file; exit code 2 means "extra files deleted" (normal).

`delete_duplicates.ps1` bakes in methods 1 and 2.

## 3. fclones CSV with commas / quotes in paths

fclones CSV is `size,hash,path[,path...]`; paths with commas are quoted. A naive
`-split ','` truncates them into invalid paths (6797 failures, 0 deletions in
one run). Always use a real CSV parser (Python `csv` module). `fclones_csv_to_report.py`
does this correctly. Also normalize doubled backslashes (`\\` -> `\`) before use.

## 4. Wildcard characters in filenames

VB `FileSystem.DeleteFile` treats `[`, `]`, `*`, `?` as wildcards: `foo[2010].pdf`
fails with "路径中具有非法字符". Use `-LiteralPath` for every `Test-Path`,
`Get-Item`, `Get-ChildItem`, `Move-Item`. The deletion script sends any path
matching `[\[\]\*\?]` through the literal-path quarantine branch.

## 5. System drive filled by the agent's own snapshot backups

A 100k-file dedupe run generated 108 GB of "modify backup" snapshots under
`C:\Users\<user>\.workbuddy\workspace\sessions\<session>\modify_backup_v2\commits`
and filled C: to 0 bytes. Consequences: AV went haywire (Access Denied), Bash
tools failed with `no space left on device`, editing/写入 failed.

Before a large task: check free space on the system drive. Keep reports and
quarantine on the TARGET volume, never on C:. After a large task, purge stale
snapshot commits if present. Note that app-internal session paths may be
sandbox-protected against writes; do not try to bypass the safety policy.

## 6. Sandbox-protected paths

Paths under `C:\Users\<user>\.workbuddy\workspace\sessions\` may reject writes
(config edits, renames) with "Permission request rejected". A bulk-delete guard
(threshold 50 files/turn) can also block large operations. When a write is
rejected, do not retry the same command or equivalent variants — report it and
ask the user. Deleting via an elevated path requires explicit user approval.

## 7. Hard links, quarantine, and real space reclamation

- A hard-link backup directory mirrors file paths but consumes no extra space.
- Moving duplicates to a same-volume quarantine keeps space occupied until the
  quarantine is deleted. Tell the user this: "space is freed only after you
  delete the quarantine".
- Verify before claiming reclamation: compare drive free space before/after
  the final quarantine purge.

## 8. Verification checklist

1. Re-run `fclones group <target>` — expect 0 duplicate groups.
2. Confirm every `keep` path still exists.
3. Confirm every quarantined path is gone from the source tree.
4. If Recycle Bin mode used, re-run `verify_recycle_bin.ps1 -ExpectFiles <n>`.
5. Report counts honestly; never claim recoverability that was not verified.
