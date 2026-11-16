#!/usr/bin/env python3
"""Find credentials committed inside the knowledge/ clones, so the brain (which can Grep them) never reads those files.

Writes data/secret_files.txt (paths relative to knowledge/, one per line): bot.py turns each into a Read/Grep deny rule.
Prints file and kind of secret, NEVER the value. Re-run after every `tools/setup.sh` (setup.sh does it).
The well-known names (.env, *.pem, application-prod*...) are denied anyway, see guard.SECRET_FILE_GLOBS.
Run: python3 tools/scan_secrets.py [--fail] [--root knowledge]     --fail: exit 1 if anything was found
"""
import argparse, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.gradle', 'Pods'}      # build output (dist, build, target) IS scanned: bundles carry real keys
SKIP_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.pdf', '.zip', '.gz', '.jar', '.class', '.so', '.dll', '.exe', '.woff',
            '.woff2', '.ttf', '.eot', '.mp4', '.mov', '.lock', '.svg', '.snap'}
MAX_BYTES = 512 * 1024

PLACEHOLDER = re.compile(r'(?i)change.?me|your[_-]|<[^>]+>|\$\{|\{\{|example|dummy|sample|placeholder|xxxx|\*{3,}|todo|secret_key_here|'
                         r'^(?:password|secret|token|test|null|none|true|false)\d*$')
RULES = [
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')),
    ('GitHub token', re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b')),
    ('Slack token', re.compile(r'\bxox[abprs]-[\w-]{10,}')),
    ('API key (sk-)', re.compile(r'\bsk-[A-Za-z0-9_-]{24,}')),
    ('URL with password', re.compile(r'\b[a-z][a-z0-9+.\-]{1,20}://[^\s/:@\'"]+:([^\s/@\'"]{4,})@[\w.\-]+')),
    ('hard-coded credential', re.compile(r'(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\b["\']?\s*[:=]\s*'
                                         r'["\']([^"\'\s]{8,})["\']')),
]


def hits(text):
    found = []
    for kind, rx in RULES:
        for m in rx.finditer(text):
            value = m.group(1) if m.groups() else m.group(0)
            if kind in ('URL with password', 'hard-coded credential') and PLACEHOLDER.search(value):
                continue
            found.append(kind)
            break
    return found


def scan(root):
    results = []
    for f in sorted(root.rglob('*')):
        if not f.is_file() or any(part in SKIP_DIRS for part in f.relative_to(root).parts) or f.suffix.lower() in SKIP_EXT:
            continue
        try:
            with open(f, 'rb') as fh:                  # the first 512 KB: a big file is read in part, never skipped whole
                text = fh.read(MAX_BYTES).decode('utf-8', errors='ignore')
        except OSError:
            continue
        for kind in hits(text):
            results.append((f.relative_to(root).as_posix(), kind))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=str(ROOT / 'knowledge'))
    ap.add_argument('--out', default=str(ROOT / 'data' / 'secret_files.txt'))
    ap.add_argument('--fail', action='store_true')
    a = ap.parse_args()
    root = Path(a.root)
    if not root.is_dir():
        sys.exit(f'{root} does not exist: run tools/setup.sh first')
    results = scan(root)
    files = list(dict.fromkeys(p for p, _ in results))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(files) + ('\n' if files else ''), encoding='utf-8')
    for path, kind in results:
        print(f'{kind}: {path}')
    print(f'{len(files)} file(s) with credentials; denied to the brain via {out}')
    sys.exit(1 if files and a.fail else 0)


if __name__ == '__main__':
    main()
