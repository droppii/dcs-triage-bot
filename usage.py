"""Token / cost accounting for each analysis. Pure functions: no network, no model calls.

Source of truth is the `result` event of `claude -p --output-format stream-json`: `modelUsage` (per model, includes
cache and thinking tokens) with `usage` as fallback. Cost is Anthropic's list-price estimate (`total_cost_usd`),
not an invoice.
"""
import json
from datetime import datetime

_KEYS = ('input', 'output', 'cache_read', 'cache_write', 'thinking', 'cost_usd', 'turns', 'duration_s')


def empty():
    return {k: 0 for k in _KEYS} | {'models': []}


def extract(result):
    """`result` stream-json event (or None) -> usage dict. Unknown/missing fields count as 0."""
    u = empty()
    if not isinstance(result, dict):
        return u
    mu = result.get('modelUsage') or {}
    if mu:
        for model, m in mu.items():
            u['input'] += int(m.get('inputTokens') or 0)
            u['output'] += int(m.get('outputTokens') or 0)
            u['cache_read'] += int(m.get('cacheReadInputTokens') or 0)
            u['cache_write'] += int(m.get('cacheCreationInputTokens') or 0)
            u['thinking'] += int(m.get('thinkingTokens') or 0)
            u['models'].append(model)
    else:  # older CLI output
        raw = result.get('usage') or {}
        u['input'] = int(raw.get('input_tokens') or 0)
        u['output'] = int(raw.get('output_tokens') or 0)
        u['cache_read'] = int(raw.get('cache_read_input_tokens') or 0)
        u['cache_write'] = int(raw.get('cache_creation_input_tokens') or 0)
        u['thinking'] = int((raw.get('output_tokens_details') or {}).get('thinking_tokens') or 0)
    u['cost_usd'] = float(result.get('total_cost_usd') or 0)
    u['turns'] = int(result.get('num_turns') or 0)
    u['duration_s'] = round((result.get('duration_ms') or 0) / 1000, 1)
    return u


def merge(*items):
    """Sum several usage dicts (brain + verifier)."""
    t = empty()
    for u in items:
        if not u:
            continue
        for k in _KEYS:
            t[k] += u.get(k) or 0
        t['models'] += [m for m in u.get('models', []) if m not in t['models']]
    t['cost_usd'] = round(t['cost_usd'], 6)
    t['duration_s'] = round(t['duration_s'], 1)
    return t


def total_tokens(u):
    """Billable-ish total: fresh input + output + cache writes + cache reads. Thinking is already inside output."""
    return u['input'] + u['output'] + u['cache_write'] + u['cache_read']


def _n(x):
    return f'{int(x):,}'.replace(',', '.')


def note(u, parts=None):
    """One line for the ticket's AI Note. `parts` = {'brain': usage, 'verifier': usage} adds a breakdown."""
    s = (f"token: {_n(u['input'])} vào · {_n(u['output'])} ra (thinking {_n(u['thinking'])}) · "
         f"cache đọc {_n(u['cache_read'])} / ghi {_n(u['cache_write'])} · tổng {_n(total_tokens(u))} · "
         f"~${u['cost_usd']:.3f} · {u['turns']} lượt · {u['duration_s']:.0f}s")
    if u['models']:
        s += ' · ' + ', '.join(u['models'])
    if parts and len([p for p in parts.values() if p and total_tokens(p)]) > 1:
        s += ' [' + '; '.join(f"{k} {_n(total_tokens(p))} tok ${p['cost_usd']:.3f}" for k, p in parts.items() if p) + ']'
    return s


def short(u):
    """Compact form for a dedicated Base column."""
    return f"{_n(total_tokens(u))} tok · {_n(u['output'])} out · ${u['cost_usd']:.3f}"


def ledger_line(label, role, u, log=None, is_error=False, now=None):
    """One JSON line for logs/usage.jsonl (append-only). Contains no ticket text."""
    return json.dumps({'ts': (now or datetime.now()).isoformat(timespec='seconds'), 'label': label, 'role': role,
                       'models': u['models'], 'input': u['input'], 'output': u['output'], 'cache_read': u['cache_read'],
                       'cache_write': u['cache_write'], 'thinking': u['thinking'], 'total': total_tokens(u),
                       'cost_usd': u['cost_usd'], 'turns': u['turns'], 'duration_s': u['duration_s'],
                       'log': log, 'error': bool(is_error)}, ensure_ascii=False)


def summarize(rows):
    """Ledger rows -> {'runs','tickets','tokens','cost_usd','per_ticket_tokens','per_ticket_cost','by_role','by_day','top'}."""
    by_label, by_role, by_day = {}, {}, {}
    for r in rows:
        k = r['label']
        s = by_label.setdefault(k, {'tokens': 0, 'cost_usd': 0.0, 'runs': 0})
        s['tokens'] += r['total']; s['cost_usd'] += r['cost_usd']; s['runs'] += 1
        a = by_role.setdefault(r['role'], {'tokens': 0, 'cost_usd': 0.0, 'runs': 0})
        a['tokens'] += r['total']; a['cost_usd'] += r['cost_usd']; a['runs'] += 1
        d = by_day.setdefault(r['ts'][:10], {'tokens': 0, 'cost_usd': 0.0, 'runs': 0})
        d['tokens'] += r['total']; d['cost_usd'] += r['cost_usd']; d['runs'] += 1
    n = len(by_label) or 1
    tokens = sum(r['total'] for r in rows)
    cost = round(sum(r['cost_usd'] for r in rows), 4)
    top = sorted(by_label.items(), key=lambda kv: -kv[1]['cost_usd'])[:5]
    return {'runs': len(rows), 'tickets': len(by_label), 'tokens': tokens, 'cost_usd': cost,
            'per_ticket_tokens': tokens // n, 'per_ticket_cost': round(cost / n, 4),
            'by_role': by_role, 'by_day': dict(sorted(by_day.items())), 'top': top}
