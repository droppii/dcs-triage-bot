#!/usr/bin/env python3
"""bot.py in PRODUCTION + SHADOW mode, end to end with a fake Lark and a fake brain (no network, no Claude, no DB).
Checks what is read, what is written and where, what is masked, withheld, purged and refused.
Run: python3 tests/test_prod_mode.py"""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')   # standalone runs on a cp1252 console
sys.stderr.reconfigure(encoding='utf-8', errors='replace')
ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())
(tmp / 'allow.json').write_text(json.dumps(
    {'sources': {'prod_order': {'database': 'vn_prod_order', 'tables': {'orders': ['id', 'code']}}}}), encoding='utf-8')
STATE = tmp / 'state.json'
cfg = {'extends': str(ROOT / 'config.json'),
       'env': {'name': 'production', 'label': '[PROD-SHADOW]', 'nr_prefix': 'vn-prod-', 'db_prefix': 'prod_',
               'bugsnag_release_stage': 'production', 'sentry_environment': 'production'},
       'ticket_source': {'mode': 'shadow', 'base_token': 'REAL_BASE', 'tickets_table': 'tblREAL', 'max_new_per_tick': 2,
                         'state_file': str(STATE)},
       'redact_logs': None,                                     # unset: production must still mask logs
       'log_retention_days': 14, 'output_guard': {'max_masked': 3},
       'db_guard': {'enabled': True, 'allowlist': str(tmp / 'allow.json'), 'upstream': 'tools/dbhub-prod.py'},
       'approvals': {k: 'Test, 2026-11-20' for k in ('data_scope', 'log_policy', 'model_provider', 'code_version')}}
(tmp / 'prod.json').write_text(json.dumps(cfg), encoding='utf-8')
os.environ['DCS_CONFIG'] = str(tmp / 'prod.json')
sys.path.insert(0, str(ROOT))
import bot
import guard
import usage

assert bot.IS_PROD and bot.SHADOW and bot.REDACT_LOGS is True, 'production forces log masking even when the config forgets it'
assert guard.preflight(bot.CFG, ROOT) == [], guard.preflight(bot.CFG, ROOT)
bot.LOGS = tmp / 'logs'
bot.RUN_DIR = tmp / 'run'
bot.oncall = lambda product, now=None: ('Dev C', 'BA D')

# ---- fake Lark: the real DCS Base is read-only, the sandbox chat is the only sink ----
FIELDS = ['Code', 'Title', 'Description', 'Priority', 'Product', 'Feature', 'Status', 'Date Created', 'ReportedBy']


def row(code, created, desc='không vào được giỏ hàng'):
    return [code, f'Ticket {code}', desc, ['High'], ['Mall App'], ['Cart'], ['Open'], created, 'x']


TICKETS = {'fields': FIELDS, 'record_id_list': ['rec_old', 'rec1', 'rec2', 'rec3', 'rec_nodate'],
           'data': [row('D-OLD', '2026-11-13T23:59:00+07:00'), row('D-1', '2026-11-14T08:00:00+07:00'),
                    row('D-2', '2026-11-14T09:00:00+07:00'), row('D-3', '2026-11-14T10:00:00+07:00'),
                    row('D-NODATE', None)]}
calls = []


def fake_lark(*args, retries=3):
    calls.append(args)
    a = list(args)
    if a[:2] == ['base', '+record-list']:
        assert a[a.index('--base-token') + 1] == 'REAL_BASE', f'read from an unexpected Base: {a}'
        assert 'Bot Status' not in a and 'Bot Thread' not in a, 'the real Base has no bot columns'
        return TICKETS
    if a[:2] == ['im', '+messages-send']:
        return {'message_id': f"om_root{sum(1 for c in calls if c[:2] == ('im', '+messages-send'))}"}
    if a[:2] == ['im', '+messages-reply']:
        return {'message_id': 'om_reply'}
    if a[:2] == ['im', '+threads-messages-list']:
        return {'items': []}
    raise AssertionError(f'unexpected lark call: {a}')


bot.lark = fake_lark

# ---- fake brain ----
ok3 = {k: {'status': 'confirmed', 'detail': 'x'} for k in ('source_be', 'db', 'newrelic')}
seen = []
reply_text = ['Giỏ hàng lỗi do cấu hình.']


def fake_brain(ticket, thread_text='', label='dry', attachments=(), prior=()):
    seen.append(label)
    return {'type': 'B', 'confidence': 0.9, 'evidence': ok3, 'priority_suggested': 'High', 'priority_reason': 'x', 'missing_info': [],
            'reply_markdown': reply_text[0], 'sources': ['db'], 'summary': 's', '_log': f'{label}.jsonl', '_cost': 0,
            '_usage': usage.empty()}


bot.run_brain = fake_brain


def posted():
    return [c for c in calls if c[:2] == ('im', '+messages-reply')]


def md(call):
    return call[list(call).index('--markdown') + 1]


# ---- first ever run with no state file: starts "from now", so history is never triaged ----
assert not STATE.exists()
assert bot.shadow_tickets() == [] and STATE.exists()
assert datetime.fromisoformat(json.loads(STATE.read_text(encoding='utf-8'))['since']) > datetime(2026, 11, 14, tzinfo=bot.TZ)

# ---- a start date typed without an offset is Vietnam time; a corrupt state file starts from now, loudly ----
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00', 'tickets': {}}), encoding='utf-8')
assert [r['Code'] for r in bot.shadow_tickets()] == ['D-1', 'D-2', 'D-3']
STATE.write_text('{ not json', encoding='utf-8')
assert bot.shadow_tickets() == [], 'a corrupt state must not re-triage history'
assert json.loads(STATE.read_text(encoding='utf-8'))['tickets'] == {}

# ---- from here: a known start time ----
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00+07:00', 'tickets': {}}), encoding='utf-8')
rows = bot.shadow_tickets()
assert [r['Code'] for r in rows] == ['D-1', 'D-2', 'D-3'], [r['Code'] for r in rows]   # D-OLD predates the run, D-NODATE unreadable

bot.tick()
assert seen == ['D-1', 'D-2'], f'max_new_per_tick=2 must cap the first tick: {seen}'
bot.tick()
assert seen == ['D-1', 'D-2', 'D-3'], f'the third ticket on the next tick, nothing twice: {seen}'
bot.tick()
assert len(seen) == 3, 'finished tickets are not triaged again'

assert not [c for c in calls if 'record-batch-update' in str(c)], 'shadow mode must never write to a Base'
sends = [c for c in calls if c[:2] == ('im', '+messages-send')]
assert len(sends) == 3 and all('SANDBOX' not in str(c) and bot.SANDBOX_CHAT in c for c in sends), 'results only go to the sandbox chat'
assert all('Chế độ bóng' in md(c) and '[PROD-SHADOW]' in md(c) for c in sends)
st = json.loads(STATE.read_text(encoding='utf-8'))
assert {v['Bot Status'][0] for v in st['tickets'].values()} == {'Đã trả lời'}
assert all(v['Bot Thread'].startswith('om_root') for v in st['tickets'].values())
assert len(posted()) == 6, 'one acknowledgement and one result per ticket'

# ---- a reply full of personal data is withheld, and nothing of it reaches chat or state ----
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00+07:00', 'tickets': {}}), encoding='utf-8')
del seen[:], calls[:]
TICKETS['data'] = [row('D-LEAK', '2026-11-14T11:00:00+07:00')]
TICKETS['record_id_list'] = ['rec_leak']
phones = ['0912345678', '0987654321', '0356789012', '0798765432', '0834567890']
reply_text[0] = 'Danh sách khách: ' + ', '.join(phones) + ' và a@b.vn'
bot.tick()
final = md(posted()[-1])
assert 'không đăng kết quả' in final and 'Dev C' in final, final
assert not any(p in json.dumps(calls, ensure_ascii=False) for p in phones), 'no phone number may leave the bot'
rec = json.loads(STATE.read_text(encoding='utf-8'))['tickets']['rec_leak']
assert rec['Bot Status'] == ['Đã chuyển người'] and rec['AI Type'] == [bot.TYPE_LABEL['?']] and 'a@b.vn' not in rec['AI Note'], rec
assert 'Danh sách khách' not in rec['AI Note']

# a normal amount of masking is still posted (the limit is a tripwire, not a censor)
del seen[:], calls[:]
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00+07:00', 'tickets': {}}), encoding='utf-8')
reply_text[0] = 'Khách 0912345678 báo lỗi.'
bot.tick()
final = md(posted()[-1])
assert '***SĐT***' in final and '0912345678' not in final and 'không đăng kết quả' not in final, final

# ---- a masked detail inside the reply still counts toward the limit (compose_reply masks pieces before the final pass) ----
del seen[:], calls[:]
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00+07:00', 'tickets': {}}), encoding='utf-8')
bot.run_brain = lambda *a, **k: {**fake_brain(*a, **k), 'reply_markdown': 'ok',
                                 'evidence': {k2: {'status': 'confirmed', 'detail': ' '.join(phones[:3]) + ' x@y.vn'} for k2 in ok3}}
bot.tick()
assert 'không đăng kết quả' in md(posted()[-1]), 'masks made while composing must be counted'
bot.run_brain = fake_brain

# ---- everything written to the Base columns is masked, never the thread id ----
bot.update_ticket('recX', {'AI Note': 'gọi 0912345678, token=abc12345', 'Bot Thread': 'om_keep_1'})
rec = json.loads(STATE.read_text(encoding='utf-8'))['tickets']['recX']
assert '0912345678' not in rec['AI Note'] and 'abc12345' not in rec['AI Note'] and rec['Bot Thread'] == 'om_keep_1', rec

# ---- the write guard still pins writes to the sandbox ----
for args in (['base', '+record-batch-update', '--base-token', 'REAL_BASE'], ['im', '+messages-send', '--chat-id', 'oc_real_dcs_group']):
    try:
        bot.lark_write(*args)
        raise AssertionError(f'write not blocked: {args}')
    except PermissionError:
        pass

# ---- database: production always goes through the guard proxy, never straight to DBHub ----
args = json.loads(bot.mcp_config())['mcpServers']['dbhub']['args']
assert any(a.endswith('dbhub_guard_mcp.py') for a in args) and '--allowlist' in args and any(a.endswith('dbhub-prod.py') for a in args), args
assert not any(a.endswith('dbhub-stg.py') for a in args)

# ---- files with credentials are denied to Read and Grep, and the command line stays inside the Windows limit ----
deny = bot.secret_deny()
assert any(d.startswith('Read(') and d.endswith('/**/.env)') for d in deny) and any(d.startswith('Grep(') and '*.pem' in d for d in deny), deny[:3]
cmd = bot.claude_cmd('x', 'sonnet', ['newrelic', 'dbhub', 'repogit', 'sentry', 'bugsnag']) + ['--disallowedTools', *deny]
size = len(subprocess.list2cmdline(cmd))
assert size < 30000, f'command line {size} chars: too close to the 32,767 Windows limit'
print(f'  claude command line: {size} chars with {len(deny)} secret deny rules')

# ---- a crashed or timed-out run leaves a masked transcript, not a raw one ----
real_run = subprocess.run


def timeout_run(cmd, input=None, stdout=None, **kw):
    stdout.write(json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': 'khách 0912345678'}]}}) + '\n')
    stdout.flush()
    raise subprocess.TimeoutExpired(cmd, 1)


subprocess.run = timeout_run
try:
    bot.run_claude('p', 'sys', 'agent/schema.json', 'crash')
    raise AssertionError('timeout must propagate')
except subprocess.TimeoutExpired:
    pass
finally:
    subprocess.run = real_run
log = next(bot.LOGS.glob('crash-*.jsonl'))
assert '0912345678' not in log.read_text(encoding='utf-8') and '***SĐT***' in log.read_text(encoding='utf-8')

# ---- retention: old transcripts go, the usage ledger and db audit stay ----
bot.LOG_RETENTION_DAYS = 14
for name, days in (('D-9-20260101-000000.jsonl', 30), ('usage.jsonl', 90), ('db-audit-20260101.jsonl', 30),
                   ('db-audit-20261113.jsonl', 1), ('D-8-20261113-000000.jsonl', 1)):
    f = bot.LOGS / name
    f.write_text('{}')
    os.utime(f, (time.time() - days * 86400,) * 2)
bot._last_purge[0] = 0
bot.housekeeping()
left = sorted(p.name for p in bot.LOGS.iterdir() if p.name != log.name)
assert left == ['D-8-20261113-000000.jsonl', 'db-audit-20261113.jsonl', 'usage.jsonl'], left   # old audit days go too

# ---- a date without an offset (Base can return '2026-11-16 10:00:00') is Vietnam time, and sorts with the aware ones ----
TICKETS['data'] = [row('D-NAIVE', '2026-11-14 10:00:00'), row('D-AWARE', '2026-11-14T09:00:00+07:00')]
TICKETS['record_id_list'] = ['rec_n', 'rec_a']
STATE.write_text(json.dumps({'since': '2026-11-14T00:00:00+07:00', 'tickets': {}}), encoding='utf-8')
assert [r['Code'] for r in bot.shadow_tickets()] == ['D-AWARE', 'D-NAIVE']

# ---- preflight refuses to start a production bot that lacks its approvals ----
real_cfg = bot.CFG
bot.CFG = {**real_cfg, 'approvals': {}}
sys.argv = ['bot.py', '--once']
try:
    bot.main()
    raise AssertionError('main() must stop')
except SystemExit as e:
    assert 'preflight failed' in str(e) and 'approvals.data_scope' in str(e), e
finally:
    bot.CFG = real_cfg

print('prod mode tests passed')
