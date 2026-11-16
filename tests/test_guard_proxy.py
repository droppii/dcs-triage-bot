#!/usr/bin/env python3
"""Runs tools/dbhub_guard_mcp.py for real (stdio) in front of a fake DBHub and checks what reaches the "database".
No network, no DBHub, no credentials. Run: python3 tests/test_guard_proxy.py"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp())

FAKE = '''
import json, sys
TOOLS = __TOOLS__
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
    elif m["method"] == "tools/list":
        r = {"tools": TOOLS}
    elif m["method"] == "tools/call":
        r = {"content": [{"type": "text", "text": "DB RAN: " + m["params"]["arguments"]["sql"]}]}
    else:
        r = {}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
'''
# a stand-in for DBHub: answers initialize / tools/list, and echoes the SQL it was asked to run
(tmp / 'fake_dbhub.py').write_text(FAKE.replace('__TOOLS__', json.dumps(
    [{'name': 'execute_sql_prod_order'}, {'name': 'execute_sql_prod_secret'}, {'name': 'search_objects'}])), encoding='utf-8')
# real DBHub 1.5.0 calls its tool plain `execute_sql` when it serves ONE source
(tmp / 'fake_dbhub_single.py').write_text(FAKE.replace('__TOOLS__', json.dumps([{'name': 'execute_sql'}, {'name': 'search_objects'}])),
                                          encoding='utf-8')
allow = tmp / 'allow.json'
allow.write_text(json.dumps({'sources': {'prod_order': {'database': 'vn_prod_order', 'tables': {'orders': ['id', 'code', 'status']}}}}),
                 encoding='utf-8')
audit = tmp / 'audit.jsonl'                    # the proxy writes audit-YYYYMMDD.jsonl next to this name


def audit_rows():
    return [json.loads(l) for f in sorted(tmp.glob('audit-????????.jsonl')) for l in f.read_text(encoding='utf-8').splitlines()]


def session(messages, upstream='fake_dbhub.py'):
    p = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/dbhub_guard_mcp.py'), '--allowlist', str(allow),
                        '--upstream', str(tmp / upstream), '--audit', str(audit)],
                       input='\n'.join(json.dumps(m) for m in messages) + '\n', capture_output=True, text=True, encoding='utf-8',
                       timeout=60)
    assert p.returncode == 0, p.stderr
    return {r['id']: r for r in map(json.loads, p.stdout.splitlines()) if 'id' in r}


def call(i, name, **args):
    return {'jsonrpc': '2.0', 'id': i, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}}


res = session([
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
    call(3, 'execute_sql_prod_order', sql="select id, code from orders where code = '2611060001'"),
    call(4, 'execute_sql_prod_order', sql='select phone from orders'),
    call(5, 'execute_sql_prod_order', sql='select 1; drop table orders'),
    call(6, 'execute_sql_prod_secret', sql='select 1'),                    # a source the upstream has but the allowlist does not
    call(7, 'search_objects', object_type='table'),                        # a tool nobody allowed
    call(8, 'allowed_schema', source='prod_order'),
    call(9, 'allowed_schema', source='nope'),
    call(10, 'execute_sql_prod_order', sql="select * from orders /* ' or 1=1 */"),
])

assert res[1]['result']['serverInfo']['name'] == 'fake', 'initialize must pass through'
names = [t['name'] for t in res[2]['result']['tools']]
assert names == ['execute_sql_prod_order', 'allowed_schema'], f'only allowlisted tools are visible: {names}'

ran = res[3]['result']['content'][0]['text']
assert ran.startswith('DB RAN: SELECT') and '"code"' in ran, ran
assert res[3]['result'].get('isError') is not True

for i in (4, 5, 6, 7):
    r = res[i]['result']
    assert r['isError'] is True and 'DB RAN' not in json.dumps(r), (i, r)
assert 'phone' in res[4]['result']['content'][0]['text']
assert 'orders(id, code, status)' in res[8]['result']['content'][0]['text']
assert res[9]['result']['isError'] is True

star = res[10]['result']['content'][0]['text']
assert star.startswith('DB RAN: SELECT') and '*' not in star and '1=1' not in star and '/*' not in star, star  # expanded + comment gone

rows = audit_rows()
assert [r['verdict'] for r in rows].count('allowed') == 2 and [r['verdict'] for r in rows].count('refused') == 4, rows
assert all('run' in r for r in rows if r['verdict'] == 'allowed')

# the audit keeps the SHAPE of each query: names and addresses a query filters on are never written down
before = len(audit_rows())
res = session([call(1, 'execute_sql_prod_order', sql="select id from orders where code = 'Nguyen Van A 12 Le Loi' and status = 'x'"),
               call(2, 'execute_sql_prod_order', sql="select phone from orders where code = 'Tran Thi B'"),
               call(3, 'search_objects', pattern='Le Van C')])
raw = ' '.join(f.read_text(encoding='utf-8') for f in tmp.glob('audit-????????.jsonl'))
assert 'Nguyen Van A' not in raw and 'Tran Thi B' not in raw and 'Le Van C' not in raw, raw
assert "code = '?'" in raw.replace('\\"', '"') or "'?'" in raw
assert len(audit_rows()) == before + 3

# malformed calls are refused, they do not kill the proxy; the next call is still answered
res = session([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'execute_sql_prod_order', 'arguments': 'oops'}},
               {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': 'not a dict'},
               {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'execute_sql_prod_order', 'arguments': {'sql': 123}}},
               {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'execute_sql_prod_order', 'arguments': {'sql': 'select id from orders'}}}])
for i in (1, 2, 3):
    assert res[i]['result']['isError'] is True, (i, res[i])
assert res[4]['result']['content'][0]['text'].startswith('DB RAN'), res[4]

# DBHub gone: the caller still gets an answer instead of a hang or a crash
(tmp / 'dead_dbhub.py').write_text('import sys\nsys.exit(0)\n', encoding='utf-8')
res = session([{'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, call(2, 'execute_sql_prod_order', sql='select id from orders'),
               call(3, 'execute_sql_prod_order', sql='select id from orders')], upstream='dead_dbhub.py')
assert all(res[i]['result']['isError'] is True and ('unavailable' in res[i]['result']['content'][0]['text'] or 'stopped' in res[i]['result']['content'][0]['text']) for i in (2, 3)), res

# one source: DBHub names the tool plain `execute_sql`; it is still checked and still limited to the allowlisted source
res = session([
    {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
    call(2, 'execute_sql', sql='select id from orders'),
    call(3, 'execute_sql', sql='select phone from orders'),
    call(4, 'execute_sql_prod_order', sql='select phone from orders'),   # the other spelling names an allowed source: same check
    call(5, 'search_objects', object_type='table'),
], upstream='fake_dbhub_single.py')
assert [t['name'] for t in res[1]['result']['tools']] == ['execute_sql', 'allowed_schema'], res[1]
assert res[2]['result']['content'][0]['text'].startswith('DB RAN: SELECT')
for i in (3, 4, 5):
    assert res[i]['result']['isError'] is True and 'DB RAN' not in json.dumps(res[i]), (i, res[i])

# fails closed: a broken allowlist means no server at all
bad = tmp / 'bad.json'
bad.write_text(json.dumps({'sources': {'a': {'database': 'd', 'tables': {'t': ['id', 'phone']}}}}), encoding='utf-8')
p = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/dbhub_guard_mcp.py'), '--allowlist', str(bad),
                    '--upstream', str(tmp / 'fake_dbhub.py'), '--audit', str(audit)], input='', capture_output=True, text=True,
                   encoding='utf-8', timeout=60)
assert p.returncode != 0 and 'refusing to start' in p.stderr and 'phone' in p.stderr, p.stderr

print('guard proxy tests passed')
