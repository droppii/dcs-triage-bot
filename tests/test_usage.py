#!/usr/bin/env python3
"""Token accounting. Run: python3 tests/test_usage.py"""
import json, sys, tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot, rca, usage

# a result event shaped like the real CLI output (values taken from a real haiku run)
RESULT = {'type': 'result', 'is_error': False, 'total_cost_usd': 0.0403897, 'num_turns': 4, 'duration_ms': 23599,
          'usage': {'input_tokens': 4, 'output_tokens': 4721, 'cache_read_input_tokens': 280830,
                    'cache_creation_input_tokens': 14195, 'output_tokens_details': {'thinking_tokens': 1221}},
          'modelUsage': {'claude-haiku-5-5': {'inputTokens': 1416, 'outputTokens': 5136, 'cacheReadInputTokens': 280830,
                                              'cacheCreationInputTokens': 14195, 'thinkingTokens': 1613, 'costUSD': 0.0403897}}}
u = usage.extract(RESULT)
assert (u['input'], u['output'], u['cache_read'], u['cache_write'], u['thinking']) == (1416, 5136, 280830, 14195, 1613), u
assert u['cost_usd'] == 0.0403897 and u['turns'] == 4 and u['duration_s'] == 23.6 and u['models'] == ['claude-haiku-5-5']
assert usage.total_tokens(u) == 1416 + 5136 + 14195 + 280830

# older CLI without modelUsage falls back to `usage`; garbage / missing -> zeros, never raises
old = usage.extract({k: v for k, v in RESULT.items() if k != 'modelUsage'})
assert (old['input'], old['output'], old['thinking']) == (4, 4721, 1221)
assert usage.extract(None)['output'] == 0 and usage.extract({})['cost_usd'] == 0 and usage.extract('x')['input'] == 0

# merge (brain + verifier) and the note line
v = usage.extract({**RESULT, 'total_cost_usd': 0.01, 'num_turns': 2})
m = usage.merge(u, v)
assert m['output'] == 2 * 5136 and m['turns'] == 6 and abs(m['cost_usd'] - 0.0503897) < 1e-6 and m['models'] == ['claude-haiku-5-5']
n = usage.note(m, {'brain': u, 'verifier': v})
assert '1.416' not in n and '2.832 vào' in n and 'cache đọc 561.660' in n and '~$0.050' in n and 'verifier' in n and 'brain' in n, n
assert 'verifier' not in usage.note(u, {'brain': u})        # no breakdown when there is a single run
assert usage.short(u).endswith('$0.040') and 'tok' in usage.short(u)

# ledger line has no ticket text; summarize groups by label (CODE and CODE-verify are different labels)
L = [json.loads(usage.ledger_line('D1-1', 'brain', u, 'a.jsonl', now=datetime(2026, 11, 10, 9, 0))),
     json.loads(usage.ledger_line('D1-1-verify', 'verifier', v, 'b.jsonl', now=datetime(2026, 11, 10, 9, 5))),
     json.loads(usage.ledger_line('D2-1', 'brain', u, 'c.jsonl', is_error=True, now=datetime(2026, 11, 11, 8, 0)))]
assert L[0]['total'] == usage.total_tokens(u) and L[2]['error'] and set(L[0]) >= {'ts', 'label', 'role', 'cost_usd', 'log'}
s = usage.summarize(L)
assert s['runs'] == 3 and s['tickets'] == 3 and s['by_role']['verifier']['runs'] == 1 and list(s['by_day']) == ['2026-11-10', '2026-11-11']

# bot side: record_usage appends one line per run (also for a crashed run), ticket_usage sums brain + verifier
tmp = Path(tempfile.mkdtemp(prefix='dcs-usage-'))
old_logs = bot.LOGS
bot.LOGS = tmp
try:
    def make_log(name, result):
        p = tmp / name
        p.write_text(json.dumps({'type': 'system'}) + '\n' + (json.dumps(result) + '\n' if result else ''), encoding='utf-8')
        return p
    bot.record_usage(make_log('a.jsonl', RESULT), 'D9-1', 'brain')
    bot.record_usage(make_log('b.jsonl', {**RESULT, 'total_cost_usd': 0.01}), 'D9-1-verify', 'verifier')
    bot.record_usage(make_log('c.jsonl', None), 'D9-2', 'brain')            # crashed before any result event
    rows = bot.ledger_rows()
    assert len(rows) == 3 and rows[2]['error'] and rows[2]['total'] == 0, rows
    t = bot.ticket_usage('D9-1')
    assert t['runs'] == 2 and abs(t['cost_usd'] - 0.0503897) < 1e-4 and bot.ticket_usage('nope') is None

    # the note on the ticket: this run, breakdown, and the running total for the ticket
    ok3 = {k: {'status': 'confirmed', 'detail': 'x'} for k in ('source_be', 'db', 'newrelic')}
    out = {'type': 'B', 'evidence': ok3, 'priority_suggested': 'Low', 'summary': 's', 'sources': ['nr'], '_log': 'a.jsonl',
           '_usage': usage.merge(u, v), '_usage_parts': {'brain': u, 'verifier': v}, '_code': 'D9-1', 'missing_info': []}
    f = bot.bot_fields(out, 'Đã trả lời')
    assert 'token:' in f['AI Note'] and 'tổng' in f['AI Note'] and 'verifier' in f['AI Note'], f['AI Note']
    assert 'cả ticket' not in f['AI Note']                                   # first analysis: nothing earlier to add
    bot.record_usage(make_log('d.jsonl', RESULT), 'D9-1', 'brain')           # a second analysis of the same ticket
    out2 = {**out, '_usage': u, '_usage_parts': {'brain': u}}
    assert 'cả ticket: 3 lần chạy' in bot.bot_fields(out2, 'Đã trả lời')['AI Note']
    # optional dedicated column
    bot.USAGE['field'] = 'AI Usage'
    assert 'tok' in bot.bot_fields(out, 'Đã trả lời')['AI Usage']
    bot.USAGE['field'] = ''
    assert 'AI Usage' not in bot.bot_fields(out, 'Đã trả lời')
    # a failure still reports what was spent
    assert 'đã tốn' in bot.error_note('D9-1', RuntimeError('brain failed')) and bot.error_note('zzz', RuntimeError('x')) == 'x'
    # without usage (old callers) nothing breaks
    assert 'token:' not in bot.bot_fields({k: v for k, v in out.items() if k != '_usage'}, 'Đã trả lời')['AI Note']
    # ledger off -> no file growth
    bot.USAGE['ledger'] = False
    before = len(bot.ledger_rows())
    bot.record_usage(make_log('e.jsonl', RESULT), 'D9-3', 'brain')
    assert len(bot.ledger_rows()) == before
    bot.USAGE['ledger'] = True
finally:
    bot.LOGS = old_logs

print('ok usage')
