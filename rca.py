"""RCA evidence validation, gating and rendering. Pure functions: no network, no Lark, no Claude calls.

The model fills `out['rca']` and `out['evidence']`; this module does NOT trust them. It re-checks every
citation against (a) the real files under knowledge/ and (b) the tool results recorded in the run's
transcript (logs/*.jsonl), downgrades what it cannot verify, and decides whether the bot may conclude.
"""
import json
import re
from pathlib import Path

CODE_REF = re.compile(
    r'(?<![\w./\\-])((?:[\w.\-]+/)*[\w.\-]+\.(?:java|kt|cs|ts|tsx|js|jsx|py|go|sql|xml|ya?ml|properties)):(\d+)(?:\s*[-–]\s*(\d+))?')

# What must hold before the bot may state a conclusion, per type. Overridable from config.json "gate".
DEFAULT_GATE = {
    'A': ['source_be', 'db', 'newrelic'],
    'B': ['source_be', 'db', 'newrelic'],
    'D': ['source_be', 'db', 'newrelic'],
    'C': ['code_location', 'runtime_signal', 'mechanism', 'alternatives'],
}
CHECK_LABEL = {'source_be': 'Code BE', 'db': 'DB', 'newrelic': 'New Relic', 'code_location': 'dòng code lỗi',
               'runtime_signal': 'dấu hiệu runtime', 'mechanism': 'cơ chế + chuỗi nhân quả',
               'alternatives': 'đã loại giả thuyết khác'}


def norm(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


# ---------- transcript ----------

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(_text(c.get('text') if isinstance(c, dict) else c) for c in content)
    return '' if content is None else str(content)


def parse_transcript(text):
    """stream-json lines -> {'tool_uses': [{'name','input'}], 'results': str, 'final': result-event|None}."""
    uses, results, final = [], [], None
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get('type') == 'result':
            final = e
        msg = e.get('message')
        content = msg.get('content') if isinstance(msg, dict) else None
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get('type') == 'tool_use':
                uses.append({'name': b.get('name', ''), 'input': b.get('input') or {}})
            elif b.get('type') == 'tool_result':
                results.append(_text(b.get('content')))
    return {'tool_uses': uses, 'results': '\n'.join(results), 'final': final}


def _used(tr, prefix):
    return any(u['name'].startswith(prefix) for u in tr['tool_uses'])


def _haystack(tr):
    r = tr['results']
    return norm(r) + ' ' + norm(r.replace('\\"', '"').replace('\\n', ' ').replace('\\t', ' '))


# ---------- code citations ----------

def resolve_path(ref, kdir):
    """A cited path -> real file under knowledge/ (never outside it), else None."""
    kdir = Path(kdir)
    ref = ref.strip().strip('`').replace('\\', '/')
    cands = []
    if Path(ref).is_absolute():
        cands.append(Path(ref))
    r = re.sub(r'^(\./)+', '', ref)
    if r.startswith('knowledge/'):
        r = r[len('knowledge/'):]
    cands.append(kdir / r)
    if kdir.is_dir():
        cands += [d / r for d in sorted(kdir.iterdir()) if d.is_dir()]
    root = kdir.resolve()
    for c in cands:
        try:
            c = c.resolve()
        except OSError:
            continue
        if c.is_file() and root in c.parents:
            return c
    return None


def check_code_ref(ref, start, end, snippet, kdir):
    """(ok, reason, path). The file must exist under knowledge/, the lines must be inside it, and a quoted
    snippet must really appear at those lines (+-3 lines of slack)."""
    path = resolve_path(ref, kdir)
    if not path:
        return False, f'{ref}: file không có trong knowledge/', None
    try:
        start = int(start)
        end = int(end or start)
    except (TypeError, ValueError):
        return False, f'{ref}: số dòng không hợp lệ', path
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    if not (1 <= start <= end <= len(lines)):
        return False, f'{ref}:{start}-{end}: dòng ngoài phạm vi file ({len(lines)} dòng)', path
    if snippet and norm(snippet):
        window = norm(' '.join(lines[max(0, start - 1 - 3):end + 3]))
        parts = [norm(p) for p in re.split(r'\.\.\.|…', snippet) if len(norm(p)) >= 4]
        if any(p not in window for p in parts):
            return False, f'{ref}:{start}-{end}: đoạn trích không khớp nội dung dòng', path
    return True, '', path


# ---------- validation (downgrades what cannot be verified) ----------

def validate(out, tr, kdir):
    """Re-check the model's claims against disk and the transcript. Mutates `out` (statuses are downgraded,
    `verified` flags are set) and returns the list of problems found."""
    problems = []
    ev = out.setdefault('evidence', {})
    rca = out.setdefault('rca', {})

    # code location
    loc = rca.get('code_location') or {}
    loc_ok = False
    if loc.get('file'):
        loc_ok, why, _ = check_code_ref(loc['file'], loc.get('line_start'), loc.get('line_end'), loc.get('snippet'), kdir)
        if not loc_ok:
            problems.append('code_location: ' + why)
    loc['verified'] = loc_ok
    rca['code_location'] = loc

    # source_be: every path:line cited must be real
    sb = ev.get('source_be') or {}
    good = bad = 0
    for f, s, e in CODE_REF.findall(sb.get('detail', '')):
        ok, why, _ = check_code_ref(f, s, e or s, '', kdir)
        good += ok
        if not ok:
            bad += 1
            problems.append('source_be: ' + why)
    if sb.get('status') == 'confirmed' and (bad or not (good or loc_ok)):
        sb['status'] = 'not_checked'
        problems.append('source_be: hạ confirmed → not_checked (không có trích dẫn file:dòng kiểm chứng được)')

    # db / newrelic must have actually been queried
    for key, prefix in (('db', 'mcp__dbhub'), ('newrelic', 'mcp__newrelic')):
        st = ev.get(key) or {}
        if st.get('status') == 'confirmed' and not _used(tr, prefix):
            st['status'] = 'not_checked'
            problems.append(f'{key}: hạ confirmed → not_checked (không có lần gọi tool nào)')

    # decisive signal: the quote must exist in a real tool result
    sig = rca.get('decisive_signal') or {}
    sig_ok = False
    if sig.get('observed'):
        q = norm(sig.get('quote'))
        sig_ok = len(q) >= 8 and q in _haystack(tr)
        if not sig_ok:
            problems.append('decisive_signal: câu trích không có trong kết quả tool')
    sig['verified'] = sig_ok
    rca['decisive_signal'] = sig

    out['_problems'] = problems
    out['_tool_calls'] = len(tr['tool_uses'])
    return problems


# ---------- gate ----------

def check_ok(out, name):
    ev, rca = out.get('evidence') or {}, out.get('rca') or {}
    if name in ('source_be', 'db', 'newrelic'):
        return (ev.get(name) or {}).get('status') == 'confirmed'
    if name == 'code_location':
        return bool((rca.get('code_location') or {}).get('verified'))
    if name == 'runtime_signal':
        return bool((rca.get('decisive_signal') or {}).get('verified'))
    if name == 'mechanism':
        return bool(norm(rca.get('mechanism'))) and bool(rca.get('causal_chain'))
    if name == 'alternatives':
        alts = rca.get('alternatives') or []
        return bool(alts) and not any(a.get('status') == 'supported' for a in alts)
    return False


def gate_failures(out, gate=None):
    return [n for n in (gate or DEFAULT_GATE).get(out['type'], []) if not check_ok(out, n)]


def gated(out, gate=None):
    return out['type'] in (gate or DEFAULT_GATE) and not out['missing_info']


def verifier_failed(out):
    return (out.get('verifier') or {}).get('verdict') == 'FAIL'


def verdict(out, gate=None):
    """The type the bot is allowed to publish. Decided here, not by the model."""
    if gated(out, gate) and (gate_failures(out, gate) or verifier_failed(out)):
        return '?'
    return out['type']


def has_candidate(out):
    rca = out.get('rca') or {}
    return bool((rca.get('code_location') or {}).get('verified') or (rca.get('decisive_signal') or {}).get('verified'))


# ---------- rendering ----------

def permalink(path, start, end, versions, knowledge, kdir):
    try:
        rel = Path(path).resolve().relative_to(Path(kdir).resolve())
    except ValueError:
        return ''
    repo, sub = rel.parts[0], '/'.join(rel.parts[1:])
    k = next((x for x in knowledge if x['name'] == repo), None)
    sha = (versions or {}).get(repo, {}).get('sha')
    if not k or not sha:
        return ''
    base = k['url'][:-4] if k['url'].endswith('.git') else k['url']
    frag = f'#L{start}' + (f'-L{end}' if end and int(end) != int(start) else '')
    return f'{base}/blob/{sha}/{sub}{frag}'


def _fence(snippet, limit=6):
    lines = [l.rstrip() for l in str(snippet).splitlines() if l.strip()][:limit]
    return ['```', *lines, '```'] if lines else []


def render_rca(out, knowledge, kdir, confirmed):
    """The investigation package for on-call, built from verified fields (the model's prose is not used)."""
    rca = out.get('rca') or {}
    versions = out.get('_versions') or {}
    head = ('**Nguyên nhân gốc (bot đã đối chiếu code và log):**' if confirmed
            else '**Ứng viên nguyên nhân (CHƯA đủ bằng chứng, người trực kiểm tra):**')
    L = [head]
    loc = rca.get('code_location') or {}
    if loc.get('verified'):
        path = resolve_path(loc['file'], kdir)
        rng = f"{loc['line_start']}-{loc['line_end']}" if loc['line_end'] != loc['line_start'] else str(loc['line_start'])
        link = permalink(path, loc['line_start'], loc['line_end'], versions, knowledge, kdir) if path else ''
        name = f"{loc['file']}:{rng}"
        L.append(f"- Code: {'[' + name + '](' + link + ')' if link else '`' + name + '`'}"
                 + (f" · `{loc['symbol']}`" if loc.get('symbol') else ''))
        L += ['  ' + x for x in _fence(loc.get('snippet'))]
    sig = rca.get('decisive_signal') or {}
    if sig.get('verified'):
        L.append(f"- Dấu hiệu runtime: {sig.get('what', '')} — {sig.get('source', '')} {sig.get('at', '')}".rstrip())
        L.append(f"  > {norm(sig.get('quote'))[:300]}")
    elif sig.get('observed'):
        L.append('- Dấu hiệu runtime: model nêu nhưng bot KHÔNG tìm thấy câu trích trong kết quả tool (bỏ qua)')
    if norm(rca.get('mechanism')):
        L.append(f"- Cơ chế: {norm(rca['mechanism'])}")
    chain = rca.get('causal_chain') or []
    if chain:
        L.append('- Chuỗi nhân quả:')
        L += [f"  {i}. [{c.get('link')}] {c.get('claim')} ({c.get('artifact')})" for i, c in enumerate(chain, 1)]
    alts = rca.get('alternatives') or []
    if alts:
        mark = {'refuted': '✗ đã loại', 'supported': '✓ còn nghi', 'unverified': '? chưa kiểm'}
        L.append('- Đã xét: ' + '; '.join(f"{a.get('claim')} [{mark.get(a.get('status'), a.get('status'))}]" for a in alts))
    if rca.get('blast_radius'):
        L.append('- Phạm vi ảnh hưởng: ' + '; '.join(rca['blast_radius']))
    if norm(rca.get('introduced_by')):
        L.append(f"- Đưa vào bởi: {norm(rca['introduced_by'])}")
    versions_txt = ', '.join(f"{r}@{v['sha'][:8]} ({v.get('branch', '?')}, {v.get('date', '?')})"
                             for r, v in versions.items() if v.get('sha') and r != 'engineering-knowledge')
    if versions_txt or norm(rca.get('code_version')):
        L.append(f"- Bản code đã đọc: {versions_txt}" + (f". Đối chiếu bản đang chạy: {norm(rca['code_version'])}" if norm(rca.get('code_version')) else ''))
    for key, label in (('repro_steps', 'Cách tái hiện'), ('suggested_fix', 'Gợi ý sửa'),
                       ('failing_test_idea', 'Test đề xuất'), ('jira_title', 'Tiêu đề Jira gợi ý')):
        if norm(rca.get(key)):
            L.append(f"- {label}: {norm(rca[key])}")
    if rca.get('could_not_confirm'):
        L.append('- Chưa xác nhận được: ' + '; '.join(rca['could_not_confirm']))
    v = out.get('verifier')
    if v:
        L.append(f"- Verifier độc lập: {v.get('verdict')}" + (' — ' + '; '.join(v['holes']) if v.get('holes') else ''))
    if out.get('_problems'):
        L.append('- Bot hạ trạng thái do trích dẫn không kiểm chứng được: ' + ' | '.join(out['_problems'][:4]))
    return L


# ---------- logs ----------

def redact_obj(obj, fn):
    """Apply fn to every string inside a parsed JSON value."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [redact_obj(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v, fn) for k, v in obj.items()}
    return obj
