#!/usr/bin/env python3
"""Local code index so the brain does not have to read the code base.

Why: Grep over ~1.4 GB of clones followed by Read of whole files costs tokens in proportion to the code base. An index built
once makes a lookup cost proportional to the question: the brain asks `search`, gets a few ranked hits (repo/file:lines plus a
short snippet, never a whole file) and only then Reads the few lines it needs. Pattern taken from how the Cerebro index is used
in the Heimdall impact-analysis flow: search first, investigate per module, only real code counts as evidence, docs only orient,
stale hits are flagged, results are compact.

SQLite FTS5 (stdlib only): chunks of ~40 lines of the knowledge clones, identifiers split (ClaimVoucherCommandHandler ->
claim voucher command handler), Vietnamese diacritics ignored. Read-only at query time.

  python3 tools/code_index.py build  [--knowledge knowledge] [--config config.json] [--db knowledge/_index/code.sqlite]
  python3 tools/code_index.py search "Khuyến mãi không tồn tại" [--repos mono-backend] [--top 5]
  python3 tools/code_index.py investigate ClaimVoucherCommandHandler
  python3 tools/code_index.py serve  --db ... --root knowledge          (MCP over stdio: tools search, investigate)
"""
import argparse, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

CODE_EXT = {'.java', '.kt', '.cs', '.ts', '.tsx', '.js', '.jsx', '.py', '.go'}
DOC_EXT = {'.md'}
LOCALE_DIRS = {'locales', 'locale', 'i18n', 'lang', 'langs', 'translations', 'translation', 'messages', 'resources'}
SKIP_DIRS = {'.git', 'node_modules', 'build', 'dist', '.next', '.yarn', 'Pods', '__pycache__', 'bin', 'obj', 'target', 'vendor',
             'Migrations', 'migrations', 'coverage', 'ios', 'android', 'public', '.turbo', 'storybook-static', '__mocks__',
             'test', 'tests', '__tests__', 'Test', 'Tests', 'e2e', '.claude', 'plans'}
MAX_FILE, MAX_LOCALE = 300_000, 3_000_000
CHUNK, STRIDE = 40, 30
STALE_DAYS = 30
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
  repo UNINDEXED, path UNINDEXED, ls UNINDEXED, le UNINDEXED, layer UNINDEXED, st UNINDEXED, text UNINDEXED, toks,
  tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS symbols(name TEXT, kind TEXT, repo TEXT, path TEXT, line INTEGER);
CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
CREATE TABLE IF NOT EXISTS meta(repo TEXT PRIMARY KEY, sha TEXT, built_at TEXT, layer TEXT, files INTEGER, chunks INTEGER);
"""
SYM_RES = [
    (re.compile(r'\b(class|interface|enum|object|record|struct|trait)\s+([A-Z]\w+)'), 1, 2),
    (re.compile(r'\b(fun|def|func|function)\s+(\w+)\s*\('), 1, 2),
    (re.compile(r'^\s*(?:export\s+)?(const)\s+([A-Za-z_]\w+)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\(|\w+\s*=>)'), 1, 2),
    (re.compile(r'^\s*(?:public|private|protected|internal)\s+(?:static\s+|async\s+|final\s+|override\s+|virtual\s+)*[\w<>\[\],.?]+\s+(\w+)\s*\('), None, 1),
]
CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|_')
WORD = re.compile(r'\w+', re.UNICODE)


def ident_tokens(text, cap=900):
    """Words of `text` lowercased, plus the parts of camelCase / snake_case identifiers; unique, in order."""
    seen, out = set(), []
    for w in WORD.findall(text):
        parts = [p.lower() for p in CAMEL.split(w) if len(p) > 1] if CAMEL.search(w) else []
        for t in (w.lower(), *parts):
            if t not in seen and len(t) > 1:
                seen.add(t)
                out.append(t)
        if len(out) >= cap:
            break
    return ' '.join(out)


def git(d, *args):
    try:
        p = subprocess.run(['git', '-C', str(d), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''


# ---------- build ----------

def source_files(repo):
    """(path, source_type): code, doc or locale (UI text)."""
    for dp, dns, fns in os.walk(repo):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        parts = {p.lower() for p in Path(dp).relative_to(repo).parts}
        for fn in fns:
            p = Path(dp) / fn
            ext = p.suffix
            if ext in CODE_EXT and not fn.endswith(('.d.ts', '.min.js', '.Designer.cs')):
                yield p, 'code'
            elif ext in DOC_EXT:
                yield p, 'docs'
            elif ext == '.json' and (fn.endswith('.translations.json') or parts & LOCALE_DIRS):
                yield p, 'locale'


def chunks_of(lines):
    for s in range(0, max(len(lines), 1), STRIDE):
        block = lines[s:s + CHUNK]
        if block:
            yield s + 1, s + len(block), block
        if s + CHUNK >= len(lines):
            break


def build(knowledge, config=None, db=None, only=None, log=print):
    knowledge = Path(knowledge)
    cfgp = Path(config) if config else knowledge.parent / 'config.json'
    layers = {k['name']: k.get('layer') for k in json.loads(cfgp.read_text(encoding='utf-8'))['knowledge']} if cfgp.exists() else {}
    db = Path(db or knowledge / '_index' / 'code.sqlite')
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_suffix('.building')
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute('PRAGMA journal_mode=OFF')
    con.execute('PRAGMA synchronous=OFF')
    con.executescript(SCHEMA)
    stats = {}
    for repo in sorted(d for d in knowledge.iterdir() if d.is_dir() and not d.name.startswith('_') and (d / '.git').exists()):
        if only and repo.name not in only:
            continue
        t0, nfiles, nchunks, rows, syms = time.time(), 0, 0, [], []
        layer = layers.get(repo.name) or ''
        for p, st in source_files(repo):
            try:
                if p.stat().st_size > (MAX_LOCALE if st == 'locale' else MAX_FILE):
                    continue
                text = p.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            lines = text.splitlines()
            if not lines or (st == 'code' and len(text) / len(lines) > 250):   # minified / generated
                continue
            rel = p.relative_to(repo).as_posix()
            nfiles += 1
            if st == 'code':
                for i, l in enumerate(lines, 1):
                    for rx, kg, ng in SYM_RES:
                        m = rx.search(l)
                        if m:
                            syms.append((m.group(ng), m.group(kg) if kg else 'method', repo.name, rel, i))
                            break
            for ls, le, block in chunks_of(lines):
                body = '\n'.join(block)
                rows.append((repo.name, rel, ls, le, layer, st, body, ident_tokens(rel + ' ' + body)))
            if len(rows) >= 5000:
                con.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)', rows)
                nchunks += len(rows)
                rows = []
        if rows:
            con.executemany('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)', rows)
            nchunks += len(rows)
        con.executemany('INSERT INTO symbols VALUES (?,?,?,?,?)', syms)
        con.execute('INSERT OR REPLACE INTO meta VALUES (?,?,?,?,?,?)',
                    (repo.name, git(repo, 'rev-parse', 'HEAD'), datetime.now(timezone.utc).isoformat(timespec='seconds'), layer, nfiles, nchunks))
        stats[repo.name] = (nfiles, nchunks, len(syms))
        log(f'  {repo.name}: {nfiles} files, {nchunks} chunks, {len(syms)} symbols, {time.time() - t0:.0f}s')
    con.commit()
    con.execute("INSERT INTO chunks(chunks) VALUES('optimize')")
    con.commit()
    con.close()
    os.replace(tmp, db)
    return stats


# ---------- query ----------

def connect(db):
    if not Path(db).exists():
        raise FileNotFoundError(f'index not built: {db} (run tools/setup.sh or tools/code_index.py build)')
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    return con


def fts_expr(query, op):
    """User text -> safe FTS5 expression: only words, each quoted, so FTS operators / punctuation in the query cannot break it."""
    toks = ident_tokens(query).split()
    toks = sorted(set(toks), key=lambda t: -len(t))[:10]          # longest (most specific) tokens first
    return f' {op} '.join('"' + t + '"' for t in toks), toks


def stale_of(con, repo, root, cache):
    """None if fresh, else why the hit may be out of date: index older than 30 days, or the clone moved on after the build."""
    if repo not in cache:
        row = con.execute('SELECT sha, built_at FROM meta WHERE repo=?', (repo,)).fetchone()
        why = None
        if row:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[1])).days
            except ValueError:
                age = 0
            head = git(Path(root) / repo, 'rev-parse', 'HEAD') if root else ''
            if age > STALE_DAYS:
                why = f'STALE: index built {age} days ago'
            elif head and row[0] and head != row[0]:
                why = 'STALE: clone changed after the index was built'
        cache[repo] = why
    return cache[repo]


def best_snippet(text, ls, qtoks, width=6, max_chars=500):
    """Lines around the line that mentions most query tokens: (line number, snippet)."""
    lines = text.split('\n')
    score = [sum(1 for t in qtoks if t in ident_tokens(l).split()) for l in lines]
    b = max(range(len(lines)), key=lambda i: score[i])
    lo, hi = max(0, b - 1), min(len(lines), b + width - 1)
    out, n = [], 0
    for i in range(lo, hi):
        s = f'{ls + i}: {lines[i].rstrip()[:160]}'
        if n + len(s) > max_chars:
            break
        out.append(s)
        n += len(s) + 1
    return ls + b, '\n'.join(out)


def search(con, query, repos=None, layers=None, types=None, top=8, max_chars=500, root=None, exact=False):
    """-> list of hits {repo, path, line, ls, le, layer, type, score, snippet, stale}. One hit per file, best chunk, ranked by BM25;
    docs are discounted (code is what counts as evidence). exact=True looks the single identifier up as one token: no splitting into
    words, no relaxing to OR (used by investigate, where "files that mention these words" would be noise)."""
    where, args = '', []
    for col, vals in (('repo', repos), ('layer', layers), ('st', types)):
        if vals:
            where += f' AND {col} IN ({",".join("?" * len(vals))})'
            args += list(vals)
    rows = []
    for op in (('AND',) if exact else ('AND', 'OR')):  # precise first; relax when the question has extra words
        if exact:
            word = query.strip().lower()
            expr, qtoks = ('"' + word + '"', [word]) if re.fullmatch(r'\w+', word) else ('', [])
        else:
            expr, qtoks = fts_expr(query, op)
        if not expr:
            return []
        rows = con.execute(f'SELECT repo, path, ls, le, layer, st, text, bm25(chunks) AS r FROM chunks WHERE chunks MATCH ?{where} '
                           f'ORDER BY r LIMIT ?', [expr, *args, max(top * 8, 40)]).fetchall()
        if exact or len({(r[0], r[1]) for r in rows}) >= min(top, 3):
            break
    rows.sort(key=lambda r: r[7] * (0.7 if r[5] == 'docs' else 1.0))             # bm25 is negative: closer to 0 = worse
    hits, seen, cache = [], set(), {}
    pinned = []
    single = query.strip()
    if re.fullmatch(r'[\w.$]+', single):          # an identifier: where it is DEFINED outranks files that merely mention it
        name = single.split('.')[-1]
        wr, wa = ('', [])
        if repos:
            wr, wa = f' AND repo IN ({",".join("?" * len(repos))})', list(repos)
        for repo, path, line in con.execute(f'SELECT repo, path, line FROM symbols WHERE name=?{wr} LIMIT 4', [name, *wa]).fetchall():
            r = con.execute('SELECT repo, path, ls, le, layer, st, text, 0 FROM chunks WHERE repo=? AND path=? AND ls<=? AND le>=? LIMIT 1',
                            (repo, path, line, line)).fetchone()
            if r and (not layers or r[4] in layers) and (not types or r[5] in types):
                pinned.append(r)
    for repo, path, ls, le, layer, st, text, rank in pinned + list(rows):
        if (repo, path) in seen:
            continue
        seen.add((repo, path))
        line, snip = best_snippet(text, ls, qtoks, max_chars=max_chars)
        hits.append({'repo': repo, 'path': path, 'line': line, 'ls': ls, 'le': le, 'layer': layer, 'type': st,
                     'score': round(-rank, 2), 'snippet': snip, 'stale': stale_of(con, repo, root, cache)})
        if len(hits) >= top:
            break
    return hits


def format_hits(hits, header=''):
    if not hits:
        return (header + '\n' if header else '') + '(no hits: try other words from the ticket, the exact error text, an endpoint or a class name; the index only holds the cloned repos)'
    out = [header] if header else []
    for i, h in enumerate(hits, 1):
        flag = f" [{h['stale']}]" if h.get('stale') else ''
        out.append(f"{i}. {h['repo']}/{h['path']}:{h['ls']}-{h['le']} [{h['layer'] or '?'}/{h['type']}] best line {h['line']} score {h['score']}{flag}")
        out.append('\n'.join('   ' + l for l in h['snippet'].split('\n')))
    out.append('Only type=code is evidence; docs and locale orient. Read just the lines you need (offset/limit) before relying on a hit.')
    return '\n'.join(out)


def investigate(con, symbol, repos=None, top=8, root=None):
    """Definition(s), where else the identifier appears, and the latest commits of the defining file."""
    sym = symbol.strip()
    if not re.fullmatch(r'[\w.$]+', sym):
        raise ValueError('symbol must be an identifier')
    sym = sym.split('.')[-1]
    wr, wa = '', []
    if repos:
        wr, wa = f' AND repo IN ({",".join("?" * len(repos))})', list(repos)
    defs = con.execute(f'SELECT name, kind, repo, path, line FROM symbols WHERE name=?{wr} LIMIT 6', [sym, *wa]).fetchall()
    def_files = {(d[2], d[3]) for d in defs}
    refs = search(con, sym, repos=repos, types=['code'], top=top + len(def_files), root=root, exact=True)
    refs = [h for h in refs if (h['repo'], h['path']) not in def_files][:top]
    lines = []
    for name, kind, repo, path, line in defs:
        lines.append(f'definition: {kind} {name} at {repo}/{path}:{line}')
    if not defs:
        lines.append(f'definition: none indexed for {sym} (it may be a framework symbol, a constant, or in an uncloned repo)')
    if refs:
        lines.append('used in:')
        lines += [f"  {h['repo']}/{h['path']}:{h['line']}  {h['snippet'].split(chr(10))[0].strip()[:150]}" +
                  (f" [{h['stale']}]" if h['stale'] else '') for h in refs]
    if defs and root:
        d = defs[0]
        log = git(Path(root) / d[2], 'log', '-n', '3', '--format=%h %cs %s', '--', d[3])
        if log:
            lines.append(f'recent commits of {d[3].rsplit("/", 1)[-1]} (clone history is short):')
            lines += ['  ' + l[:150] for l in log.splitlines()]
    return '\n'.join(lines)


# ---------- MCP (stdio, no dependencies) ----------

TOOLS = [
    {'name': 'search', 'description': 'Ranked lookup in the pre-built index of the cloned code (backend, web, mobile) and docs. Use this BEFORE Grep or Read: '
                                      'query with the exact UI text, error text/code, endpoint path, class/function/table name. Returns at most top_k hits as '
                                      'repo/file:lines + a short snippet (never whole files). Only type=code hits are evidence; docs/locale orient. '
                                      'Hits flagged STALE must be re-read before use.',
     'inputSchema': {'type': 'object', 'properties': {
         'query': {'type': 'string'}, 'repos': {'type': 'array', 'items': {'type': 'string'}},
         'layers': {'type': 'array', 'items': {'type': 'string', 'enum': ['be', 'odoo', 'fe', 'mobile', 'docs']}},
         'types': {'type': 'array', 'items': {'type': 'string', 'enum': ['code', 'docs', 'locale']}},
         'top_k': {'type': 'integer'}, 'max_chars': {'type': 'integer'}}, 'required': ['query']}},
    {'name': 'investigate', 'description': 'For one identifier (class, function, constant): where it is defined, which other files use it, and the latest commits of '
                                           'the defining file. One call per module, after search found it.',
     'inputSchema': {'type': 'object', 'properties': {'symbol': {'type': 'string'}, 'repos': {'type': 'array', 'items': {'type': 'string'}},
                                                      'top_k': {'type': 'integer'}}, 'required': ['symbol']}},
]
DB, ROOT = None, None


def call(name, a):
    con = connect(DB)
    try:
        if name == 'search':
            top = max(1, min(int(a.get('top_k') or 6), 15))
            hits = search(con, str(a.get('query', '')), a.get('repos'), a.get('layers'), a.get('types'), top,
                          max(150, min(int(a.get('max_chars') or 500), 1200)), ROOT)
            return format_hits(hits)
        if name == 'investigate':
            return investigate(con, str(a.get('symbol', '')), a.get('repos'), max(1, min(int(a.get('top_k') or 8), 15)), ROOT)
        raise ValueError(f'unknown tool {name}')
    finally:
        con.close()


def handle(msg):
    mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
    if mid is None:
        return None
    if method == 'initialize':
        result = {'protocolVersion': params.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'codeindex', 'version': '1.0'}}
    elif method == 'tools/list':
        result = {'tools': TOOLS}
    elif method == 'tools/call':
        try:
            text, err = call(params.get('name'), params.get('arguments') or {}), False
        except Exception as e:
            text, err = f'error: {e}', True
        result = {'content': [{'type': 'text', 'text': text}], 'isError': err}
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'method not found: {method}'}}
    return {'jsonrpc': '2.0', 'id': mid, 'result': result}


def serve(db, root):
    global DB, ROOT
    DB, ROOT = db, root
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            continue
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['build', 'search', 'investigate', 'serve'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--knowledge', '--root', dest='knowledge', default=str(Path(__file__).resolve().parent.parent / 'knowledge'))
    ap.add_argument('--config')
    ap.add_argument('--db')
    ap.add_argument('--repos', nargs='*')
    ap.add_argument('--top', type=int, default=6)
    ap.add_argument('--only', nargs='*', help='build only these repos')
    a = ap.parse_args()
    db = a.db or str(Path(a.knowledge) / '_index' / 'code.sqlite')
    if a.cmd == 'build':
        print(build(a.knowledge, a.config, db, set(a.only or [])))
    elif a.cmd == 'serve':
        serve(db, a.knowledge)
    else:
        global DB, ROOT
        DB, ROOT = db, a.knowledge
        con = connect(db)
        print(format_hits(search(con, a.arg or '', a.repos, None, None, a.top, 500, a.knowledge)) if a.cmd == 'search'
              else investigate(con, a.arg or '', a.repos, a.top, a.knowledge))


if __name__ == '__main__':
    main()
