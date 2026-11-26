#!/usr/bin/env python3
"""Scan one directory tree for content-identical duplicate files.

Three-phase design keeps I/O tractable on very large trees:
  1. metadata walk      - group files by exact size
  2. fast fingerprint   - SHA-256 of head+tail (64 KiB each), parallel
  3. verification       - full SHA-256 for fingerprint matches; for files
                          above --sample-threshold use multi-segment sampling
                          instead of a full read (fast, near-certain, marked
                          "sampled" in the report)

Writes <output>/dedupe_report.json with, for each duplicate group:
  keep  = newest mtime (tie-break: lexicographically largest path)
  delete = all other members
Also writes <output>/dedupe_decisions.csv (UTF-8 BOM) for human review.

Usage:
  python scan_duplicates.py <root> --output <dir> [options]

  root                 Single top-level folder to scan (required).
  --output <dir>       Where reports are written (required).
  --skip NAME          Top-level directory name to prune (repeatable).
                       Defaults: $RECYCLE.BIN, System Volume Information, .workbuddy
  --workers N          Thread pool size (default 12).
  --sample-threshold N Bytes above which full hashing is replaced by sampling
                       (default 268435456 = 256 MiB). Set 0 to disable sampling.
  --min-size N         Ignore files smaller than N bytes (default 0).
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed


def log(msg):
    print(msg, flush=True)


def walk_metadata(root, skip, min_size):
    """Return {size: [(path, mtime_ns), ...]} and error list."""
    by_size = defaultdict(list)
    errors = []
    count = 0

    def on_error(e):
        errors.append(str(e))

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=on_error):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p)
            except Exception as e:
                errors.append(f'{p}: {e}')
                continue
            if st.st_size < min_size:
                continue
            by_size[st.st_size].append((p, st.st_mtime_ns))
            count += 1
    return by_size, errors, count


def parallel_map(fn, tasks, workers, label='', every=2000):
    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, t) for t in tasks]
        for n, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if every and n % every == 0:
                log(f'  {label} {n}/{len(tasks)} sec={round(time.time() - t0, 1)}')
    return results


def fingerprint(item):
    """Head+tail 64 KiB SHA-256. Cheap prefilter before full hashing."""
    size, p, mt = item
    h = hashlib.sha256()
    chunk = 64 * 1024
    try:
        with open(p, 'rb') as f:
            h.update(f.read(chunk))
            if size > chunk * 2:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
    except Exception as e:
        return ('ERR', str(e), p, mt, size)
    return (size, h.hexdigest(), p, mt, size)


def strong_sample(item):
    """Sample N evenly-spaced segments plus head/tail. For huge files."""
    p, size = item
    nseg = 8
    seg = 512 * 1024
    h = hashlib.sha256()
    try:
        with open(p, 'rb') as f:
            for k in range(nseg):
                f.seek(int(size * k / nseg))
                h.update(f.read(seg))
            if size > seg:
                f.seek(-seg, os.SEEK_END)
                h.update(f.read(seg))
    except Exception as e:
        return (p, None, str(e))
    return (p, h.hexdigest(), None)


def full_hash(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
                h.update(chunk)
    except Exception as e:
        return (path, None, str(e))
    return (path, h.hexdigest(), None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root', help='Single top-level folder to scan')
    ap.add_argument('--output', required=True, help='Report output directory')
    ap.add_argument('--skip', action='append', default=None,
                    help='Top-level dir name to prune (repeatable)')
    ap.add_argument('--workers', type=int, default=12)
    ap.add_argument('--sample-threshold', type=int, default=268435456,
                    help='Bytes above which sampling replaces full hash (0 disables)')
    ap.add_argument('--min-size', type=int, default=0)
    args = ap.parse_args()

    skip = set(args.skip) if args.skip else {
        '$RECYCLE.BIN', 'System Volume Information', '.workbuddy'
    }
    os.makedirs(args.output, exist_ok=True)
    t0 = time.time()

    # Phase 1: metadata
    by_size, errors, nfiles = walk_metadata(args.root, skip, args.min_size)
    cands = [(size, p, mt) for size, pairs in by_size.items()
             if len(pairs) > 1 for p, mt in pairs]
    log(f'[1/3] files={nfiles} size_groups={len(by_size)} '
        f'candidates={len(cands)} sec={round(time.time()-t0,1)}')
    if not cands:
        log('No size-collision candidates; no duplicates possible.')

    # Phase 2: fast fingerprint
    fp_groups = defaultdict(list)
    fp_res = parallel_map(fingerprint, cands, args.workers, 'fingerprint')
    for size, digest, p, mt, sz in fp_res:
        if size == 'ERR':
            errors.append(f'fingerprint {p}: {digest}')
            continue
        fp_groups[(size, digest)].append({'path': p, 'mtime_ns': mt, 'size': sz})
    likely = {k: v for k, v in fp_groups.items() if len(v) > 1}
    log(f'[2/3] fingerprint_groups={len(fp_groups)} likely_groups={len(likely)} '
        f'files_to_verify={sum(len(v) for v in likely.values())} '
        f'sec={round(time.time()-t0,1)}')

    # Phase 3a: full hash for normal-size files, sampling for huge ones
    verify_tasks = []
    for items in likely.values():
        for it in items:
            if args.sample_threshold and it['size'] > args.sample_threshold:
                verify_tasks.append(('sample', (it['path'], it['size'])))
            else:
                verify_tasks.append(('full', it['path']))

    full_res = parallel_map(full_hash,
                            [t for kind, t in verify_tasks if kind == 'full'],
                            args.workers, 'full_hash', every=500)
    sample_res = parallel_map(strong_sample,
                              [t for kind, t in verify_tasks if kind == 'sample'],
                              args.workers, 'sample', every=100)

    full_by_path = {}
    for p, digest, err in full_res:
        if err:
            errors.append(f'full_hash {p}: {err}')
        else:
            full_by_path[p] = digest
    sample_by_path = {}
    for p, digest, err in sample_res:
        if err:
            errors.append(f'sample {p}: {err}')
        else:
            sample_by_path[p] = digest

    # A fingerprint group is keyed by size, so all its members are either all
    # full-hashed or all sampled. Pick one verification mode per group.
    verified = defaultdict(list)  # (size, key) -> items
    unresolved = []
    sampled_groups = 0
    for key, items in likely.items():
        size = key[0]
        sampled = bool(args.sample_threshold and size > args.sample_threshold)
        source = sample_by_path if sampled else full_by_path
        by_digest = defaultdict(list)
        for it in items:
            digest = source.get(it['path'])
            if digest is None:
                unresolved.append(it)
            else:
                by_digest[digest].append(it)
        for digest, sub in by_digest.items():
            if sampled:
                sampled_groups += 1
                verified[(size, 'SAMPLE:' + digest)].extend(sub)
            else:
                verified[(size, digest)].extend(sub)

    # Build groups
    groups = []
    for (size, key), items in verified.items():
        if len(items) < 2:
            continue
        items = sorted(items, key=lambda x: (x['mtime_ns'], x['path']), reverse=True)
        verification = 'sampled' if key.startswith('SAMPLE:') else 'exact'
        groups.append({
            'verification': verification,
            'size': size,
            'keep': items[0],
            'delete': items[1:],
        })
    groups.sort(key=lambda g: sum(i['size'] for i in g['delete']), reverse=True)

    redundant = sum(len(g['delete']) for g in groups)
    reclaim = sum(i['size'] for g in groups for i in g['delete'])
    report = {
        'root': args.root,
        'total_files': nfiles,
        'duplicate_groups': groups,
        'redundant_files': redundant,
        'reclaim_bytes': reclaim,
        'sampled_groups': sampled_groups,
        'unresolved': unresolved,
        'errors': errors[:200],
    }
    rp = os.path.join(args.output, 'dedupe_report.json')
    with open(rp, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    cp = os.path.join(args.output, 'dedupe_decisions.csv')
    with open(cp, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['verification', 'size_bytes', 'action', 'mtime_ns', 'path'])
        for g in groups:
            w.writerow([g['verification'], g['size'], 'keep',
                        g['keep']['mtime_ns'], g['keep']['path']])
            for i in g['delete']:
                w.writerow([g['verification'], g['size'], 'delete',
                            i['mtime_ns'], i['path']])

    log(f'[3/3] groups={len(groups)} redundant={redundant} '
        f'reclaim_GB={round(reclaim / 1024**3, 2)} '
        f'sampled_groups={sampled_groups} unresolved={len(unresolved)} '
        f'errors={len(errors)} sec={round(time.time()-t0,1)}')
    log(f'report: {rp}')
    log(f'csv:    {cp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
