"""Locators for FE / mobile repos (used by gen_anchors.py). Static, regex-based, best effort: a locator is never
evidence; the brain must still read the code. Everything here is pure (files in, rows out).

Shapes handled (taken from the real droppii repos, see tests/test_anchors.py):
  UI text   RN `translation/resources/common_vi.ts` (nested TS object), next-intl `*.translations.json`
            ({"name": {"key": "ns:name", "messages": {"vi-VN": ...}}}), plain locale JSON.
            Repos that hard-code Vietnamese in components (web-mall) have no key table: Grep the text directly.
  API       `http.post('/path')`, template literals, `fetch`, `{url, method}`, and endpoint CONSTANTS used as
            `apiInstance.get(END_POINTS.USER_SERVICE.TOOLTIP_JOURNEY(id))`, resolved through the constants object.
  screens   React Navigation `<Stack.Screen name component>`, React Router, Next.js app/pages routes.
and joins client calls to backend routes (edges) and BE error codes to UI texts (error map).
"""
import bisect
import json
import os
import re
from pathlib import Path

CLIENT_EXT = {'.ts', '.tsx', '.js', '.jsx'}
SKIP_DIRS = {'.git', 'node_modules', 'build', 'dist', '.next', '.yarn', 'Pods', 'ios', 'android', 'coverage', '__mocks__',
             '__tests__', 'e2e', 'docs', 'storybook-static', '.turbo', 'public', '.claude', '.engineering', 'plans'}
LOCALE_DIRS = ('locales', 'locale', 'i18n', 'lang', 'langs', 'translations', 'translation', 'messages', 'resources')
LOCALE_TS = re.compile(r'(?:^|[_.\-])(vi|en|km)(?:[-_][A-Za-z]{2})?\.(?:ts|js)$')
MAX_JSON = 5 * 1024 * 1024
MAX_ROWS = 30000

GENERIC = r'(?:<[^()]*>)?'                                   # <BaseResponse<X>> between the method and its "("
API_CALL = re.compile(r'[.\s](get|post|put|delete|patch)\s*' + GENERIC + r"""\s*\(\s*(['"`])(.+?)\2""", re.I)
API_CONST = re.compile(r'[.\s](get|post|put|delete|patch)\s*' + GENERIC + r'\s*\(\s*([A-Z][A-Z0-9_]+)((?:\.[A-Za-z_$][\w$]*)+)')
FETCH = re.compile(r"""\bfetch\s*\(\s*(['"`])(.+?)\1(?:\s*,\s*\{[^}]*?method:\s*['"](\w+)['"])?""", re.S)
URL_KEY = re.compile(r"""\burl:\s*(['"`])(.+?)\1""")
METHOD_NEAR = re.compile(r"""\bmethod:\s*['"](\w+)['"]""", re.I)
PATH_LITERAL = re.compile(r"""(['"`])(/(?:api/)?v\d+/[\w\-/{}:$.]*|/api/[\w\-/{}:$.]*)\1""")
SYM = re.compile(r"""function\*?\s+(\w+)|(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*(?:async\s*)?(?:\(|function\b|\w+\s*=>)""")
DEF = re.compile(r"""(?:export\s+)?(?:default\s+)?(?:function|class|const)\s+([A-Z]\w+)""")
USE_KEY = re.compile(r"""(?:\bt|\btrans|\bi18n\.t|\btranslate|\$t|\bformatMessage)\s*\(\s*(?:\{\s*id:\s*)?(['"`])([\w.\-:/ ]+?)\1""")
SCREEN_JSX = re.compile(r"""<\s*[\w.]+\.Screen\b[^>]*?\bname=\{?['"]([\w\-/ ]+)['"]\}?[^>]*?\bcomponent=\{(\w+)\}""", re.S)
SCREEN_OBJ = re.compile(r"""\bname:\s*['"]([\w\-/ ]+)['"]\s*,\s*component:\s*(\w+)""")
ROUTE_JSX = re.compile(r"""<Route\b[^>]*?\bpath=['"]([^'"]+)['"][^>]*?(?:element=\{\s*<\s*(\w+)|component=\{(\w+)\})""", re.S)
CONST_OBJ = re.compile(r"""(?:export\s+)?const\s+([A-Z][A-Z0-9_]+)\s*(?::[^=\n]+)?=\s*(?:[\w.]+\()?\s*\{""")
KEY = r"""([\w$]+|'[^']+'|"[^"]+")"""
OPEN_KEY = re.compile(r'^\s*' + KEY + r':\s*\{\s*$')
CLOSE = re.compile(r'^\s*\}[,;)]*\s*$')
STR_VAL = re.compile(r'^\s*' + KEY + r"""\s*:\s*(['"`])(.*)\2\s*,?\s*$""")
KEY_ONLY = re.compile(r'^\s*' + KEY + r':\s*$')
INLINE_OBJ = re.compile(r'^\s*' + KEY + r':\s*\{(.*)\}[,;)]*\s*$')
INLINE_PAIR = re.compile(r"""([\w$]+)\s*:\s*(['"`])(.*?)\2""")
ARROW = re.compile(r'^\s*' + KEY + r"""\s*:\s*(?:async\s*)?\([^)]*\)\s*(?::\s*[\w<>\[\]| ]+)?\s*=>\s*(.*)$""")
LIT = re.compile(r"""(['"`])(.*?)\1""")
ERRORISH = re.compile(r'ERR|EXCEPT|INVALID|NOT_|FAIL|DENIED|FORBIDDEN|UNAUTH|EXPIRE|REJECT|LIMIT|DUPLICATE', re.I)
KEYWORDS = {'if', 'for', 'while', 'switch', 'catch', 'function', 'return'}


def walk(repo):
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            yield Path(dp) / fn


def read(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''


def line_index(text):
    return [m.start() for m in re.finditer('\n', text)]


def line_of(idx, pos):
    return bisect.bisect_left(idx, pos) + 1


def unquote(k):
    return k.strip('\'"')


# ---------- i18n ----------

def clean_text(t):
    """Translation value -> comparable text: trimmed, without wrapping quotes some files keep."""
    t = str(t).strip()
    return t[1:-1].strip() if len(t) >= 2 and t[0] == t[-1] and t[0] in '\'"`' else t


def locale_of(name):
    m = re.search(r'(?:^|[^A-Za-z])(vi|en|km)(?:[-_][A-Za-z]{2})?(?:[^A-Za-z]|$)', name)
    return m.group(1) if m else ''


def flatten(d, prefix=''):
    """JSON locale dict -> [(dotted.key, text)] in file order."""
    out = []
    if isinstance(d, dict):
        for k, v in d.items():
            out += flatten(v, f'{prefix}.{k}' if prefix else str(k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out += flatten(v, f'{prefix}.{i}')
    elif isinstance(d, str):
        out.append((prefix, d))
    return out


def scan_locale(path, rel):
    """Locale JSON -> [(key, text, 'rel:line', locale)]. Handles `*.translations.json` ({name: {key, messages:{locale: text}}})
    and plain nested locale files. Line = first `"leaf":` after the previous key (file order)."""
    if path.stat().st_size > MAX_JSON:
        return []
    raw = read(path)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    lines = raw.splitlines()
    rows, pos = [], 0

    def find(leaf):
        nonlocal pos
        pat = re.compile(r'"' + re.escape(leaf) + r'"\s*:')
        ln = next((i for i in range(pos, len(lines)) if pat.search(lines[i])), pos)
        pos = ln
        return ln + 1

    if isinstance(data, dict) and any(isinstance(v, dict) and isinstance(v.get('messages'), dict) for v in data.values()):
        for name, entry in data.items():
            if isinstance(entry, dict) and isinstance(entry.get('messages'), dict):
                ln = find(name)
                for loc, text in entry['messages'].items():
                    if isinstance(text, str) and len(clean_text(text)) >= 4:
                        rows.append((entry.get('key') or name, clean_text(text), f'{rel}:{ln}', locale_of(loc)))
        return rows
    lc = locale_of(path.stem) or locale_of(path.parent.name)
    ns = '' if path.stem.lower() in ('vi', 'en', 'km') or locale_of(path.stem) == path.stem.lower() else path.stem
    for key, text in flatten(data):
        ln = find(key.split('.')[-1])
        if len(clean_text(text)) >= 4:
            rows.append((f'{ns}:{key}' if ns else key, clean_text(text), f'{rel}:{ln}', lc))
    return rows


def scan_ts_resource(path, rel):
    """RN-style `common_vi.ts` (`export default { common: { c_skip: 'Bỏ qua' } }`) -> [(ns:dotted.key, text, 'rel:line', locale)]."""
    m = LOCALE_TS.search(path.name)
    locale = m.group(1)
    ns = re.sub(r'[_.\-](vi|en|km)(?:[-_][A-Za-z]{2})?$', '', path.stem)
    stack, rows, pending = [], [], None
    for n, line in enumerate(read(path).splitlines(), 1):
        if pending is not None:
            v = LIT.match(line.strip())
            if v:
                rows.append((pending, v.group(2), f'{rel}:{n}', locale))
                pending = None
                continue
            pending = None
        mo = OPEN_KEY.match(line)
        if mo:
            stack.append(unquote(mo.group(1)))
            continue
        if CLOSE.match(line):
            if stack:
                stack.pop()
            continue
        mv = STR_VAL.match(line)
        dotted = lambda k: f"{ns}:{'.'.join(stack + [unquote(k)])}"
        if mv:
            if len(clean_text(mv.group(3))) >= 4:
                rows.append((dotted(mv.group(1)), clean_text(mv.group(3)), f'{rel}:{n}', locale))
            continue
        mk = KEY_ONLY.match(line)
        if mk:
            pending = dotted(mk.group(1))
    return [r for r in rows if len(r[1]) >= 4]


def is_locale_ts(path, parts):
    return bool(LOCALE_TS.search(path.name)) and any(p in LOCALE_DIRS for p in parts[:-1]) and path.suffix in ('.ts', '.js')


def used_refs(key, used):
    """Places that use an i18n key. The namespace prefix (`common:`) is optional at the call site."""
    out = []
    for v in {key, key.split(':', 1)[-1]}:
        out += used.get(v, [])
    return out[:3]


# ---------- endpoint constants ----------

def brace_block(text, start):
    """Text of the `{...}` that opens at text[start] (balanced; `${}` in template literals balances itself)."""
    depth = 0
    for i in range(start, min(len(text), start + 400000)):
        c = text[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ''


def first_literal(s):
    m = LIT.search(s)
    return m.group(2) if m else None


def scan_constants(text):
    """UPPER_CASE objects that hold endpoint paths -> {NAME: {'A.B': '/path/{}'}}. Values are strings or arrow functions
    returning a template literal; nesting comes from the object structure."""
    found = {}
    for m in CONST_OBJ.finditer(text):
        block = brace_block(text, m.end() - 1)
        if not block:
            continue
        stack, paths, pending, arrow_next = [], {}, None, None
        for line in block.splitlines()[1:]:
            if arrow_next:
                lit = first_literal(line)
                if lit and lit.startswith('/'):
                    paths['.'.join(stack + [arrow_next])] = lit
                arrow_next = None
                continue
            if pending:
                lit = first_literal(line)
                if lit and lit.startswith('/'):
                    paths['.'.join(stack + [pending])] = lit
                pending = None
                continue
            mi = INLINE_OBJ.match(line)
            if mi:  # `NESTED: { D: '/svc/d', E: '/svc/e' },` on one line
                for k, _, v in INLINE_PAIR.findall(mi.group(2)):
                    if v.startswith('/'):
                        paths['.'.join(stack + [unquote(mi.group(1)), k])] = v
                continue
            mo = OPEN_KEY.match(line)
            if mo:
                stack.append(unquote(mo.group(1)))
                continue
            if CLOSE.match(line):
                if stack:
                    stack.pop()
                continue
            mv = STR_VAL.match(line)
            if mv and mv.group(3).startswith('/'):
                paths['.'.join(stack + [unquote(mv.group(1))])] = mv.group(3)
                continue
            ma = ARROW.match(line)
            if ma:
                lit = first_literal(ma.group(2))
                if lit and lit.startswith('/'):
                    paths['.'.join(stack + [unquote(ma.group(1))])] = lit
                else:
                    arrow_next = unquote(ma.group(1))
                continue
            mk = KEY_ONLY.match(line)
            if mk:
                pending = unquote(mk.group(1))
        if paths:
            found.setdefault(m.group(1), {}).update(paths)
    return found


# ---------- one client repo ----------

def next_route(parts):
    """Next.js file-based route of a source file path (tuple of parts), or None. app/**/page.* and pages/** (not api, not _app)."""
    d, stem = list(parts[:-1]), Path(parts[-1]).stem
    if 'app' in d and stem == 'page':
        segs = d[d.index('app') + 1:]
    elif 'pages' in d and not stem.startswith('_'):
        segs = d[d.index('pages') + 1:] + ([] if stem == 'index' else [stem])
    else:
        return None
    if 'api' in segs:
        return None
    segs = [x for x in segs if not (x.startswith('(') and x.endswith(')'))]  # route groups do not appear in the URL
    conv = [re.sub(r'^\[\.\.\.(\w+)\]$', r':\1*', re.sub(r'^\[(\w+)\]$', r':\1', x)) for x in segs]
    return '/' + '/'.join(conv)


def scan_client(repo, name):
    """-> dict(i18n, used_keys, api, literals, screens, defs, consts) for repo `repo` (Path), cited as `name/relative/path`."""
    i18n, used, api, literals, screens, defs = [], {}, [], [], [], {}
    consts, pending_const, seen_api = {}, [], set()
    for f in walk(repo):
        rel = f'{name}/{f.relative_to(repo).as_posix()}'
        parts = [p.lower() for p in f.relative_to(repo).parts]
        if f.suffix == '.json':
            if (f.name.endswith('.translations.json') or any(p in LOCALE_DIRS for p in parts[:-1])) and f.name not in ('package.json', 'package-lock.json'):
                i18n += scan_locale(f, rel)
            continue
        if f.suffix not in CLIENT_EXT:
            continue
        if is_locale_ts(f, f.relative_to(repo).parts):
            i18n += scan_ts_resource(f, rel)
            continue
        text = read(f)
        if not text:
            continue
        idx = line_index(text)
        syms = [(m.start(), m.group(1) or m.group(2)) for m in SYM.finditer(text) if (m.group(1) or m.group(2)) not in KEYWORDS]
        sym_pos = [p for p, _ in syms]

        def sym_at(pos):
            i = bisect.bisect_right(sym_pos, pos) - 1
            return syms[i][1] if i >= 0 else ''

        for cname, paths in scan_constants(text).items():
            consts.setdefault(cname, {}).update(paths)
        for m in DEF.finditer(text):
            defs.setdefault(m.group(1), f'{rel}:{line_of(idx, m.start())}')
        for m in USE_KEY.finditer(text):
            used.setdefault(m.group(2), [])
            if len(used[m.group(2)]) < 3:
                used[m.group(2)].append(f'{rel}:{line_of(idx, m.start())}')
        for m in API_CALL.finditer(text):
            p = m.group(3)
            if p.startswith(('/', 'http')) or '/api/' in p:
                api.append((m.group(1).upper(), p, f'{rel}:{line_of(idx, m.start())}', sym_at(m.start())))
                seen_api.add((rel, p))
        for m in API_CONST.finditer(text):  # resolved after every file is read (the constants live elsewhere)
            pending_const.append((m.group(1).upper(), m.group(2), m.group(3).lstrip('.'), f'{rel}:{line_of(idx, m.start())}', sym_at(m.start())))
        for m in FETCH.finditer(text):
            p = m.group(2)
            if p.startswith(('/', 'http')) or '/api/' in p:
                api.append(((m.group(3) or 'ANY').upper(), p, f'{rel}:{line_of(idx, m.start())}', sym_at(m.start())))
                seen_api.add((rel, p))
        for m in URL_KEY.finditer(text):
            p = m.group(2)
            if p.startswith(('/', 'http')) or '/api/' in p:
                mm = METHOD_NEAR.search(text, m.end(), m.end() + 200) or METHOD_NEAR.search(text, max(0, m.start() - 200), m.start())
                api.append(((mm.group(1) if mm else 'ANY').upper(), p, f'{rel}:{line_of(idx, m.start())}', sym_at(m.start())))
                seen_api.add((rel, p))
        for m in PATH_LITERAL.finditer(text):
            if (rel, m.group(2)) not in seen_api:
                literals.append((m.group(2), f'{rel}:{line_of(idx, m.start())}'))
        for m in SCREEN_JSX.finditer(text):
            screens.append((m.group(1), m.group(2), f'{rel}:{line_of(idx, m.start())}'))
        for m in SCREEN_OBJ.finditer(text):
            screens.append((m.group(1), m.group(2), f'{rel}:{line_of(idx, m.start())}'))
        for m in ROUTE_JSX.finditer(text):
            screens.append((m.group(1), m.group(2) or m.group(3), f'{rel}:{line_of(idx, m.start())}'))
        route = next_route(f.relative_to(repo).parts)
        if route:
            screens.append((route, f.stem, f'{rel}:1'))
    for verb, cname, key, loc, sym in pending_const:        # END_POINTS.A.B -> '/service/v1/...'
        path = consts.get(cname, {}).get(key)
        if path:
            api.append((verb, path, loc, sym))
    return {'i18n': i18n[:MAX_ROWS], 'used_keys': used, 'api': api, 'literals': literals, 'screens': screens, 'defs': defs,
            'consts': consts}


# ---------- joins ----------

def norm_path(p):
    """Client or server path -> comparable form: no host/query, params as {}, lowercase, no trailing slash."""
    p = re.sub(r'^https?://[^/]+', '', str(p).strip())
    p = re.split(r'[?#]', p)[0]
    p = re.sub(r'\$\{[^}]*\}|\{[^}]*\}|:\w+\*?|\[\w+\]', '{}', p)
    p = re.sub(r'/+', '/', p).rstrip('/').lower()
    return p if p.startswith('/') else '/' + p


def _segs(p, strip_service=False):
    segs = [s for s in norm_path(p).split('/') if s]
    if strip_service:
        if segs and segs[0] == '{}':                      # `${BASE_URL}/api/x`: the variable is the host, not a path segment
            segs = segs[1:]
        if segs and segs[0].endswith('-service'):          # gateway prefix, e.g. /marketing-service/v1/app/...
            segs = segs[1:]
    return segs


def _eq(a, b):
    return a == b or a == '{}' or b == '{}'


def _literal_hits(fe, be):
    """How many positions hold the SAME literal on both sides (a `{}` parameter on either side proves nothing)."""
    return sum(1 for a, b in zip(fe, be) if a == b and a != '{}')


def service_for(fe_path, services):
    """Gateway prefix `/<x>-service/...` -> code dir of that service (from the service catalog), or ''."""
    segs = [s for s in norm_path(fe_path).split('/') if s]
    if segs and segs[0].endswith('-service'):
        return (services or {}).get(segs[0], '')
    return ''


def match_route(fe_verb, fe_path, be_routes):
    """Client call -> [(confidence, be_route)]. Exact path = high; client path is a suffix of the server path (the HTTP
    client has a base URL such as /api/v1/app) = medium; more than 3 suffix candidates = low. A leading `<x>-service`
    gateway segment on the client path is ignored."""
    fe = _segs(fe_path, strip_service=True)
    if sum(1 for s in fe if s != '{}') < 2:                # need two literal segments to say anything
        return []
    exact, suffix = [], []
    for r in be_routes:
        verb = r[0]
        if fe_verb not in ('ANY', '', '?') and verb not in ('ANY', 'ROUTE') and verb != fe_verb:
            continue
        be = _segs(r[1])
        if len(be) == len(fe) and all(_eq(a, b) for a, b in zip(fe, be)) and _literal_hits(fe, be) >= 2:
            exact.append(r)
        elif len(be) > len(fe) and all(_eq(a, b) for a, b in zip(fe, be[-len(fe):])) and _literal_hits(fe, be[-len(fe):]) >= 2:
            suffix.append(r)
    if exact:
        return [('high', r) for r in exact[:3]]
    return [('low' if len(suffix) > 3 else 'medium', r) for r in suffix[:3]]


def join_edges(api_calls, be_routes, services=None):
    """-> (rows, unmatched_count). Row = (client loc, symbol, verb, path, confidence, be loc, be handler, dispatched, handler class).
    A call with no route match but a `/<x>-service/` prefix still yields a row with confidence `service`: the code dir of
    that service, so the brain knows which repo and folder to open."""
    rows, unmatched = [], 0
    for verb, path, loc, sym in api_calls:
        m = match_route(verb, path, be_routes)
        if m:
            for conf, r in m:
                rows.append((loc, sym, verb, path, conf, r[2], r[3], r[4], r[5]))
            continue
        d = service_for(path, services)
        if d:
            rows.append((loc, sym, verb, path, 'service', d, '', '', ''))
        else:
            unmatched += 1
    return rows, unmatched


def error_map(be_defs, be_refs, i18n, client_code_refs):
    """BE error constants -> the UI texts that equal their message, plus client places that handle the numeric code.
    be_defs: {name: (code, loc, message)}; be_refs: {name: [loc]}; i18n: [(key, text, loc, locale)];
    client_code_refs: {code: [loc]}. -> rows (code, name, message, ui keys, client handlers, be raised at)."""
    by_text = {}
    for key, text, loc, *_ in i18n:
        by_text.setdefault(re.sub(r'\s+', ' ', text).casefold(), []).append((key, loc))
    rows = []
    for name, (code, loc, msg) in be_defs.items():
        ui = by_text.get(re.sub(r'\s+', ' ', msg).casefold(), []) if len(msg) >= 6 else []
        cl = client_code_refs.get(code, []) if len(code) >= 4 and ERRORISH.search(name) else []   # `1000` as a page size is not an error
        if ui or cl:
            rows.append((code, name, msg, '; '.join(f'{k} ({l})' for k, l in ui[:3]), '; '.join(cl[:3]), '; '.join(be_refs.get(name, [])[:2])))
    return sorted(rows, key=lambda r: (int(r[0]), r[1]))


def client_code_refs(client_files, codes):
    """Lines in client code that mention a known BE numeric error code next to the word code/error/status."""
    codes = {c for c in codes if len(c) >= 4}              # 3-digit numbers are status enums / HTTP codes: too noisy
    if not codes:
        return {}
    rx = re.compile(r'(?<![\w.])(' + '|'.join(re.escape(c) for c in sorted(codes)) + r')(?![\w.])')
    out = {}
    for name, repo in client_files:
        for f in walk(repo):
            if f.suffix not in CLIENT_EXT:
                continue
            for i, line in enumerate(read(f).splitlines(), 1):
                low = line.lower()
                if ('code' in low or 'error' in low or 'status' in low) and not low.lstrip().startswith(('//', '*')):
                    for m in rx.finditer(line):
                        lst = out.setdefault(m.group(1), [])
                        if len(lst) < 5:
                            lst.append(f"{name}/{f.relative_to(repo).as_posix()}:{i}")
    return out
