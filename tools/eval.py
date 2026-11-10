#!/usr/bin/env python3
"""Run the cases in cases/*.json N times, score each run deterministically, compare with evals/baseline.json.

  python3 tools/eval.py                          all cases, 3 runs each (calls the model: costs money)
  python3 tools/eval.py cases/stg-voucher-ended.json --runs 5
  python3 tools/eval.py --from-log cases/stg-voucher-ended.json=logs/dry-stg-voucher-ended-....jsonl
                                                 re-score a saved run, no model call (also re-verifies citations)
  python3 tools/eval.py --freeze cases/stg-voucher-ended.json=logs/dry-....jsonl
                                                 save that run to evals/fixtures/ (New Relic logs expire in ~30 days)
  python3 tools/eval.py --from-fixtures          re-score every frozen run: checks the checker itself, free
  python3 tools/eval.py --write-baseline         store this run's per-case means as evals/baseline.json
Exit 1 when a case drops more than 0.2 vs the baseline, or the mean drops. Case format: see CONTRIBUTING.md.
Note: replaying a fixture re-scores a recorded answer; it does not re-run the agent against recorded tool results.
"""
import argparse, json, shutil, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot, evalkit, usage

CASES = ROOT / 'cases'
FIXTURES = ROOT / 'evals' / 'fixtures'
BASELINE = ROOT / 'evals' / 'baseline.json'


def load(path):
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    return doc.get('ticket', doc), doc.get('expect', {})


def report(name, results):
    n = len(results)
    mean = sum(r['score'] for r in results) / n
    print(f"\n{name}: mean {mean:.2f} over {n} run(s)")
    for i, r in enumerate(results, 1):
        bad = [f'{c}({d})' for c, ok, d in r['checks'] if not ok]
        print(f"  #{i} score {r['score']:.2f} verdict {r['verdict']} tools {r.get('tools')} tokens {usage._n(r['tokens']) if r.get('tokens') else '-'} cost {r.get('cost')}"
              + (f"  ✗ {'; '.join(bad)}" if bad else '  ✓'))
    return mean


def score_out(expect, out):
    r = evalkit.score_case(expect, out, bot.verdict(out))
    r.update(verdict=bot.verdict(out), tools=out.get('_tool_calls'), cost=out.get('_cost'),
             tokens=usage.total_tokens(out['_usage']) if out.get('_usage') else None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cases', nargs='*')
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--from-log', action='append', default=[], metavar='CASE=LOG')
    ap.add_argument('--freeze', action='append', default=[], metavar='CASE=LOG')
    ap.add_argument('--from-fixtures', action='store_true')
    ap.add_argument('--write-baseline', action='store_true')
    a = ap.parse_args()

    for spec in a.freeze:
        case, log = spec.split('=', 1)
        FIXTURES.mkdir(parents=True, exist_ok=True)
        dest = FIXTURES / (Path(case).stem + '.jsonl')
        shutil.copy(log, dest)
        bot.redact_log(dest)  # fixtures are committed: mask PII/secrets first
        print('frozen ->', dest.relative_to(ROOT))
    if a.freeze:
        return

    means = {}
    if a.from_log or a.from_fixtures:
        pairs = [tuple(s.split('=', 1)) for s in a.from_log]
        if a.from_fixtures:
            pairs += [(str(CASES / (f.stem + '.json')), str(f)) for f in sorted(FIXTURES.glob('*.jsonl'))]
        for case, log in pairs:
            _, expect = load(case)
            means[Path(case).stem] = report(Path(case).stem, [score_out(expect, bot.finalize_log(Path(log)))])
    else:
        files = [Path(c) for c in a.cases] or sorted(p for p in CASES.glob('*.json') if not p.name.startswith('_'))
        for f in files:
            ticket, expect = load(f)
            results = []
            for i in range(a.runs):
                try:
                    results.append(score_out(expect, bot.run_brain(ticket, label=f'eval-{f.stem}-{i}')))
                except Exception as e:  # a crashed run is a failed run
                    results.append({'score': 0.0, 'checks': [('run', False, str(e)[:120])], 'verdict': '-', 'tools': None, 'cost': None})
            means[f.stem] = report(f.stem, results)

    if not means:
        return
    print(f"\nOVERALL mean {sum(means.values()) / len(means):.2f}")
    if a.write_baseline:
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text(json.dumps({'written': f'{datetime.now():%Y-%m-%d}', 'cases': means}, indent=1) + '\n')
        print('baseline written:', BASELINE.relative_to(ROOT))
    elif BASELINE.exists():
        regress = evalkit.compare_baseline(means, json.loads(BASELINE.read_text(encoding='utf-8'))['cases'])
        if regress:
            print('REGRESSION vs baseline:', *regress, sep='\n  ')
            sys.exit(1)
        print('no regression vs baseline')


if __name__ == '__main__':
    main()
