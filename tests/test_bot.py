#!/usr/bin/env python3
"""Offline self-check for bot.py pure logic (no Lark / Claude calls). Run: python3 tests/test_bot.py"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
rca_ok = {'code_location': {'file': 'x.java', 'line_start': 1, 'line_end': 1, 'symbol': 's', 'snippet': '', 'verified': True},
          'decisive_signal': {'observed': True, 'verified': True, 'what': 'NPE', 'quote': 'NullPointerException', 'source': 'nr', 'at': 't'},
          'mechanism': 'uuid null', 'causal_chain': [{'link': 'defect', 'claim': 'c', 'artifact': 'a'}],
          'alternatives': [{'claim': 'dữ liệu', 'status': 'refuted', 'artifact': 'sql'}]}
out = {'type': 'C', 'confidence': 0.8, 'evidence': ok3, 'rca': rca_ok, 'priority_suggested': 'Medium', 'priority_reason': '1 đơn',
       'missing_info': [], 'reply_markdown': 'Lỗi do X.', 'sources': ['nr'], 'summary': 's', '_log': 'l'}
r = bot.compose_reply(out, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'High → Medium' in r and '**Giao cho:** Dev C' in r and '<at' not in r and 'C - Bug' in r, r
assert 'Giao cho' not in bot.compose_reply({**out, 'missing_info': ['mã đơn']}, {'Priority': ['High']}, 'Dev C', 'BA D')
assert bot.bot_fields(out, 'Đã chuyển người')['AI Type'] == ['C - Bug']

# 100% gate (dev rule): one step not confirmed -> no conclusion, routed to on-call, Base shows '?'
weak = {**out, 'type': 'B', 'evidence': {**ok3, 'newrelic': {'status': 'not_found', 'detail': 'log hết hạn'}}}
assert bot.verdict(weak) == '?'
r = bot.compose_reply(weak, {'Priority': ['High']}, 'Dev C', 'BA D')
assert '**Loại:** `? - Không chắc`' in r and '**New Relic:** `✗ không thấy`' in r and '**Giao cho:** Dev C' in r, r
assert bot.bot_fields(weak, 'Đã chuyển người')['AI Type'] == ['? - Không chắc']
assert bot.verdict({**weak, 'type': 'E'}) == 'E'  # feature requests go to PO/BA, no investigation gate

print('ok')

# permission-rule paths: the old "/C:" + backslash form was denied by the CLI on Windows; //c/... works (verified)
if bot.os.name == 'nt':
    assert bot.perm_path(r'C:\Users\x\knowledge') == '//c/Users/x/knowledge', bot.perm_path(r'C:\Users\x\knowledge')
else:
    assert bot.perm_path('/Users/x/knowledge') == '//Users/x/knowledge'
assert all(t.endswith('/**)') for t in bot.READ_TOOLS if t.startswith(('Read(', 'Grep(', 'Glob(')))
assert 'mcp__repogit' in bot.READ_TOOLS and 'mcp__dbhub' in bot.READ_TOOLS
# the model only ever gets read tools: no Bash/Write/Edit anywhere in the allowlist
assert not any(t.split('(')[0] in ('Bash', 'Write', 'Edit', 'NotebookEdit') for t in bot.READ_TOOLS)

# rules + playbooks: every {PLACEHOLDER} is resolved (the model reads playbooks as plain files), product map is rendered
import re as _re
_rules = bot.rules()
assert not _re.findall(r'\{[A-Z_]+\}', _rules), _re.findall(r'\{[A-Z_]+\}', _rules)
for _f in bot.PLAYBOOKS_DIR.glob('*.md'):
    assert not _re.findall(r'\{[A-Z_]+\}', _f.read_text(encoding='utf-8')), _f.name
assert {f.name for f in bot.PLAYBOOKS_DIR.glob('*.md')} >= {'errors.md', 'fe-mobile.md', 'regression.md'}
assert '- Biz App: lớp mobile; repo `biz-mobile`' in _rules and '- Mall Web: lớp fe; repo `web-mall`' in _rules
assert 'chưa xác nhận' in bot.product_map_text() and bot.REPO_LAYER['biz-mobile'] == 'mobile' and bot.REPO_LAYER['mono-backend'] == 'be'
assert str(bot.ANCHORS_DIR) in _rules and 'data' in str(bot.PLAYBOOKS_DIR)
# every repo the product map points to is a configured clone with a client layer (a typo here would silently blind the bot)
for _name, _v in bot.CFG['products']['map'].items():
    for _r in _v['repos']:
        assert bot.REPO_LAYER.get(_r) == _v['layer'], (_name, _r)

# run_claude: nothing long on the command line (Windows stops at ~32k chars); prompt via stdin, rules + MCP config via files
import json as _json, tempfile as _tf
from pathlib import Path as _P
_seen = {}
_real_run, _real_logs, _real_ledger = bot.subprocess.run, bot.LOGS, bot.USAGE['ledger']
def _fake_run(cmd, **kw):
    _seen['cmd'], _seen['kw'] = list(cmd), kw
    _seen['rules'] = _P(cmd[cmd.index('--append-system-prompt-file') + 1]).read_text(encoding='utf-8')
    _seen['mcp'] = _json.loads(_P(cmd[cmd.index('--mcp-config') + 1]).read_text(encoding='utf-8'))
    class _R: returncode = 0
    return _R()
bot.subprocess.run, bot.LOGS, bot.USAGE['ledger'] = _fake_run, _P(_tf.mkdtemp(prefix='dcs-logs-')), False
try:
    _long = 'x' * 100000
    _log = bot.run_claude(_long, 'SYSTEM ' + 'r' * 50000, 'agent/schema.json', 'unit')
finally:
    bot.subprocess.run, bot.LOGS, bot.USAGE['ledger'] = _real_run, _real_logs, _real_ledger
_cmd = _seen['cmd']
assert _long not in ' '.join(_cmd) and _seen['kw']['input'] == _long, 'prompt must go through stdin'
assert sum(len(a) + 1 for a in _cmd) < 30000, sum(len(a) + 1 for a in _cmd)          # real runs must stay under the Windows limit
assert _seen['rules'].startswith('SYSTEM ') and 'mcpServers' in _seen['mcp'] and 'repogit' in _seen['mcp']['mcpServers']
assert '--append-system-prompt' not in _cmd and _cmd.index('--allowedTools') > _cmd.index('--json-schema')   # variadic flag stays last
assert not list(bot.RUN_DIR.glob('unit-*')), 'temp rules/mcp files must be removed after the run'
_schema = _json.loads(_cmd[_cmd.index('--json-schema') + 1])
assert 'rca' in _schema['properties'] and ' ' not in _cmd[_cmd.index('--json-schema') + 1][:20]
# every backend repo the handbook lists is configured with its own default branch; gamification is the Next.js app, not a service
for _n, _layer in (('mono-backend', 'be'), ('openim-server', 'be'), ('assistant-service', 'be'), ('recommender-service', 'be'),
                   ('droppii-odoo', 'odoo'), ('gamification', 'fe')):
    assert bot.REPO_LAYER.get(_n) == _layer, _n
assert {k['name']: k['branch'] for k in bot.KNOWLEDGE_CFG}['recommender-service'] == 'main'
assert {k['name']: k['branch'] for k in bot.KNOWLEDGE_CFG}['openim-server'] == 'dev'

# MCP trimming: every server's tool definitions are re-read each turn (measured: Sentry ~86k tokens), so a run only gets the servers
# its Product needs and none of the tools the bot never allows
assert set(_json.loads(bot.mcp_config(['newrelic']))['mcpServers']) == {'newrelic'}
assert {'sentry', 'bugsnag', 'newrelic', 'dbhub', 'repogit'} <= set(_json.loads(bot.mcp_config())['mcpServers'])
_ps = bot.product_servers
assert 'bugsnag' in _ps('Biz App') and 'sentry' not in _ps('Biz App') and 'newrelic' in _ps('Biz App') and 'repogit' in _ps('Biz App')
assert 'bugsnag' in _ps('Mall App') and 'sentry' not in _ps('Mall App')
assert 'sentry' in _ps('Mall Web') and 'bugsnag' not in _ps('Mall Web')
assert 'sentry' not in _ps('Odoo') and 'bugsnag' not in _ps('Odoo') and 'dbhub' in _ps('Odoo')       # backend: no client tracker
assert _ps('biz app') == _ps('Biz App') and _ps(' BIZ APP ') == _ps('Biz App')                       # tolerant of case / spaces
assert 'sentry' in _ps('Some New Product') and 'bugsnag' in _ps(None) and 'sentry' in _ps('')       # unknown: do not blind the bot
assert len(_ps('Odoo')) == len(set(_ps('Odoo')))
_fake_tools = {'newrelic': ['mcp__newrelic__execute_nrql_query', 'mcp__newrelic__create_alert_condition'],
               'sentry': ['mcp__sentry__find_organizations', 'mcp__sentry__update_issue', 'mcp__sentry__create_project'],
               'dbhub': ['mcp__dbhub__execute_sql_stg_order'], 'repogit': ['mcp__repogit__git_log'],
               'bugsnag': ['mcp__bugsnag__bugsnag_get_error', 'mcp__bugsnag__bugsnag_update_error']}
_real_disc = bot.discover_mcp_tools
bot.discover_mcp_tools = lambda force=False: _fake_tools
try:
    _den = bot.denied_tools(['newrelic', 'sentry', 'dbhub', 'repogit'])
    assert sorted(_den) == ['mcp__newrelic__create_alert_condition', 'mcp__sentry__create_project', 'mcp__sentry__update_issue'], _den
    assert not [t for t in bot.denied_tools(['dbhub', 'repogit'])]            # whole-server allowances are never trimmed
    assert bot.denied_tools(['bugsnag']) == ['mcp__bugsnag__bugsnag_update_error']
    assert bot.denied_tools(['sentry'])[0].startswith('mcp__sentry__') and not any(t in bot.READ_TOOLS for t in _den)
    bot.discover_mcp_tools = lambda force=False: {}                            # tool lists unknown: nothing trimmed, nothing breaks
    assert bot.denied_tools(['sentry']) == []
    def _boom(force=False): raise RuntimeError('cli missing')
    bot.discover_mcp_tools = _boom
    assert bot.denied_tools(['sentry']) == []
    bot.discover_mcp_tools = lambda force=False: _fake_tools
    # the command a real run gets: only the Product's servers, the unwanted tools denied, --allowedTools still last
    _seen2 = {}
    def _fake_run2(cmd, **kw):
        _seen2['cmd'] = list(cmd)
        _seen2['mcp'] = _json.loads(_P(cmd[cmd.index('--mcp-config') + 1]).read_text(encoding='utf-8'))
        class _R: returncode = 0
        return _R()
    bot.subprocess.run, bot.LOGS, bot.USAGE['ledger'] = _fake_run2, _P(_tf.mkdtemp(prefix='dcs-logs-')), False
    try:
        bot.run_claude('p', 'SYS', 'agent/schema.json', 'trim', servers=['newrelic', 'sentry', 'dbhub'])
    finally:
        bot.subprocess.run, bot.LOGS, bot.USAGE['ledger'] = _real_run, _real_logs, _real_ledger
    _c = _seen2['cmd']
    assert set(_seen2['mcp']['mcpServers']) == {'newrelic', 'sentry', 'dbhub'}
    _di = _c.index('--disallowedTools')
    assert 'mcp__sentry__update_issue' in _c[_di + 1:_c.index('--allowedTools')] and _di < _c.index('--allowedTools')
    assert _c[_c.index('--allowedTools') + 1:] == bot.READ_TOOLS                                      # allowlist untouched and last
    assert sum(len(a) + 1 for a in _c) < 30000
finally:
    bot.discover_mcp_tools = _real_disc

# discovery: read the tool names from the CLI's init event, then kill the process before any model call; cached afterwards
class _FakeProc:
    def __init__(self, *a, **k):
        self.killed, self.written = False, ''
        init = {'type': 'system', 'subtype': 'init', 'tools': ['Read', 'mcp__sentry__a', 'mcp__sentry__b', 'mcp__newrelic__c']}
        self.stdin = type('S', (), {'write': lambda s_, x: None, 'close': lambda s_: None})()
        self.stdout = iter(['not json\n', _json.dumps({'type': 'assistant'}) + '\n', _json.dumps(init) + '\n', _json.dumps({'type': 'result'}) + '\n'])
    def kill(self): self.killed = True
_procs = []
_real_popen, _real_root = bot.subprocess.Popen, bot.ROOT
_tmp_root = _P(_tf.mkdtemp(prefix='dcs-root-'))
(_tmp_root / 'agent').mkdir()
bot.subprocess.Popen = lambda *a, **k: (_procs.append(_FakeProc()) or _procs[-1])
bot.ROOT = _tmp_root
try:
    _found = bot.discover_mcp_tools(force=True)
    assert _found == {'sentry': ['mcp__sentry__a', 'mcp__sentry__b'], 'newrelic': ['mcp__newrelic__c']} and _procs[0].killed, _found
    assert (_tmp_root / 'data' / 'mcp_tools.json').exists()
    _procs.clear()
    assert bot.discover_mcp_tools() == _found and not _procs                  # second call: from cache, the CLI is not started again
finally:
    bot.subprocess.Popen, bot.ROOT = _real_popen, _real_root

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
assert '**Code BE:** `✓`' in r and '**ĐÃ KIỂM TRA:**' in r, r
print('ok gate')

# High ticket is routed to on-call even while waiting for the reporter; non-High waits
hi = {**weak, 'type': '?', 'missing_info': ['mã đơn'], 'priority_suggested': 'High'}
assert '**Giao cho:** Dev C' in bot.compose_reply(hi, {'Priority': ['High']}, 'Dev C', 'BA D')
assert 'Giao cho' not in bot.compose_reply({**hi, 'priority_suggested': 'Low'}, {'Priority': ['Low']}, 'Dev C', 'BA D')
print('ok high routing')

# lark-cli errors arrive on stderr (429 rate limit) and must still be parsed so the retry kicks in
e = bot.parse_cli('', 'warn: x\n{"ok": false, "error": {"subtype": "rate_limit", "retryable": true}}')
assert e['error']['retryable'] is True
assert bot.parse_cli('{"ok": true, "data": {"a": 1}}', '')['data']['a'] == 1
print('ok parse_cli')

# bearer tokens / JWTs from Bugsnag request headers are masked
m = bot.sanitize('Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.abc_DEF-123 and eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.sig')
assert 'eyJ' not in m and 'Bearer ***' in m, m
# error-tracker filters come from config env, not from our environment name (Biz staging = Bugsnag 'development')
assert bot.render('{BUGSNAG_STAGE}') == bot.ENV.get('bugsnag_release_stage', bot.ENV['name'])
print('ok jwt mask + tracker env')
