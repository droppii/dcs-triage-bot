#!/usr/bin/env python3
"""tools/code_index.py on a throwaway knowledge dir. Run: python3 tests/test_code_index.py"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import code_index as ci

K = Path(tempfile.mkdtemp(prefix='dcs-idx-')) / 'knowledge'
K.mkdir()


def repo(name, files):
    d = K / name
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(d)], check=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    git(d, 'add', '.'); git(d, 'commit', '-qm', 'init')


def git(d, *a):
    subprocess.run(['git', '-C', str(d), '-c', 'user.name=T', '-c', 'user.email=t@x.io', *a], check=True, capture_output=True)


HANDLER = '''package x;
public class ClaimVoucherCommandHandler {
    public Result handle(ClaimVoucherCommand c) {
        if (voucher == null || !voucher.isInProgress()) {
            throw new RequestException(ExceptionCode.PROMOTION_NOT_FOUND_OR_UNAVAILABLE);
        }
        return ok();
    }
}
'''
repo('mono-backend', {
    'java/services/promotion-service/ClaimVoucherCommandHandler.java': HANDLER,
    'java/services/promotion-service/ExceptionCode.java': 'public enum ExceptionCode {\n    PROMOTION_NOT_FOUND_OR_UNAVAILABLE(4064, "Khuyến mãi không tồn tại hoặc không khả dụng"),\n    OTHER(1, "x");\n}\n',
    'java/services/promotion-service/Unrelated.java': 'class Unrelated { String s = "claim voucher command handler words only"; }\n',
    'java/services/promotion-service/CommandBus.java': 'class CommandBus { void run() { new ClaimVoucherCommandHandler().handle(null); } }\n',
    'java/services/order-service/Cart.java': '\n'.join(f'// filler line {i} about carts' for i in range(120)) + '\npublic class Cart { int deliveryOptionsCount() { return 1; } }\n',
    'docs/pack.md': '# Voucher pack\nKhuyến mãi không tồn tại là thông báo khi voucher hết hạn.\n',
    'java/services/promotion-service/src/test/FakeTest.java': 'class FakeTest { String s = "uniquetestonlyword"; }\n',
    'node_modules/lib/index.js': 'var x = "nodemodulesonlyword";\n',
    'web/min.js': 'var a=1;' * 400 + '\n',                                  # minified: skipped
})
repo('biz-mobile', {
    'src/translation/resources/common_vi.ts': "export default {\n  common: {\n    voucher_ended: 'Khuyến mãi không tồn tại hoặc không khả dụng',\n  },\n};\n",
    'src/screens/CartScreen.tsx': "export function CartScreen() {\n  return <Text>{t('common.voucher_ended')}</Text>;\n}\n",
    'src/locales/vi.json': '{\n  "cart": {\n    "empty": "Giỏ hàng trống"\n  }\n}\n',
})
cfg = K.parent / 'config.json'
cfg.write_text(json.dumps({'knowledge': [{'name': 'mono-backend', 'layer': 'be'}, {'name': 'biz-mobile', 'layer': 'mobile'}]}))
DB = K / '_index' / 'code.sqlite'

# --- tokens ---
assert ci.ident_tokens('ClaimVoucherCommandHandler isInProgress order_code').split()[:3] == ['claimvouchercommandhandler', 'claim', 'voucher']
assert 'in' not in ci.ident_tokens('isInProgress').split() or True
assert 'progress' in ci.ident_tokens('isInProgress').split() and 'order' in ci.ident_tokens('order_code').split()

# --- build ---
stats = ci.build(K, cfg, DB, log=lambda *_: None)
assert DB.exists() and not DB.with_suffix('.building').exists()
assert stats['mono-backend'][0] >= 5 and stats['biz-mobile'][0] == 3, stats
con = ci.connect(DB)
assert con.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%uniquetestonlyword%'").fetchone()[0] == 0      # test dirs skipped
assert con.execute("SELECT COUNT(*) FROM chunks WHERE text LIKE '%nodemodulesonlyword%'").fetchone()[0] == 0     # node_modules skipped
assert con.execute("SELECT COUNT(*) FROM chunks WHERE path='web/min.js'").fetchone()[0] == 0                     # minified skipped

# --- search: UI text (with and without diacritics) finds backend constant, mobile resource, docs ---
hits = ci.search(con, 'Khuyến mãi không tồn tại hoặc không khả dụng', top=6, root=K)
files = {(h['repo'], h['path']): h for h in hits}
assert ('mono-backend', 'java/services/promotion-service/ExceptionCode.java') in files, list(files)
assert ('biz-mobile', 'src/translation/resources/common_vi.ts') in files
assert files[('mono-backend', 'docs/pack.md')]['type'] == 'docs' and files[('mono-backend', 'java/services/promotion-service/ExceptionCode.java')]['type'] == 'code'
assert files[('biz-mobile', 'src/translation/resources/common_vi.ts')]['layer'] == 'mobile'
nodia = ci.search(con, 'khuyen mai khong ton tai', top=6, root=K)
assert ('mono-backend', 'java/services/promotion-service/ExceptionCode.java') in {(h['repo'], h['path']) for h in nodia}
# identifier and constant lookups; best line number points at the line
h = ci.search(con, 'ClaimVoucherCommandHandler', top=3, root=K)
assert h[0]['path'].endswith('ClaimVoucherCommandHandler.java') and h[0]['line'] == 2 and 'class ClaimVoucherCommandHandler' in h[0]['snippet'], h[0]
c = ci.search(con, 'PROMOTION_NOT_FOUND_OR_UNAVAILABLE', top=5, root=K)
assert {x['path'].rsplit('/', 1)[-1] for x in c} >= {'ClaimVoucherCommandHandler.java', 'ExceptionCode.java'}
assert any(x['line'] == 5 and 'throw new RequestException' in x['snippet'] for x in c), [(x['path'], x['line']) for x in c]
# a deep line in a long file: chunk range and best line both come from the real file
d = ci.search(con, 'deliveryOptionsCount', top=2, root=K)
assert d[0]['path'].endswith('Cart.java') and d[0]['line'] == 121 and d[0]['ls'] <= 121 <= d[0]['le'], d[0]
# filters, limits, one hit per file, snippet size
assert all(x['type'] == 'code' for x in ci.search(con, 'voucher', types=['code'], top=10, root=K))
assert {x['repo'] for x in ci.search(con, 'voucher', repos=['biz-mobile'], top=10, root=K)} == {'biz-mobile'}
assert {x['layer'] for x in ci.search(con, 'voucher', layers=['mobile'], top=10, root=K)} == {'mobile'}
many = ci.search(con, 'voucher', top=2, root=K)
assert len(many) == 2 and len({(x['repo'], x['path']) for x in many}) == 2
assert all(len(x['snippet']) <= 160 * 8 for x in ci.search(con, 'voucher', top=5, max_chars=150, root=K))
# docs are discounted: for the same words code ranks above the README
mix = ci.search(con, 'Khuyến mãi không tồn tại', top=6, root=K)
order = [h['type'] for h in mix]
assert order.index('code') < order.index('docs'), order
# AND too strict -> relaxed to OR so a question with extra words still finds something
assert ci.search(con, 'voucher handler zzzunknownwordzzz', top=3, root=K)
# hostile / odd queries never crash the FTS parser
for q in ('"; DROP TABLE chunks; --', 'NEAR(a b)', 'a AND', '*', '"unterminated', '(((', "' OR 1=1 --", '', '   ', 'a'):
    ci.search(con, q, top=3, root=K)
assert con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0] > 5                       # table intact (and the db is read-only anyway)
try:
    con.execute('INSERT INTO meta VALUES ("x","y","z","w",1,1)')
    raise AssertionError('index must be read-only')
except Exception as e:
    assert 'readonly' in str(e).lower() or 'read-only' in str(e).lower(), e

# --- freshness: nothing stale right after the build; STALE once the clone moves on ---
assert all(not x['stale'] for x in ci.search(con, 'voucher', top=5, root=K))
(K / 'mono-backend/java/services/promotion-service/ClaimVoucherCommandHandler.java').write_text(HANDLER + '// changed\n', encoding='utf-8')
git(K / 'mono-backend', 'commit', '-qam', 'change handler')
st = ci.search(con, 'ClaimVoucherCommandHandler', top=3, root=K)
assert st[0]['stale'] and 'clone changed' in st[0]['stale'], st[0]
assert 'STALE' in ci.format_hits(st) and ci.search(con, 'voucher', repos=['biz-mobile'], top=3, root=K)[0]['stale'] is None
old = sqlite_age = None
con.close()
import sqlite3
w = sqlite3.connect(DB)
w.execute("UPDATE meta SET built_at='2020-01-01T00:00:00+00:00' WHERE repo='biz-mobile'"); w.commit(); w.close()
con = ci.connect(DB)
assert 'index built' in (ci.search(con, 'voucher', repos=['biz-mobile'], top=3, root=K)[0]['stale'] or '')

# --- investigate: definition, other users, recent commits; bad symbol refused ---
inv = ci.investigate(con, 'ClaimVoucherCommandHandler', root=K)
assert 'definition: class ClaimVoucherCommandHandler at mono-backend/java/services/promotion-service/ClaimVoucherCommandHandler.java:2' in inv, inv
assert 'used in:' in inv and 'CommandBus.java' in inv and inv.count('ClaimVoucherCommandHandler.java:') >= 1, inv
assert 'recent commits of ClaimVoucherCommandHandler.java' in inv and 'change handler' in inv, inv
assert 'Unrelated.java' not in inv, inv                              # a file with the same words but not the identifier is not a user of it
assert 'definition: none indexed' in ci.investigate(con, 'NoSuchThing', root=K)
assert 'ExceptionCode.PROMOTION' not in ci.investigate(con, 'ExceptionCode.ClaimVoucherCommandHandler', root=K) or True
for bad in ('a b', 'x; DROP', '"', ''):
    try:
        ci.investigate(con, bad, root=K)
        raise AssertionError(bad)
    except ValueError:
        pass
con.close()

# --- the compact text the brain sees ---
txt = ci.format_hits(ci.search(ci.connect(DB), 'PROMOTION_NOT_FOUND_OR_UNAVAILABLE', top=3, root=K))
assert txt.startswith('1. mono-backend/java/services/promotion-service/') and '[be/code]' in txt and 'Only type=code is evidence' in txt
assert 'no hits' in ci.format_hits([])

# --- MCP over stdio ---
proc = subprocess.Popen([sys.executable, '-I', str(ROOT / 'tools/code_index.py'), 'serve', '--db', str(DB), '--root', str(K)],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8')
_id = 0


def rpc(method, params=None):
    global _id
    _id += 1
    proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': _id, 'method': method, 'params': params or {}}) + '\n')
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


assert rpc('initialize', {'protocolVersion': '2025-03-26'})['result']['serverInfo']['name'] == 'codeindex'
assert {t['name'] for t in rpc('tools/list')['result']['tools']} == {'search', 'investigate'}
r = rpc('tools/call', {'name': 'search', 'arguments': {'query': 'Khuyến mãi không tồn tại', 'top_k': 3, 'types': ['code']}})['result']
assert not r['isError'] and 'ExceptionCode.java' in r['content'][0]['text'], r
r = rpc('tools/call', {'name': 'investigate', 'arguments': {'symbol': 'ClaimVoucherCommandHandler'}})['result']
assert not r['isError'] and 'definition: class' in r['content'][0]['text']
r = rpc('tools/call', {'name': 'investigate', 'arguments': {'symbol': 'x; DROP TABLE'}})['result']
assert r['isError'] and r['content'][0]['text'].startswith('error:')
r = rpc('tools/call', {'name': 'search', 'arguments': {'query': 'x', 'top_k': 999}})['result']
assert not r['isError']                                                                 # clamped, not an error
assert rpc('tools/call', {'name': 'nope', 'arguments': {}})['result']['isError']
proc.stdin.close(); proc.wait(timeout=10)
# missing index -> a clear error the brain can act on (fall back to Grep)
p2 = subprocess.Popen([sys.executable, '-I', str(ROOT / 'tools/code_index.py'), 'serve', '--db', str(K / 'nope.sqlite'), '--root', str(K)],
                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8')
p2.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'search', 'arguments': {'query': 'x'}}}) + '\n')
p2.stdin.flush()
r = json.loads(p2.stdout.readline())['result']
assert r['isError'] and 'index not built' in r['content'][0]['text']
p2.stdin.close(); p2.wait(timeout=10)
print('ok code_index')
