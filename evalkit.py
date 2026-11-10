"""Deterministic scoring of one bot run against a case's `expect`. Pure functions (no network, no model).

Score (same idea as the brain-agent-kit judge): 1.0 all good · 0.5 verdict ok but an anchor is missing ·
0.05 wrong verdict, a forbidden conclusion, or a citation the bot could not verify (fabrication).
"""
import json
import re


def _n(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip().casefold()


def haystack(out):
    rca = out.get('rca') or {}
    return _n(' '.join([out.get('reply_markdown', ''), out.get('summary', ''), json.dumps(out.get('evidence'), ensure_ascii=False),
                        json.dumps(rca, ensure_ascii=False)]))


def prose(out):
    rca = out.get('rca') or {}
    return _n(' '.join([out.get('reply_markdown', ''), out.get('summary', ''), str(rca.get('mechanism', ''))]))


def score_case(expect, out, effective):
    """-> {'score': float, 'checks': [(name, ok, detail)]}. `effective` = verdict the bot would publish."""
    checks = []

    def add(name, ok, detail=''):
        checks.append((name, bool(ok), detail))

    allowed = expect.get('verdict_any_of')
    if expect.get('missing_info') is True:
        add('verdict', bool(out.get('missing_info')), 'phải hỏi thông tin bắt buộc')
    elif allowed:
        add('verdict', effective in allowed, f'{effective} ∉ {allowed}' if effective not in allowed else effective)
    hay = haystack(out)
    # must_contain / must_not_contain are literal strings. (must_mention / must_not in cases are notes for humans.)
    for m in expect.get('must_contain', []):
        add('must_contain', _n(m) in hay, m)
    for m in expect.get('must_not_contain', []):
        add('must_not', _n(m) not in prose(out), m)
    for m in expect.get('forbidden_conclusions', []):
        add('forbidden', _n(m) not in prose(out), m)
    cl = expect.get('code_location')
    if cl:
        loc = (out.get('rca') or {}).get('code_location') or {}
        ok = bool(loc.get('verified')) and str(loc.get('file', '')).endswith(cl['file_suffix'])
        rng = cl.get('line_range')
        if ok and rng:
            ok = not (int(loc['line_end']) < rng[0] or int(loc['line_start']) > rng[1])
        add('code_location', ok, f"{cl['file_suffix']} {rng or ''} vs {loc.get('file')}:{loc.get('line_start')}-{loc.get('line_end')}")
    if expect.get('layer'):  # be | odoo | fe | mobile: the layer the bug really lives in (taken from the repo of the cited line)
        got = (out.get('rca') or {}).get('layer')
        add('layer', got == expect['layer'], f"{got} vs {expect['layer']}")
    add('no_fabrication', not out.get('_problems'), '; '.join(out.get('_problems') or [])[:200])
    if 'max_tool_calls' in expect:
        add('tool_budget', out.get('_tool_calls', 0) <= expect['max_tool_calls'], str(out.get('_tool_calls')))
    if 'max_cost_usd' in expect:
        add('cost', (out.get('_cost') or 0) <= expect['max_cost_usd'], str(out.get('_cost')))

    failed = {name for name, ok, _ in checks if not ok}
    if failed & {'verdict', 'forbidden', 'must_not'} or not _fab_ok(checks):
        score = 0.05
    elif failed:
        score = 0.5
    else:
        score = 1.0
    return {'score': score, 'checks': checks}


def _fab_ok(checks):
    return all(ok for name, ok, _ in checks if name == 'no_fabrication')


def compare_baseline(current, baseline, max_drop=0.2):
    """current/baseline: {case: mean_score}. -> list of regression messages (empty = no regression)."""
    msgs = []
    for case, base in baseline.items():
        cur = current.get(case)
        if cur is not None and base - cur > max_drop:
            msgs.append(f'{case}: {base:.2f} → {cur:.2f}')
    common = [c for c in baseline if c in current]
    if common:
        mb = sum(baseline[c] for c in common) / len(common)
        mc = sum(current[c] for c in common) / len(common)
        if mc < mb - 1e-9:
            msgs.append(f'mean: {mb:.2f} → {mc:.2f}')
    return msgs
