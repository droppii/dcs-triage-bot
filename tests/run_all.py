#!/usr/bin/env python3
"""Run every offline test. Run: python3 tests/run_all.py (exit 1 if any fails)."""
import subprocess, sys
from pathlib import Path

here = Path(__file__).resolve().parent
failed = []
for t in sorted(here.glob('test_*.py')):
    p = subprocess.run([sys.executable, str(t)], capture_output=True, text=True, encoding='utf-8', errors='replace',
                       env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})
    print(('PASS ' if p.returncode == 0 else 'FAIL ') + t.name)
    if p.returncode:
        failed.append(t.name)
        print((p.stdout + p.stderr)[-1500:])
sys.exit(1 if failed else 0)
