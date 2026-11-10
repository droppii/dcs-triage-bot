#!/usr/bin/env python3
"""Generate a locate-the-code index from the knowledge clones, so the brain can go
route / error code / table -> handler file:line with one Grep instead of guessing.

Writes knowledge/_anchors/{routes,errors,tables,versions}.md. Static, regex-based and best-effort: it is a
*locator*, never evidence (the brain must still Read the code). Run by tools/setup.sh; safe to re-run.
Run: python3 tools/gen_anchors.py [--knowledge knowledge] [--out knowledge/_anchors]
"""
import argparse, json, os, re, subprocess, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import anchors_client  # noqa: E402  (FE / mobile locators)

SRC_EXT = {'.java', '.kt', '.cs', '.ts', '.py', '.go'}
SKIP_DIRS = {'.git', 'node_modules', 'build', 'target', 'bin', 'obj', 'dist', '__pycache__', 'test', 'tests', '__tests__',
             'Test', 'Tests', 'migrations', 'Migrations', 'generated', 'vendor'}
HTTP = ('Get', 'Post', 'Put', 'Delete', 'Patch')
ANNOT = re.compile(r'^\s*(?:@(?P<j>\w+)(?:\((?P<ja>.*)\))?|\[(?P<c>\w+)(?:\((?P<ca>.*?)\))?\])\s*$')
PY_VERB = re.compile(r'''^\s*@\w+(?:\.\w+)*\.(get|post|put|delete|patch)\(\s*[rf]?["']([^"']*)["']''')        # FastAPI / Flask
GO_ROUTE = re.compile(r'''\b\w+\.(GET|POST|PUT|DELETE|PATCH)\(\s*"([^"]+)"\s*,\s*(?:[\w.]+\.)?(\w+)''')          # gin / echo
PY_ROUTE = re.compile(r'^\s*@(?:http\.)?route\((?P<a>.*)\)\s*$')
STR = re.compile(r'''["']([^"']*)["']''')
CLASS_RE = re.compile(r'\b(?:class|interface)\s+(\w+)')
FUNC_RE = re.compile(r'\b(\w+)\s*\(')
PY_DEF = re.compile(r'^\s*def\s+(\w+)\s*\(')
CMD_RE = re.compile(r'\bnew\s+(\w+(?:Command|Query|Request))\b')
HANDLER_FILE = re.compile(r'^(\w+(?:Command|Query|Request))Handler\.(?:java|kt|cs|ts)$')
ENUM_CODE = re.compile(r'^\s*([A-Z][A-Z0-9_]{2,})\s*\(\s*(\d{3,6})\s*(?:,\s*"([^"]*)")?')
CONST_CODE = re.compile(r'(?:static\s+final|const|readonly)\s+(?:\w+\s+)?([A-Za-z_]\w*)\s*(?::\s*\w+\s*)?=\s*(\d{4,6})\s*;?')
CS_ENUM_CODE = re.compile(r'^\s*([A-Z]\w+)\s*=\s*(\d{4,6})\s*,?\s*(?://.*)?$')
TABLE_RES = [re.compile(r'@Table\s*\([^)]*?name\s*=\s*"([^"]+)"'), re.compile(r'\[Table\(\s*"([^"]+)"'),
             re.compile(r'\.ToTable\(\s*"([^"]+)"'), re.compile(r'@Entity\s*\(\s*name\s*=\s*"([^"]+)"'),
             re.compile(r'^\s*_name\s*=\s*[\'"]([\w.]+)[\'"]')]  # last one: Odoo model name (python files only)


def source_files(repo):
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if os.path.splitext(fn)[1] in SRC_EXT:
                yield Path(dp) / fn


def read_lines(path):
    try:
        return path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []


def first_str(args):
    m = STR.search(args or '')
    return m.group(1) if m else ''


def join_path(*parts):
    return '/' + '/'.join(p.strip('/') for p in parts if p and p.strip('/'))


def scan_routes(path, lines, rel, handlers):
    """Route annotations (Spring, ASP.NET, Nest, Odoo) -> handler method, plus the Command/Query it dispatches."""
    rows, pending, prefix, open_parens = [], [], '', 0
    for i, line in enumerate(lines, 1):
        if open_parens > 0:  # inside a decorator whose arguments span several lines (@SwaggerResponse({ ... })): not a method
            open_parens += line.count('(') - line.count(')')
            continue
        gm = GO_ROUTE.search(line) if rel.endswith('.go') else None
        if gm:  # path groups (r.Group("/v1")) are not resolved: the path is as written at the call
            rows.append((gm.group(1), gm.group(2), f'{rel}:{i}', gm.group(3), '', ''))
            continue
        pv = PY_VERB.match(line)
        if pv:
            pending.append((pv.group(1).capitalize(), f'"{pv.group(2)}"', i))
            continue
        m = ANNOT.match(line)
        py = PY_ROUTE.match(line)
        if m or py:
            if py:
                pending.append(('route', py.group('a'), i))
            else:
                name, args = (m.group('j'), m.group('ja')) if m.group('j') else (m.group('c'), m.group('ca'))
                pending.append((name, args or '', i))
            continue
        if not line.strip() or line.strip().startswith(('//', '*', '/*')):
            continue
        if line.lstrip().startswith('@') and line.count('(') > line.count(')'):
            open_parens = line.count('(') - line.count(')')
            continue
        if pending:
            cm = CLASS_RE.search(line)
            if cm:
                prefix = ''
                for name, args, _ in pending:
                    if name in ('RequestMapping', 'Controller', 'Route', 'route'):
                        prefix = first_str(args)
            else:
                fm = PY_DEF.match(line) or FUNC_RE.search(line)
                sym = fm.group(1) if fm else '?'
                for name, args, ln in pending:
                    verb = None
                    for h in HTTP:
                        if name in (h, h + 'Mapping', 'Http' + h):
                            verb = h.upper()
                    if name == 'RequestMapping':  # method-level (class-level was handled above)
                        verb = 'ANY'
                    if name == 'route':
                        verb = 'ROUTE'
                    if verb:
                        body = ' '.join(lines[i - 1:i + 25])
                        cmd = CMD_RE.search(body)
                        rows.append((verb, join_path(prefix, first_str(args)), f'{rel}:{i}', sym,
                                     cmd.group(1) if cmd else '', handlers.get(cmd.group(1), '') if cmd else ''))
        pending = []
    return rows


def scan_services(repos, layers):
    """service name -> (language, code dir). mono-backend style `<lang>/services/<name>`; a repo with a root `services/`
    folder lists its sub-services; any other backend repo is one service named after the repo."""
    out = {}
    for r in repos:
        found = False
        for lang, d in (('csharp', r / 'csharp' / 'services'), ('java', r / 'java' / 'services'), ('typescript', r / 'typescript' / 'services'),
                        ('go', r / 'go' / 'services'), ('', r / 'services')):
            if d.is_dir():
                for sd in sorted(x for x in d.iterdir() if x.is_dir()):
                    out[sd.name] = (lang or layers.get(r.name) or '', f"{r.name}/{d.relative_to(r).as_posix()}/{sd.name}")
                    found = True
        if not found:
            out[r.name] = (layers.get(r.name) or '', r.name)
    return out


def run(knowledge, out, config=None):
    knowledge, out = Path(knowledge), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    all_repos = [d for d in sorted(knowledge.iterdir()) if d.is_dir() and not d.name.startswith('_') and (d / '.git').exists()]
    cfgp = Path(config) if config else knowledge.parent / 'config.json'
    kcfg = json.loads(cfgp.read_text(encoding='utf-8'))['knowledge'] if cfgp.exists() else []
    layers = {k['name']: k.get('layer') for k in kcfg}
    client = [r for r in all_repos if layers.get(r.name) in ('fe', 'mobile')]                       # FE / mobile code
    repos = [r for r in all_repos if layers.get(r.name) not in ('fe', 'mobile', 'docs')]             # server-side code
    files = {r.name: list(source_files(r)) for r in repos}

    # Command/Query -> handler class location
    handlers = {}
    for r in repos:
        for f in files[r.name]:
            m = HANDLER_FILE.match(f.name)
            if m:
                lines = read_lines(f)
                ln = next((i for i, l in enumerate(lines, 1) if CLASS_RE.search(l)), 1)
                handlers[m.group(1)] = f'{r.name}/{f.relative_to(r).as_posix()}:{ln}'

    routes, defs, tables = [], {}, []
    for r in repos:
        for f in files[r.name]:
            lines = read_lines(f)
            rel = f'{r.name}/{f.relative_to(r).as_posix()}'
            routes += scan_routes(f, lines, rel, handlers)
            cls = None
            for i, l in enumerate(lines, 1):
                cm = CLASS_RE.search(l)
                if cm:
                    cls = cm.group(1)
                for rx in (ENUM_CODE, CONST_CODE, CS_ENUM_CODE):
                    m = rx.match(l) if rx is not CONST_CODE else rx.search(l)
                    if m:
                        msg = (m.group(3) if rx is ENUM_CODE and m.lastindex >= 3 else '') or ''
                        if msg or len(m.group(2)) >= 4:
                            defs.setdefault(m.group(1), (m.group(2), f'{rel}:{i}', msg))
                        break
                for trx in TABLE_RES:
                    if f.name.endswith(('.Designer.cs', 'ModelSnapshot.cs')):  # EF migration snapshots repeat every table
                        break
                    if '_name' in trx.pattern and f.suffix != '.py':
                        continue
                    m = trx.search(l)
                    if m and re.fullmatch(r'[\w.]+', m.group(1).strip('`"')):  # skip `\`, quoted junk
                        # the annotation usually sits above its class line: look ahead a few lines first
                        ahead = next((CLASS_RE.search(x).group(1) for x in lines[i:i + 6] if CLASS_RE.search(x)), None)
                        tables.append((m.group(1).strip('`"'), (cls if ('_name' in trx.pattern or 'ToTable' in trx.pattern) else ahead) or cls or '?', f'{rel}:{i}'))

    # where each error-code constant is referenced (outside its own definition line)
    refs = defaultdict(list)
    if defs:
        rx = re.compile(r'\b(' + '|'.join(re.escape(n) for n in sorted(defs, key=len, reverse=True)) + r')\b')
        for r in repos:
            for f in files[r.name]:
                rel = f'{r.name}/{f.relative_to(r).as_posix()}'
                for i, l in enumerate(read_lines(f), 1):
                    for m in rx.finditer(l):
                        loc = f'{rel}:{i}'
                        if loc != defs[m.group(1)][1] and len(refs[m.group(1)]) < 5:
                            refs[m.group(1)].append(loc)

    def table(headers, rows, name, title, note):
        text = [f'# {title}', '', note, '', '| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
        text += ['| ' + ' | '.join(str(c).replace('|', '\\|') for c in row) + ' |' for row in rows]
        (out / name).write_text('\n'.join(text) + '\n', encoding='utf-8')

    table(['Method', 'Path', 'Handler (file:line)', 'Method name', 'Dispatches', 'Handler class (file:line)'],
          sorted(set(routes), key=lambda x: (x[1], x[0])), 'routes.md', 'Routes → handlers',
          'Generated by tools/gen_anchors.py (regex, best effort). A locator only: Read the code to confirm. Path = class prefix + method path.')
    table(['Code', 'Constant', 'Message', 'Defined at', 'Referenced at (max 5)'],
          sorted(((v[0], k, v[2], v[1], '; '.join(refs.get(k, []))) for k, v in defs.items()), key=lambda x: (int(x[0]), x[1])),
          'errors.md', 'Error codes → where raised',
          'Constants/enums with a 3-6 digit code and the places that reference them. A locator only; the constant name can be shared by several codes.')
    seen_t, uniq_t = defaultdict(int), []
    for row in sorted(set(tables)):  # at most 3 definitions per table name
        seen_t[row[0]] += 1
        if seen_t[row[0]] <= 3:
            uniq_t.append(row)
    tables = uniq_t
    table(['Table / model', 'Class', 'Defined at'], tables, 'tables.md', 'Tables → entity classes',
          'From @Table / [Table] / ToTable / Odoo _name. Read/write sites: Grep the class name.')
    branches = {k['name']: k['branch'] for k in kcfg}
    vrows = []
    for r in all_repos:
        def g(*a):
            p = subprocess.run(['git', '-C', str(r), *a], capture_output=True, text=True)
            return p.stdout.strip() if p.returncode == 0 else '?'
        vrows.append((r.name, layers.get(r.name, '?'), branches.get(r.name, '?'), g('rev-parse', 'HEAD'),
                      g('log', '-1', '--format=%cI'), g('rev-list', '--count', 'HEAD')))
    table(['Repo', 'Layer', 'Branch', 'HEAD', 'Commit date', 'Commits in clone'], vrows, 'versions.md', 'Code versions read',
          'The clone is NOT necessarily what is deployed. Backend: compare with the New Relic deployment marker of the entity. '
          'Mobile/web: compare with the app version / bundle from Bugsnag or Sentry (git_tags maps a version to a commit).')

    # ---- FE / mobile repos: UI text, API calls, screens, and the joins to the backend ----
    services = scan_services(repos, layers)
    table(['Service', 'Language', 'Code dir', 'Routes found'],
          [(n, lang, d, sum(1 for r_ in routes if r_[2].startswith(d + '/') or r_[2].startswith(d + ':'))) for n, (lang, d) in sorted(services.items())],
          'services.md', 'Backend services → code',
          'Service name as used in the gateway path prefix (/<name>/...) and New Relic entity (<prefix><name>), the language and '
          'the folder to open. "Routes found" = 0 means the route scanner does not understand that service\'s style: read the code directly.')
    scans = {r.name: anchors_client.scan_client(r, r.name) for r in client}
    i18n = [x for c in scans.values() for x in c['i18n']]
    used = defaultdict(list)
    for c in scans.values():
        for k, v in c['used_keys'].items():
            used[k] += v
    api = [x for c in scans.values() for x in c['api']]
    note_none = '' if client else ' (no FE/mobile repo with layer fe|mobile is cloned: see config.json)'
    table(['UI text', 'Locale', 'i18n key', 'Defined at', 'Used at (max 3)'],
          sorted(((t, lc, k, loc, '; '.join(anchors_client.used_refs(k, used))) for k, t, loc, lc in i18n),
                 key=lambda r: (r[0].casefold(), r[1])),
          'ui-texts.md', 'UI text → i18n key → component',
          'A ticket quotes what the user saw: Grep that text here, then open the "Used at" file. Covers RN resource files '
          '(*_vi.ts), next-intl *.translations.json and locale JSON. Repos that hard-code Vietnamese in components '
          '(e.g. web-mall) have no key table: Grep the text directly in the repo.' + note_none)
    table(['Method', 'Path as called', 'Called at (file:line)', 'In function'],
          sorted(set(api) | {('?', p_, loc, '(path string outside a call, e.g. a constants file)') for c in scans.values()
                             for p_, loc in c['literals']}, key=lambda r: (r[1], r[0])), 'api-calls.md',
          'FE / mobile → backend calls',
          'Where the client calls the backend. Base URL is not resolved: the path may lack a prefix such as /api/v1/app. '
          'See edges.md for the match to a backend route.' + note_none)
    be_routes = sorted(set(routes), key=lambda x: (x[1], x[0]))
    edges, unmatched = anchors_client.join_edges(api, be_routes, {n: d for n, (_, d) in services.items()})
    table(['Client call (file:line)', 'In function', 'Method', 'Path as called', 'Confidence', 'Backend route handler', 'Method name',
           'Dispatches', 'Handler class'], sorted(set(edges), key=lambda r: (r[3], r[0])), 'edges.md',
          'Client call → backend route → handler (cross-repo edges)',
          f'Joined on method + normalised path. high = same path; medium = the client path is the tail of the server path '
          f'(base URL not known); low = more than 3 candidates. {unmatched} client calls have no match (dynamic paths, '
          f'other backends). A lead, never proof: confirm in both repos.' + note_none)
    shown = {}
    for c in scans.values():
        for name, comp, loc in c['screens']:
            shown[(name, comp, loc)] = c['defs'].get(comp, '')
    table(['Screen / route', 'Component', 'Declared at', 'Component defined at'],
          sorted((n, c_, l, d) for (n, c_, l), d in shown.items()), 'screens.md', 'Screens and routes → components',
          'React Navigation screens, React Router routes, Next.js app/pages routes.' + note_none)
    ccr = anchors_client.client_code_refs([(r.name, r) for r in client], {v[0] for v in defs.values()})
    emap = anchors_client.error_map(defs, refs, i18n, ccr)
    table(['Code', 'Backend constant', 'Backend message', 'UI key with the same text', 'Client code mentioning the code',
           'Raised in backend at'], emap, 'error-map.md', 'Backend error code ↔ UI message',
          'Joins errors.md to the UI: a ticket quoting the UI text or the code can be followed to where the backend raises it. '
          'Only exact text matches and code mentions near "code/error/status" are listed.' + note_none)
    return {'routes': len(routes), 'errors': len(defs), 'tables': len(tables), 'services': len(services), 'ui_texts': len(i18n), 'api_calls': len(api),
            'edges': len(edges), 'screens': len(shown), 'error_map': len(emap)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--knowledge', default=str(Path(__file__).resolve().parent.parent / 'knowledge'))
    ap.add_argument('--out')
    ap.add_argument('--config', help='config.json with the knowledge list and layers (default: next to the knowledge dir)')
    a = ap.parse_args()
    print(run(a.knowledge, a.out or Path(a.knowledge) / '_anchors', a.config))
