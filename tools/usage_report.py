#!/usr/bin/env python3
"""Token / cost report from logs/usage.jsonl (one line per `claude -p` run, written by bot.py).

  python3 tools/usage_report.py                 everything
  python3 tools/usage_report.py --since 2026-11-01
  python3 tools/usage_report.py --ticket D1106-005
Cost is the CLI's list-price estimate, not an invoice. Labels: ticket code (analysis), CODE-verify (verifier),
dry-<case> (bot.py --dry-ticket), eval-<case>-<n> (tools/eval.py).
"""
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='YYYY-MM-DD')
    ap.add_argument('--ticket', help='ticket code (matches CODE and CODE-verify)')
    ap.add_argument('--ledger', default=str(ROOT / 'logs' / 'usage.jsonl'))
    a = ap.parse_args()
    p = Path(a.ledger)
    if not p.exists():
        sys.exit(f'no ledger at {p} (written by bot.py after each run)')
    rows = [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    if a.since:
        rows = [r for r in rows if r['ts'][:10] >= a.since]
    if a.ticket:
        rows = [r for r in rows if r['label'] in (a.ticket, a.ticket + '-verify')]
    if not rows:
        sys.exit('no runs match')
    s = usage.summarize(rows)
    n = usage._n
    print(f"{s['runs']} runs · {s['tickets']} labels · {n(s['tokens'])} tokens · ~${s['cost_usd']:.3f}")
    print(f"average per label: {n(s['per_ticket_tokens'])} tokens · ~${s['per_ticket_cost']:.3f}")
    print('\nby role:')
    for k, v in s['by_role'].items():
        print(f"  {k:9} {v['runs']:4} runs  {n(v['tokens']):>14} tok  ~${v['cost_usd']:.3f}")
    print('\nby day:')
    for k, v in s['by_day'].items():
        print(f"  {k}  {v['runs']:4} runs  {n(v['tokens']):>14} tok  ~${v['cost_usd']:.3f}")
    print('\nmost expensive:')
    for k, v in s['top']:
        print(f"  {k:40} {n(v['tokens']):>14} tok  ~${v['cost_usd']:.3f}  ({v['runs']} runs)")
    errs = [r for r in rows if r.get('error')]
    if errs:
        print(f"\n{len(errs)} run(s) ended in error but still cost ~${sum(r['cost_usd'] for r in errs):.3f}")


if __name__ == '__main__':
    main()
