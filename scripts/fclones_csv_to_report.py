#!/usr/bin/env python3
"""Convert fclones CSV group output into dedupe_report.json + dedupe_decisions.csv
consumed by delete_duplicates.ps1.

fclones CSV format (v0.35): size,hash,count,files... where paths containing commas
are quoted with doubled quotes for embedded quotes. NOTE column 3 is the group
COUNT, not a path — paths start at column 4. A naive -split ',' corrupts quoted
paths, so this script uses Python's csv module (correct quoting) -- a real
production bug in a 100k-file cleanup.

Keep policy: newest mtime wins; tie-break lexicographically largest path.

Usage:
  python fclones_csv_to_report.py <fclones.csv> --output <report-dir>

Outputs:
  <report-dir>/dedupe_report.json
  <report-dir>/dedupe_decisions.csv
"""
import argparse
import csv
import json
import os
import sys


def parse_size(s):
    """fclones emits plain byte counts in CSV mode."""
    try:
        return int(s)
    except ValueError:
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('csvfile', help='fclones group --format csv output file')
    ap.add_argument('--output', required=True, help='Report output directory')
    args = ap.parse_args()

    os.makedirs(args.output, exist_ok=True)
    groups = []
    errors = []
    with open(args.csvfile, newline='', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        header = next(reader, None)
        # fclones 0.35 header: size,hash,count,files... (paths start at index 3)
        for lineno, row in enumerate(reader, 2):
            if len(row) < 4:
                continue
            size = parse_size(row[0])
            paths = [p.replace('\\\\', '\\') for p in row[3:] if p]
            items = []
            for p in paths:
                try:
                    mt = os.stat(p).st_mtime_ns
                except OSError as e:
                    errors.append(f'{p}: {e}')
                    continue
                items.append({'path': p, 'mtime_ns': mt, 'size': size})
            if len(items) < 2:
                continue
            # Newest first; tie-break lexicographically largest path.
            items.sort(key=lambda x: (x['mtime_ns'], x['path']), reverse=True)
            groups.append({
                'verification': 'exact',
                'size': size,
                'keep': items[0],
                'delete': items[1:],
            })

    groups.sort(key=lambda g: sum(i['size'] for i in g['delete']), reverse=True)
    redundant = sum(len(g['delete']) for g in groups)
    reclaim = sum(i['size'] for g in groups for i in g['delete'])
    report = {
        'source': args.csvfile,
        'duplicate_groups': groups,
        'redundant_files': redundant,
        'reclaim_bytes': reclaim,
        'sampled_groups': 0,
        'unresolved': [],
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
    print(f'groups={len(groups)} redundant={redundant} '
          f'reclaim_GB={round(reclaim / 1024**3, 2)} errors={len(errors)}')
    print(f'report: {rp}')
    print(f'csv:    {cp}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
