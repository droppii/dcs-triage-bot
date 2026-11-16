#!/usr/bin/env python3
"""Offline tests for guard.py (masking, leak limit, retention, production preflight) and tools/scan_secrets.py.
Run: python3 tests/test_guard.py"""
import copy, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import guard
import scan_secrets

# ---- masking ----
s = guard.sanitize('gọi 0912 345 678 hoặc 091.234.5678 hoặc +84 912 345 678 hoặc 0912345678')
assert not any(d in s for d in ('345 678', '234.5678', '912 345', '0912345678')), s
s = guard.sanitize('đơn 2611060001, vận đơn GHN123456789, mã 1234567890123')
assert '2611060001' in s and 'GHN123456789' in s, 'order and tracking numbers must survive'
assert '***' not in guard.sanitize('tổng 1.250.000đ phí 30.000đ')

assert '4111111111111111' not in guard.sanitize('thẻ 4111111111111111 hết hạn')          # valid Luhn + Visa prefix: masked
assert '5500005555555559' not in guard.sanitize('thẻ 5500005555555559')                    # Mastercard
assert '9704' not in guard.sanitize('thẻ Napas 9704 0000 0000 0018'), 'Napas prefix + valid Luhn: masked'
assert guard.sanitize('mã 7000000000000009') == 'mã 7000000000000009', 'passes Luhn but no card prefix: an id, kept'
assert guard.sanitize('ts 1763280000000') == 'ts 1763280000000', 'epoch milliseconds survive'
for code in ('JT1234567890123', 'VN0123456789012', 'GHTK123456789012', 'SPXVN012345678901'):
    assert guard.sanitize(f'vận đơn {code}') == f'vận đơn {code}', f'carrier tracking code masked: {code}'
assert '4111 1111 1111 1111' not in guard.sanitize('thẻ 4111 1111 1111 1111 hết hạn')
assert '4111111111111112' in guard.sanitize('mã giao dịch 4111111111111112')               # fails Luhn: an id, kept

s = guard.sanitize('STK: 0123456789012 Vietcombank; số tài khoản 9704 1234 5678; IBAN GB82WEST12345698765432')
assert '0123456789012' not in s and '9704 1234 5678' not in s and '98765432' not in s, s

for secret in ('postgres://app:hunter22@db.internal/orders', 'redis://u:p4ss@cache:6379/0', 'mongodb+srv://u:pw12345@c.mongodb.net/x',
               'jdbc:postgresql://db:5432/x?user=a&password=zzz999', 'AKIAABCDEFGHIJKLMNOP', 'ghp_' + 'a1' * 20,
               'xoxb-1234567890-abcdefghij', 'sk-' + 'Ab3' * 10, 'token=abcdef123456', 'Authorization: Bearer abc.def.ghi',
               '-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----'):
    out = guard.sanitize(f'log: {secret} end')
    core = secret.split('//')[-1].split(':')[-1].split('=')[-1].split('\n')[1] if 'PRIVATE' in secret else \
        (secret.split('@')[0].split(':')[-1] if '://' in secret and '@' in secret else secret.split('=')[-1])
    assert core not in out, f'{secret!r} leaked: {out!r}'

assert 'IBAN' in guard.sanitize('chuyển tới GB82WEST12345698765432 nhé'), 'a valid IBAN is masked'
assert guard.sanitize('chuyển tới GB82WEST12345698765433 nhé').count('IBAN') == 0, 'bad checksum: not an IBAN'

# masks are idempotent: finish() masks while composing and again on the whole reply, and counts both
for sample in ('Authorization: Bearer abc.def.ghi', 'password: hunter22', 'token=abcdef123456', 'postgres://a:b@h/db',
               'jdbc:postgresql://h/db?password=x', 'redis://u:p@h', 'a@b.vn', 'gọi 0912345678', 'STK 0123456789012',
               'thẻ 4111111111111111', 'GB82WEST12345698765432', 'AKIAABCDEFGHIJKLMNOP', 'eyJhbGciOiJIUzI1NiJ9.eyJhIjoxfQ.sig'):
    once, n1 = guard.sanitize_counted(sample)
    assert n1 >= 1 and guard.sanitize_counted(once) == (once, 0), (sample, once, guard.sanitize_counted(once))

assert guard.mask_literals("select id from t where name = 'Nguyễn Văn A' and x = 'it''s' and y = $$a b$$ and z = 5") == \
    "select id from t where name = '?' and x = '?' and y = '?' and z = 5"

assert guard.is_secret_path('svc/config/application-prod.yml') and guard.is_secret_path('svc/.env') and guard.is_secret_path('a/b/server.PEM')
assert not guard.is_secret_path('svc/src/Main.java') and guard.is_secret_path('svc/odd.txt', ['svc/odd.txt'])
assert ':(exclude,glob)**/.env' in guard.secret_pathspecs('svc') and ':(exclude,literal)odd.txt' in guard.secret_pathspecs('svc', ['svc/odd.txt'])

text, n = guard.sanitize_counted('a@b.vn c@d.vn 0912345678 token: x1y2z3w4')
assert n == 4 and '@b.vn' not in text, (n, text)
assert guard.sanitize_counted('nothing sensitive here') == ('nothing sensitive here', 0)
assert guard.sanitize(guard.sanitize('a@b.vn 0912345678')) == guard.sanitize('a@b.vn 0912345678')   # idempotent (reply() masks again)

# ---- leak limit ----
assert guard.leak_blocked(6, 5) and not guard.leak_blocked(5, 5) and not guard.leak_blocked(100, 0)

# ---- retention ----
d = Path(tempfile.mkdtemp())
for name, age_days in (('old-20260101-000000.jsonl', 40), ('new-20261110-000000.jsonl', 1), ('usage.jsonl', 99)):
    f = d / name
    f.write_text('x')
    os.utime(f, (time.time() - age_days * 86400,) * 2)
assert guard.purge_old(d, 30, '*-????????-??????.jsonl') == 1
assert sorted(p.name for p in d.iterdir()) == ['new-20261110-000000.jsonl', 'usage.jsonl'], 'usage ledger must survive'
assert guard.purge_old(d, 0) == 0 and guard.purge_old(d / 'missing', 30) == 0

# ---- secret file globs ----
assert '.env' in guard.SECRET_FILE_GLOBS and '*.pem' in guard.SECRET_FILE_GLOBS and 'application-prod*' in guard.SECRET_FILE_GLOBS

# ---- production preflight ----
root = Path(tempfile.mkdtemp())
(root / 'config').mkdir()
(root / 'config' / 'db_allowlist.json').write_text(json.dumps(
    {'sources': {'prod_order': {'database': 'vn_prod_order', 'tables': {'orders': ['id', 'code']}}}}), encoding='utf-8')
PROD = {'env': {'name': 'production', 'nr_prefix': 'vn-prod-', 'db_prefix': 'prod_', 'bugsnag_release_stage': 'production',
                'sentry_environment': 'production'},
        'log_retention_days': 14, 'db_guard': {'enabled': True, 'allowlist': 'config/db_allowlist.json'},
        'ticket_source': {'mode': 'shadow', 'base_token': 'bascReal123', 'tickets_table': 'tblReal123'},
        'approvals': {k: 'Name, 2026-11-20' for k in guard.APPROVALS}}
assert guard.preflight({'env': {'name': 'staging'}}, root) == [], 'staging is never blocked'
assert guard.preflight({}, root) == []
assert guard.preflight(PROD, root) == [], guard.preflight(PROD, root)


def breaks(mutate, *words):
    cfg = copy.deepcopy(PROD)
    mutate(cfg)
    problems = ' | '.join(guard.preflight(cfg, root))
    assert problems and all(w in problems for w in words), (words, problems)


breaks(lambda c: c.update(redact_logs=False), 'redact_logs')
breaks(lambda c: c.pop('log_retention_days'), 'log_retention_days')
breaks(lambda c: c.update(log_retention_days=365), 'log_retention_days')
breaks(lambda c: c['env'].update(sentry_environment='không có'), 'sentry_environment')
breaks(lambda c: c['env'].update(bugsnag_release_stage='development'), 'bugsnag_release_stage')
breaks(lambda c: c['env'].update(nr_prefix='vn-stg-'), 'staging')
breaks(lambda c: c['env'].update(db_prefix='stg_'), 'staging')
for k in guard.APPROVALS:
    breaks(lambda c, k=k: c['approvals'].update({k: ''}), f'approvals.{k}')
breaks(lambda c: c.pop('approvals'), 'approvals.data_scope', 'approvals.model_provider')
breaks(lambda c: c['db_guard'].update(enabled=False), 'db_guard')
breaks(lambda c: c.pop('db_guard'), 'db_guard')
breaks(lambda c: c['db_guard'].update(allowlist='config/missing.json'), 'unreadable')
breaks(lambda c: c['ticket_source'].update(mode='live'), 'ticket_source.mode')
(root / 'config' / 'bad.json').write_text(json.dumps(
    {'sources': {'a': {'database': 'vn_prod', 'tables': {'t': ['id', 'phone']}}}}), encoding='utf-8')
breaks(lambda c: c['db_guard'].update(allowlist='config/bad.json'), 'personal data')
(root / 'config' / 'stg.json').write_text(json.dumps(
    {'sources': {'a': {'database': 'vn_stg_order_service', 'tables': {'t': ['id']}}}}), encoding='utf-8')
breaks(lambda c: c['db_guard'].update(allowlist='config/stg.json'), 'staging')

# ---- scan_secrets: reports the file and kind, never the value ----
kn = Path(tempfile.mkdtemp())
(kn / 'svc').mkdir()
(kn / 'svc' / 'Config.java').write_text('String password = "Pr0dP@ssw0rd!";\n')
(kn / 'svc' / 'safe.yml').write_text('password: ${DB_PASSWORD}\napi_key: "your_api_key_here"\n')
(kn / 'svc' / 'deploy.sh').write_text('curl https://admin:s3cretpw@registry.internal/v2\n')
(kn / 'svc' / 'node_modules').mkdir()
(kn / 'svc' / 'node_modules' / 'x.js').write_text('password = "ShouldBeSkipped1"\n')
found = scan_secrets.scan(kn)
files = sorted({p for p, _ in found})
assert files == ['svc/Config.java', 'svc/deploy.sh'], found
assert all('Pr0dP@ssw0rd' not in k and 's3cretpw' not in k for _, k in found)

print('guard tests passed')

# ---- the committed examples stay valid (a broken example is found here, not during the switch to production) ----
import sqlguard
ex = sqlguard.load_allowlist(ROOT / 'config' / 'db_allowlist.example.json')
assert 'prod_order' in ex and all(not s['database'].lower().count('stg') for s in ex.values())
cfg = guard.load_config(ROOT / 'config.prod.example.json')
assert cfg['env']['name'] == 'production' and cfg['ticket_source']['mode'] == 'shadow' and cfg['knowledge'], 'extends config.json'
assert cfg['mcp_default'] and cfg['gate'], 'inherited from config.json'
problems = ' | '.join(guard.preflight(cfg, ROOT))
assert 'approvals.data_scope' in problems and 'approvals.code_version' in problems, 'the example must not start without sign-off'
assert 'redact_logs' not in problems and 'log_retention_days' not in problems and 'staging' not in problems, problems
assert 'REPLACE_WITH_DCS_TICKETS_TABLE_ID' in problems, 'an unfilled placeholder must stop a production start'
filled = copy.deepcopy(cfg)
filled['ticket_source']['tickets_table'] = 'tblReal123'
filled['approvals'] = {k: 'Name, 2026-11-20' for k in guard.APPROVALS}
assert 'placeholder' not in ' | '.join(guard.preflight(filled, ROOT)).replace('db_allowlist', ''), guard.preflight(filled, ROOT)
filled['ticket_source']['base_token'] = ''
assert 'ticket_source.base_token is empty' in ' | '.join(guard.preflight(filled, ROOT))
(root / 'config' / 'ex.json').write_text((ROOT / 'config' / 'db_allowlist.example.json').read_text(encoding='utf-8'), encoding='utf-8')
assert 'example placeholder' in ' | '.join(guard.preflight({**PROD, 'db_guard': {'enabled': True, 'allowlist': 'config/ex.json'}}, root))
assert guard.deep_merge({'a': {'b': 1, 'c': 2}, 'l': [1]}, {'a': {'b': 9}, 'l': [2]}) == {'a': {'b': 9, 'c': 2}, 'l': [2]}
print('guard examples ok')
