#!/usr/bin/env python3
"""Generate a locate-the-code index from the knowledge clones, so the brain can go
route / error code / table -> handler file:line with one Grep instead of guessing.

Writes knowledge/_anchors/{routes,errors,tables,versions}.md. Static, regex-based and best-effort: it is a
*locator*, never evidence (the brain must still Read the code). Run by tools/setup.sh; safe to re-run.
Run: python3 tools/gen_anchors.py [--knowledge knowledge] [--out knowledge/_anchors]
"""
import argparse, json, os, re, subprocess
from collections import defaultdict
from pathlib import Path

SRC_EXT = {'.java', '.kt', '.cs', '.ts', '.py'}
SKIP_DIRS = {'.git', 'node_modules', 'build', 'target', 'bin', 'obj', 'dist', '__pycache__', 'test', 'tests', '__tests__',
             'Test', 'Tests', 'migrations', 'Migrations', 'generated'}
HTTP = ('Get', 'Post', 'Put', 'Delete', 'Patch')
ANNOT = re.compile(r'^\s*(?:@(?P<j>\w+)(?:\((?P<ja>.*)\))?|\[(?P<c>\w+)(?:\((?P<ca>.*?)\))?\])\s*$')
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
             re.compile(r'_name\s*=\s*[\'"]([\w.]+)[\'"]')]  # last one: Odoo model name


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
    rows, pending, prefix = [], [], ''
    for i, line in enumerate(lines, 1):
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


def run(knowledge, out):
    knowledge, out = Path(knowledge), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    repos = [d for d in sorted(knowledge.iterdir()) if d.is_dir() and not d.name.startswith('_') and (d / '.git').exists()]
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
                        defs.setdefault(m.group(1), (m.group(2), f'{rel}:{i}', (m.group(3) if rx is ENUM_CODE and m.lastindex >= 3 else '') or ''))
                        break
                for trx in TABLE_RES:
                    m = trx.search(l)
                    if m:  # the annotation usually sits above its class line: look ahead a few lines first
                        ahead = next((CLASS_RE.search(x).group(1) for x in lines[i:i + 6] if CLASS_RE.search(x)), None)
                        tables.append((m.group(1), (cls if ('_name' in trx.pattern or 'ToTable' in trx.pattern) else ahead) or cls or '?', f'{rel}:{i}'))

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
    table(['Table / model', 'Class', 'Defined at'], sorted(set(tables)), 'tables.md', 'Tables → entity classes',
          'From @Table / [Table] / ToTable / Odoo _name. Read/write sites: Grep the class name.')
    cfg = Path(knowledge).parent / 'config.json'
    branches = {k['name']: k['branch'] for k in json.loads(cfg.read_text(encoding='utf-8'))['knowledge']} if cfg.exists() else {}
    vrows = []
    for r in repos:
        def g(*a):
            p = subprocess.run(['git', '-C', str(r), *a], capture_output=True, text=True)
            return p.stdout.strip() if p.returncode == 0 else '?'
        vrows.append((r.name, branches.get(r.name, '?'), g('rev-parse', 'HEAD'), g('log', '-1', '--format=%cI'),
                      g('rev-list', '--count', 'HEAD')))
    table(['Repo', 'Branch', 'HEAD', 'Commit date', 'Commits in clone'], vrows, 'versions.md', 'Code versions read',
          'The clone is NOT necessarily what is deployed. Compare with the New Relic deployment marker of the entity.')
    return {'routes': len(routes), 'errors': len(defs), 'tables': len(tables)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--knowledge', default=str(Path(__file__).resolve().parent.parent / 'knowledge'))
    ap.add_argument('--out')
    a = ap.parse_args()
    print(run(a.knowledge, a.out or Path(a.knowledge) / '_anchors'))
