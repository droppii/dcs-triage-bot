#!/usr/bin/env python3
"""Offline self-check for bot.py pure logic (no Lark / Claude calls). Run: python3 test_bot.py"""
from datetime import datetime
import bot

# sanitize: secrets and PII never reach the thread
s = bot.sanitize('pwd=abc123 token: xyz postgres://u:p@h/db gọi 0912345678 mail a.b@droppii.vn CCCD 012345678901 đơn 2611060001')
assert 'abc123' not in s and 'xyz' not in s and 'u:p@h' not in s, s
assert '0912345678' not in s and 'a.b@droppii.vn' not in s and '012345678901' not in s, s
assert '2611060001' in s, 'order-like numbers must survive'

# write guard: anything outside the sandbox is refused before any call is made
for args in (['base', '+record-batch-update', '--base-token', bot.PROD_BASE],
             ['im', '+messages-send', '--chat-id', 'oc_b52993daa65c482e0968e0faf70fd20e'],
             ['im', '+messages-reply', '--message-id', 'om_not_from_sandbox']):
    try:
        bot.lark_write(*args)
        raise AssertionError(f'not blocked: {args}')
    except PermissionError:
        pass

# rows_to_dicts + pick_oncall
data = {'fields': ['FromDate', 'ToDate', 'Product', 'Person', 'BA-QA'], 'record_id_list': ['r1', 'r2'],
        'data': [['2026-11-14T00:00:00.000+07:00', '2026-11-15T00:00:00.000+07:00', ['PDP Portal'],
                  [{'id': 'ou_1', 'name': 'Dev A'}], [{'id': 'ou_2', 'name': 'BA B'}]],
                 ['2026-11-06T00:00:00.000+07:00', '2026-11-07T00:00:00.000+07:00', ['Biz App', 'Admin Dashboard'],
                  [{'id': 'ou_3', 'name': 'Dev C'}], [{'id': 'ou_4', 'name': 'BA D'}]]]}
rows = bot.rows_to_dicts(data)
assert rows[1]['record_id'] == 'r2'
now = datetime(2026, 11, 6, 15, 0, tzinfo=bot.TZ)
assert bot.pick_oncall(rows, 'Admin Dashboard', now) == ('Dev C', 'BA D')
assert bot.pick_oncall(rows, 'Odoo', now) == ('', '')

# compose_reply: priority change explained, routing only by name, nothing routed while info is missing
ok3 = {k: {'status': 'confirmed', 'detail': 'x'} for k in ('source_be', 'db', 'newrelic')}
out = {'type': 'C', 'confidence': 0.8, 'evidence': ok3, 'priority_suggested': 'Medium', 'priority_reason': '1 đơn',
       'missing_info': [], 'reply_markdown': 'Lỗi do X.', 'sources': ['nr'], 'summary': 's', '_log': 'l'}
r = bot.compose_reply(out, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'High → Medium' in r and 'Chuyển: Dev C' in r and '<at' not in r and 'C - Bug' in r, r
assert 'Chuyển' not in bot.compose_reply({**out, 'missing_info': ['mã đơn']}, {'Priority': ['High']}, 'Dev C', 'BA D')
assert bot.bot_fields(out, 'Đã chuyển người')['AI Type'] == ['C - Bug']

# 100% gate (dev rule): one step not confirmed -> no conclusion, routed to on-call, Base shows '?'
weak = {**out, 'type': 'B', 'evidence': {**ok3, 'newrelic': {'status': 'not_found', 'detail': 'log hết hạn'}}}
assert bot.verdict(weak) == '?'
r = bot.compose_reply(weak, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'Loại: ? - Không chắc' in r and 'New Relic ✗ không thấy' in r and 'Chuyển: Dev C' in r, r
assert bot.bot_fields(weak, 'Đã chuyển người')['AI Type'] == ['? - Không chắc']
assert bot.verdict({**weak, 'type': 'E'}) == 'E'  # feature requests go to PO/BA, no investigation gate

print('ok')

# created_at: ISO, epoch ms, missing (missing must NOT skip the 3-minute card wait)
n = datetime(2026, 11, 6, 15, 0, tzinfo=bot.TZ)
assert bot.created_at({'Date Created': '2026-11-06T14:58:00.000+07:00'}, n).minute == 58
assert bot.created_at({'Date Created': str(int(n.timestamp() * 1000))}, n) == n
assert bot.created_at({}, n) == n
print('ok created_at')

# handle_new: a failure right after claiming (e.g. the ack send) must end in 'Lỗi bot', never stuck in 'Đang xử lý'
writes = []
bot.find_card = lambda code: 'om_root'
bot.update_ticket = lambda rid, f: writes.append(f)
bot.reply = lambda root, md, key=None: (_ for _ in ()).throw(RuntimeError('lark retryable')) if key.endswith('-ack') else None
bot.handle_new({'record_id': 'r', 'Code': 'D1-1'}, n)
assert writes[-1]['Bot Status'] == ['Lỗi bot'], writes
print('ok fail path')

# checklist is shown even when the model itself picked '?'
r = bot.compose_reply({**weak, 'type': '?'}, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'Đã kiểm: Code BE ✓' in r, r
print('ok gate')

# High ticket is routed to on-call even while waiting for the reporter; non-High waits
hi = {**weak, 'type': '?', 'missing_info': ['mã đơn'], 'priority_suggested': 'High'}
assert 'Chuyển: Dev C' in bot.compose_reply(hi, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'Chuyển' not in bot.compose_reply({**hi, 'priority_suggested': 'Low'}, {'Priority': ['Low']}, 'Dev C', 'BA D')
print('ok high routing')

# lark-cli errors arrive on stderr (429 rate limit) and must still be parsed so the retry kicks in
e = bot.parse_cli('', 'warn: x\n{"ok": false, "error": {"subtype": "rate_limit", "retryable": true}}')
assert e['error']['retryable'] is True
assert bot.parse_cli('{"ok": true, "data": {"a": 1}}', '')['data']['a'] == 1
print('ok parse_cli')
