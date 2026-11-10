#!/usr/bin/env python3
"""Offline checks for rca.py / priorart.py and their use in bot.compose_reply. Run: python3 tests/test_rca.py"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bot, priorart, rca

# --- a fake knowledge dir with one repo and one Java file ---
K = Path(tempfile.mkdtemp(prefix='dcs-k-'))
(K / 'mono-backend/java/order').mkdir(parents=True)
JAVA = K / 'mono-backend/java/order/CartServiceImpl.java'
JAVA.write_text('\n'.join([f'// line {i}' for i in range(1, 40)]
                          + ['    String id = cart.getWarehouse().getUuid();   // line 40', '    return find(id);']), encoding='utf-8')
OUTSIDE = K.parent / 'dcs-outside-secret.txt'
OUTSIDE.write_text('x')

# --- resolve_path: only inside knowledge/ ---
assert rca.resolve_path('mono-backend/java/order/CartServiceImpl.java', K) == JAVA.resolve()
assert rca.resolve_path('knowledge/mono-backend/java/order/CartServiceImpl.java', K) == JAVA.resolve()
assert rca.resolve_path('java/order/CartServiceImpl.java', K) == JAVA.resolve()          # repo prefix optional
assert rca.resolve_path('../dcs-outside-secret.txt', K) is None                                       # no escaping knowledge/
assert rca.resolve_path(str(OUTSIDE), K) is None

# --- check_code_ref ---
good = rca.check_code_ref('mono-backend/java/order/CartServiceImpl.java', 40, 40, 'cart.getWarehouse().getUuid()', K)
assert good[0], good
assert not rca.check_code_ref('mono-backend/java/order/CartServiceImpl.java', 40, 40, 'somethingElse()', K)[0]
assert not rca.check_code_ref('mono-backend/java/order/CartServiceImpl.java', 500, 500, '', K)[0]
assert not rca.check_code_ref('mono-backend/java/order/Nope.java', 1, 1, '', K)[0]
assert rca.check_code_ref('mono-backend/java/order/CartServiceImpl.java', 40, 41, 'getUuid ... find(id)', K)[0]  # '...' splits

# --- transcript with two tool results ---
def ev(kind, **kw):
    return json.dumps(kw | {'type': kind})
NR = 'TransactionError: java.lang.NullPointerException: Cannot invoke "Warehouse.getUuid()" because "w" is null'
transcript = '\n'.join([
    ev('assistant', message={'content': [{'type': 'tool_use', 'name': 'mcp__newrelic__execute_nrql_query', 'input': {'q': 'x'}}]}),
    ev('user', message={'content': [{'type': 'tool_result', 'content': [{'type': 'text', 'text': NR}]}]}),
    ev('assistant', message={'content': [{'type': 'tool_use', 'name': 'Read', 'input': {'file_path': str(JAVA)}}]}),
    ev('result', is_error=False, structured_output={}, total_cost_usd=0.1),
])
tr = rca.parse_transcript(transcript)
assert len(tr['tool_uses']) == 2 and NR in tr['results'] and tr['final']['total_cost_usd'] == 0.1


def make(**over):
    out = {
        'type': 'C', 'missing_info': [], 'priority_suggested': 'High', 'priority_reason': '', 'reply_markdown': 'Lỗi.',
        'sources': [], 'summary': 's', '_log': 'l',
        'evidence': {'source_be': {'status': 'confirmed', 'detail': 'mono-backend/java/order/CartServiceImpl.java:40 getUuid'},
                     'db': {'status': 'not_checked', 'detail': ''},
                     'newrelic': {'status': 'confirmed', 'detail': 'NPE 15:30'}},
        'rca': {
            'category': 'code_defect', 'symptom_card': {}, 'mechanism': 'warehouse null nên getUuid() ném NPE',
            'code_location': {'file': 'mono-backend/java/order/CartServiceImpl.java', 'line_start': 40, 'line_end': 40,
                              'symbol': 'getDeliveryOptions', 'snippet': 'cart.getWarehouse().getUuid()'},
            'decisive_signal': {'observed': True, 'what': 'NPE', 'quote': 'Cannot invoke "Warehouse.getUuid()" because "w" is null',
                                'source': 'newrelic', 'at': '2026-11-06 15:30 +0700'},
            'causal_chain': [{'link': 'defect', 'claim': 'getUuid trên warehouse null', 'artifact': 'CartServiceImpl.java:40'}],
            'alternatives': [{'claim': 'dữ liệu kho sai', 'status': 'refuted', 'artifact': 'sql'}],
            'blast_radius': ['12 giỏ hàng tương tự'], 'introduced_by': '', 'code_version': 'không rõ', 'suggested_fix': 'null-check',
            'failing_test_idea': '', 'repro_steps': '', 'jira_title': 'NPE delivery-options', 'could_not_confirm': []},
    }
    for k, v in over.items():
        out[k] = v
    return out


# --- everything real: C concludes WITHOUT a DB step (the new gate) ---
out = make()
assert rca.validate(out, tr, K) == [], out['_problems']
assert out['rca']['code_location']['verified'] and out['rca']['decisive_signal']['verified']
assert rca.gate_failures(out) == [] and rca.verdict(out) == 'C'
assert out['_tool_calls'] == 2

# --- the same run under the old rule (all 3 steps) would have been '?': A/B/D still need it ---
assert rca.verdict({**out, 'type': 'B'}) == '?'

# --- fabricated line number / snippet -> not verified, source_be downgraded, no conclusion ---
bad = make()
bad['rca']['code_location']['line_start'] = bad['rca']['code_location']['line_end'] = 12
bad['evidence']['source_be']['detail'] = 'mono-backend/java/order/CartServiceImpl.java:999'
probs = rca.validate(bad, tr, K)
assert not bad['rca']['code_location']['verified'] and bad['evidence']['source_be']['status'] == 'not_checked' and probs
assert rca.verdict(bad) == '?' and 'code_location' in rca.gate_failures(bad)

# --- a quote that is not in any tool result is rejected ---
q = make()
q['rca']['decisive_signal']['quote'] = 'java.lang.IllegalStateException: boom boom'
rca.validate(q, tr, K)
assert not q['rca']['decisive_signal']['verified'] and rca.verdict(q) == '?'

# --- confirmed claims without any tool call are downgraded ---
no_tools = rca.parse_transcript('')
n = make()
rca.validate(n, no_tools, K)
assert n['evidence']['newrelic']['status'] == 'not_checked'

# --- a still-supported competing explanation blocks the conclusion; empty list too ---
for alts in ([{'claim': 'dữ liệu', 'status': 'supported', 'artifact': 'sql'}], []):
    o = make()
    o['rca']['alternatives'] = alts
    rca.validate(o, tr, K)
    assert rca.verdict(o) == '?' and 'alternatives' in rca.gate_failures(o)

# --- verifier FAIL blocks; PASS does not ---
o = make()
rca.validate(o, tr, K)
o['verifier'] = {'verdict': 'FAIL', 'holes': ['reachability chưa kiểm']}
assert rca.verdict(o) == '?'
o['verifier'] = {'verdict': 'PASS', 'holes': []}
assert rca.verdict(o) == 'C'

# --- config gate overrides ---
assert rca.verdict(make(), {'C': ['source_be', 'db', 'newrelic']}) == '?'   # old 3/3 rule, selectable from config

# --- rendering: permalink pinned to the clone's sha, candidate wording when not confirmed ---
o = make()
rca.validate(o, tr, K)
o['_versions'] = {'mono-backend': {'sha': 'a' * 40, 'branch': 'dev', 'date': '2026-11-09'}}
know = [{'name': 'mono-backend', 'url': 'https://github.com/droppii/mono-backend.git', 'branch': 'dev'}]
text = '\n'.join(rca.render_rca(o, know, K, confirmed=True))
assert 'https://github.com/droppii/mono-backend/blob/' + 'a' * 40 + '/java/order/CartServiceImpl.java#L40' in text, text
assert 'Nguyên nhân gốc' in text and 'Cơ chế' in text and 'getUuid' in text and 'mono-backend@aaaaaaaa' in text
assert 'Ứng viên' in '\n'.join(rca.render_rca(o, know, K, confirmed=False))

# --- bot.compose_reply: unconfirmed C still hands the candidate to on-call (this was hidden before) ---
o = make()
o['rca']['alternatives'] = []          # gate fails on 'alternatives'
rca.validate(o, tr, K)
o['_versions'] = {}
old_k = bot.KNOWLEDGE_DIR
bot.KNOWLEDGE_DIR = K
try:
    r = bot.compose_reply(o, {'Priority': ['High']}, 'Dev C', 'BA D')
finally:
    bot.KNOWLEDGE_DIR = old_k
assert 'Loại: ? - Không chắc' in r and 'Ứng viên nguyên nhân' in r and 'CartServiceImpl.java:40' in r and 'Chuyển: Dev C' in r, r
assert 'đã loại giả thuyết khác ✗' in r, r

# --- bot.finalize_log: a saved transcript -> verified result (same path eval --from-log uses) ---
good = make()
good['rca']['alternatives'] = [{'claim': 'dữ liệu kho sai', 'status': 'refuted', 'artifact': 'sql'}]
log_lines = transcript.splitlines()[:-1] + [json.dumps({'type': 'result', 'is_error': False, 'structured_output': good, 'total_cost_usd': 0.2})]
logf = Path(tempfile.mkdtemp(prefix='dcs-log-')) / 'case.jsonl'
logf.write_text('\n'.join(log_lines) + '\n', encoding='utf-8')
old_k = bot.KNOWLEDGE_DIR
bot.KNOWLEDGE_DIR = K
try:
    fin = bot.finalize_log(logf, versions={'mono-backend': {'sha': 'b' * 40, 'branch': 'dev', 'date': '2026-11-09'}})
    assert bot.verdict(fin) == 'C' and fin['_cost'] == 0.2 and fin['_tool_calls'] == 2 and fin['_problems'] == []
    reply = bot.compose_reply(fin, {'Priority': ['High']}, 'Dev C', 'BA D')
    assert 'Nguyên nhân gốc' in reply and 'blob/' + 'b' * 40 in reply and 'Loại: C - Bug' in reply, reply
    assert 'code: mono-backend/java/order/CartServiceImpl.java:40' in bot.bot_fields(fin, 'Đã chuyển người')['AI Note']
    # a run that crashed has no structured output -> raises (bot.fail() then hands the ticket to on-call)
    bad_log = logf.with_name('bad.jsonl')
    bad_log.write_text(json.dumps({'type': 'result', 'is_error': True}) + '\n')
    try:
        bot.finalize_log(bad_log)
        raise AssertionError('crashed run not detected')
    except RuntimeError:
        pass
finally:
    bot.KNOWLEDGE_DIR = old_k

# --- redact_obj masks inside nested JSON (used for saved transcripts) ---
red = rca.redact_obj({'a': ['gọi 0912345678', {'b': 'pwd=abc'}], 'n': 3}, bot.sanitize)
assert '0912345678' not in json.dumps(red) and 'abc' not in json.dumps(red) and red['n'] == 3

# --- prior art ---
F = {'code': 'Code', 'title': 'Title', 'description': 'Description', 'status': 'Status', 'resolution': 'Resolution'}
rows = [
    {'Code': 'D1', 'Title': 'Không lưu được voucher KM01', 'Description': 'báo khuyến mãi không tồn tại', 'Status': 'Done',
     'Resolution': 'Voucher đã bị kết thúc thủ công, đúng thiết kế'},
    {'Code': 'D2', 'Title': 'Sai tồn kho Odoo', 'Description': 'ERP lệch', 'Status': 'Done', 'Resolution': 'đồng bộ lại'},
    {'Code': 'D3', 'Title': 'Không lưu được voucher KM02', 'Description': 'khuyến mãi không tồn tại', 'Status': 'Open', 'Resolution': ''},
]
hits = priorart.similar({'Title': 'Không lưu được voucher KM03', 'Description': 'báo khuyến mãi không tồn tại hoặc không khả dụng'},
                        rows, F, {'Done'})
assert [h['code'] for h in hits] == ['D1'] and 'đúng thiết kế' in hits[0]['resolution'], hits   # open/unresolved ones excluded
assert priorart.similar({'Title': '', 'Description': ''}, rows, F, {'Done'}) == []

# ===== FE / mobile: layer from the repo, version-pinned citations, client_version gate =====
import subprocess
MOB = K / 'biz-mobile'
MOB.mkdir()
def mg(*a):
    subprocess.run(['git', '-C', str(MOB), '-c', 'user.name=T', '-c', 'user.email=t@x.io', *a], check=True, capture_output=True)
mg('init', '-q', '-b', 'main')
(MOB / 'src').mkdir()
cart = MOB / 'src/CartScreen.tsx'
cart.write_text('export function Cart() {\n  const id = cart.warehouse.uuid;\n  return id;\n}\n', encoding='utf-8')   # released code
mg('add', '.'); mg('commit', '-qm', 'v3.12.0'); mg('tag', 'v3.12.0')
cart.write_text('// refactor\nexport function Cart() {\n  const id = cart.warehouse?.uuid;\n  return id;\n}\n', encoding='utf-8')  # HEAD differs
mg('commit', '-qam', 'refactor')
LAYERS = {'mono-backend': 'be', 'biz-mobile': 'mobile'}


def mobile(**loc_over):
    o = make()
    o['rca']['layer'] = 'be'                                  # the model guessed wrong
    o['rca']['code_location'] = {'file': 'biz-mobile/src/CartScreen.tsx', 'line_start': 2, 'line_end': 2, 'symbol': 'Cart',
                                 'snippet': 'cart.warehouse.uuid', 'ref': 'v3.12.0'} | loc_over
    o['rca']['client_version'] = 'Biz Mobile 3.12.0 iOS, bundle CodePush v45, releaseStage staging (Bugsnag)'
    return o


# the cited line only exists in the released tag, not in HEAD: it verifies at ref, fails without it
o = mobile()
probs = rca.validate(o, tr, K, LAYERS)
assert o['rca']['code_location']['verified'], probs
assert o['rca']['layer'] == 'mobile' and o['rca']['layer_derived'] == 'mobile'              # repo decides, claim overridden
assert any(p.startswith('layer:') and 'be' in p for p in probs), probs                      # and the mismatch is reported
o2 = mobile(ref='')
rca.validate(o2, tr, K, LAYERS)
assert not o2['rca']['code_location']['verified']                                           # HEAD has uuid?. not uuid
for bad_ref in ('v9.9.9', '--output=/tmp/pwn', 'v3.12.0..main', '-p'):
    o3 = mobile(ref=bad_ref)
    rca.validate(o3, tr, K, LAYERS)
    assert not o3['rca']['code_location']['verified'], bad_ref

# mobile bug needs the client version, even though config gate for C does not list it
o = mobile()
rca.validate(o, tr, K, LAYERS)
assert rca.gate_failures(o) == [] and rca.verdict(o) == 'C'
for v in ('', 'không rõ: Bugsnag không có sự kiện của người dùng này'):
    o = mobile()
    o['rca']['client_version'] = v
    rca.validate(o, tr, K, LAYERS)
    assert rca.gate_failures(o) == ['client_version'] and rca.verdict(o) == '?', v
# an FE (web) bug is not forced to state a client version; neither is BE
o = make()
o['rca']['layer'] = 'be'
rca.validate(o, tr, K, LAYERS)
assert rca.verdict(o) == 'C' and o['rca']['layer'] == 'be' and o['rca']['layer_derived'] == 'be'
# without a layers map nothing changes (older callers)
o = make()
rca.validate(o, tr, K)
assert o['rca']['layer_derived'] is None and rca.verdict(o) == 'C'

# rendering: layer, client version, link pinned to the released tag instead of the clone's HEAD
o = mobile()
rca.validate(o, tr, K, LAYERS)
o['_versions'] = {'biz-mobile': {'sha': 'c' * 40, 'branch': 'main', 'date': '2026-11-09'}}
know2 = know + [{'name': 'biz-mobile', 'url': 'https://github.com/droppii/biz-mobile.git', 'branch': 'main'}]
text = '\n'.join(rca.render_rca(o, know2, K, confirmed=True))
assert 'Lớp lỗi: mobile (code đọc ở v3.12.0)' in text and 'Phiên bản client: Biz Mobile 3.12.0' in text, text
assert 'https://github.com/droppii/biz-mobile/blob/v3.12.0/src/CartScreen.tsx#L2' in text, text
assert 'phiên bản app/bundle' == rca.CHECK_LABEL['client_version']

# --- a bare file name / partial path resolves only when exactly one file matches (models often cite "Foo.java:73") ---
(K / 'mono-backend/y').mkdir(); (K / 'biz-mobile/z').mkdir()
(K / 'mono-backend/y/Dup.java').write_text('class A {}\n'); (K / 'biz-mobile/z/Dup.java').write_text('class B {}\n')
(K / 'mono-backend/y/Uniq.java').write_text('class U {}\n')
rca._NAMES['dir'] = None                                                  # fixtures were added after the cached index
assert rca.resolve_path('CartServiceImpl.java', K) == JAVA.resolve()
assert rca.resolve_path('Uniq.java', K) == (K / 'mono-backend/y/Uniq.java').resolve()
assert rca.resolve_path('order/CartServiceImpl.java', K) == JAVA.resolve()
assert rca.resolve_path('Dup.java', K) is None                            # ambiguous: refuse, do not guess
assert rca.resolve_path('y/Dup.java', K) == (K / 'mono-backend/y/Dup.java').resolve()   # disambiguated by a partial path
assert rca.resolve_path('Nope.java', K) is None and rca.resolve_path('../Uniq.java', K) is None
sbare = make()
sbare['evidence']['source_be']['detail'] = 'CartServiceImpl.java:40 gọi getUuid'                # no directory, as the model often writes
sbare['rca']['code_location']['file'] = 'CartServiceImpl.java'
rca.validate(sbare, tr, K)
assert sbare['evidence']['source_be']['status'] == 'confirmed' and sbare['rca']['code_location']['verified'], sbare['_problems']

# --- loose comparison: quotes / backslashes / whitespace do not matter (they break the model's JSON, so it is told to drop them) ---
assert rca.loose(' a "b" \\ x `c` ') == 'abxc' and rca.loose("it's") == 'its'
q2 = make()
q2['rca']['decisive_signal']['quote'] = 'Cannot invoke Warehouse.getUuid() because w is null'        # quotes dropped by the model
rca.validate(q2, tr, K)
assert q2['rca']['decisive_signal']['verified']
q3 = make()
q3['rca']['code_location']['snippet'] = 'String id = cart.getWarehouse().getUuid();'                  # the file line has the same code
rca.validate(q3, tr, K)
assert q3['rca']['code_location']['verified']
q4 = make()
q4['rca']['decisive_signal']['quote'] = 'Cannot invoke Warehouse.getName() because w is null'        # a different method: still rejected
rca.validate(q4, tr, K)
assert not q4['rca']['decisive_signal']['verified']

print('ok rca')
