---
name: safe-dedupe
description: "Find content-identical duplicate files across large directory trees with fclones' fast native engine, then remove them with hardened, recoverable, batch-verified deletion. Use when the user asks to deduplicate, find/delete duplicate files, clean repeated files, or audit a folder for duplicates/under-replicated files — especially on large trees (100k+ files, TB scale) or Windows where antivirus, Recycle Bin quota, wildcard filenames, and sandbox rules can silently break naive deletion."
agent_created: true
---

# Safe Dedupe — fast scan, hardened deletion

## Overview

This skill merges the strengths of two approaches into one end-to-end pipeline:

- **fclones (native, fast)**: size pre-grouping + parallel hashing discovers duplicates in large trees far faster than per-file Python SHA-256. Also supports under-replicated-file audits.
- **Hardened deletion (recoverable, verified)**: same-volume quarantine moves, Recycle Bin quota pre-flight, wildcard-safe literal paths, .NET `File.Move` with a two-step rename fallback, batched moves with per-file verification and stop-on-first-failure.

The workflow is deliberately two-phase: **scan and report first; delete only after explicit user confirmation.** No file is ever touched during scanning.

## When to use

- Deduplicate a folder / drive, especially large ones (100k+ files, hundreds of GB).
- Clean repeated files while keeping the newest copy.
- Audit a tree for duplicates before migration/backup, or find under-replicated files (`--rf-under`).
- Windows environments where `Move-Item` gets "access denied" from antivirus, Recycle Bin has no quota, or paths contain `[ ] * ?`.

## Hard rules

1. **One top-level folder at a time.** Ask which folder if ambiguous. Never clean multiple drives in one run.
2. **Confirm before scanning**: target folder; duplicate criterion (default exact content); "newest" definition (default modification time).
3. **Never delete before** producing `dedupe_report.json` + `dedupe_decisions.csv`, presenting the list, and getting explicit confirmation of scope.
4. Before any destructive action, warn in bold: **"⚠️ 此操作非常危险，可能导致不可逆的数据丢失！"**, state counts and reclaimable bytes, and point to the CSV as the full list.
5. Verify after deletion: every `delete` path is gone and every `keep` path still exists. Report both counts.
6. Prefer recoverable deletion (quarantine). Only use permanent deletion with explicit confirmation.
7. Read `references/windows-battlefield.md` before any Windows deletion — it documents failures seen in real production cleanups (Recycle Bin quota degradation, comma paths, AV blocking, wildcard names, C-drive snapshot blowups).

## Workflow

### Step 1 — Clarify scope

Ask (do not assume): target folder path; duplicate criterion (exact hash / filename / both); date basis (mtime / filename date / creation time).

### Step 2 — Fast scan (read-only)

Bundled engine: `bin/fclones.exe` (v0.35.0, Windows x86_64, self-contained).

```powershell
$fclones = "<skill-dir>\bin\fclones.exe"

# Group duplicates, machine-readable report:
& $fclones group "<target>" --format csv --output "<report-dir>\fclones.csv"

# Optional: only large files / exclude subtrees / find under-replicated files:
& $fclones group "<target>" --min-size 10M --exclude "**/node_modules/**"
& $fclones group "<target>" --rf-under 2
```

`fclones group` never modifies files. Write reports to the **target volume**, not the system drive (large scans can fill C: via app snapshots — see reference).

### Step 3 — Build reviewable report

Convert fclones CSV into the deletion script's input format:

```bash
python scripts/fclones_csv_to_report.py "<report-dir>\fclones.csv" --output "<report-dir>"
```

This uses Python's `csv` module so paths with commas/quotes parse correctly — **never parse fclones CSV with a naive comma split** (a real 100k-file run produced 6797 failures from that mistake). It writes:
- `dedupe_report.json` — groups with `keep` (newest mtime; tie-break lexicographically largest path) and `delete` lists.
- `dedupe_decisions.csv` — UTF-8 BOM, one row per keep/delete decision, for human review.

Present the CSV to the user. State group count, redundant files, reclaimable bytes.

### Step 4 — Confirm deletion

Show the bold danger warning, counts, and per-directory breakdown. Confirm deletion scope and quarantine location (default: `_dedupe_quarantine` on the target volume). Explain that files are moved to quarantine and remain recoverable.

If the Recycle Bin mode is ever requested, first run the pre-flight check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_recycle_bin.ps1 -Drive 'F:'
```

If it prints `QUOTA_NOT_CONFIGURED` or `RECYCLED_FILES 0`, tell the user `SendToRecycleBin` degrades to permanent deletion; prefer quarantine instead.

### Step 5 — Delete (batched, verified, recoverable)

Always dry-run first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/delete_duplicates.ps1 -Report "<report-dir>\dedupe_report.json" -DryRun
```

Then execute:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/delete_duplicates.ps1 -Report "<report-dir>\dedupe_report.json" -QuarantineRoot "F:\_dedupe_quarantine" -BatchSize 50 -Log "<report-dir>\deletion_log.txt"
```

The script moves files (not deletes) into a mirrored quarantine tree on the same volume: instant, space-free, fully recoverable. It tries .NET `File.Move`, falls back to a two-step rename defeat AV/sandbox blocks, then to `Move-Item -LiteralPath` for wildcard names. It verifies each file and stops at the first failure. `-BatchSize` is configurable for throughput; 10 is the conservative default for precious data.

### Step 6 — Verify and report

Re-scan the target with `fclones group` and confirm zero duplicate groups remain. Confirm every `keep` path still exists. Report: groups processed, files quarantined, bytes reclaimed, quarantine location, and how to restore (move files back along the mirrored path).

If Recycle Bin mode was used, re-run `verify_recycle_bin.ps1` with `-ExpectFiles <count>` and report honestly whether files are actually recoverable.

## Restore

To undo, move files from `<quarantine>\<relative path>` back to their original location. The quarantine mirrors the source tree, so each file's path under the root is its original path minus the drive letter.

## Resources

### bin/
- `fclones.exe` — v0.35.0 native duplicate/under-replication scanner (`group` is read-only).

### scripts/
- `fclones_csv_to_report.py` — quote-aware CSV → report/CSV converter (handles comma paths).
- `scan_duplicates.py` — optional pure-Python scanner (3-phase: size → head/tail fingerprint → full/sampled SHA-256) for environments without fclones or when resumable中间 results are needed.
- `delete_duplicates.ps1` — hardened batched quarantine mover with .NET fallback and per-file verification.
- `verify_recycle_bin.ps1` — Windows Recycle Bin quota/payload checker, pre- and post-deletion.

### references/
- `windows-battlefield.md` — production-tested gotchas: Recycle Bin quota trap, AV blocking `Move-Item`, comma/wildcard paths, sandbox-protected paths, system-drive snapshot blowups, and large-volume deletion strategy. Read before Windows deletion.
