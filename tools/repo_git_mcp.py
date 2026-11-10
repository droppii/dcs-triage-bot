#!/usr/bin/env python3
"""Read-only git history for the knowledge clones, as a minimal MCP server (stdio, no dependencies).

Tools: git_head, git_log, git_blame, git_show. Only the repos directly under --root are reachable; every git
command line is fixed here (the model only supplies validated values), so it cannot run anything else, write,
or leave --root. Blobless clones fetch missing blobs lazily, so blame/show may need network + git credentials.
Run: python3 tools/repo_git_mcp.py --root knowledge
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

MAX_OUT = 14000
SAFE_PATH = re.compile(r'^[\w@+./ \-]{1,300}$')
SAFE_DATE = re.compile(r'^[\w:+\-. ]{1,40}$')
SHA = re.compile(r'^[0-9a-fA-F]{4,40}$')
ROOT = Path('.')

TOOLS = [
    {'name': 'git_head', 'description': 'HEAD commit (sha, date, subject) and branch of a knowledge repo, plus how far its history goes back.',
     'inputSchema': {'type': 'object', 'properties': {'repo': {'type': 'string'}}, 'required': ['repo']}},
    {'name': 'git_log', 'description': 'Commits touching a path (or the whole repo). since/until accept dates like 2026-11-01 or "2 weeks ago". Max 30.',
     'inputSchema': {'type': 'object', 'properties': {'repo': {'type': 'string'}, 'path': {'type': 'string'},
                                                       'since': {'type': 'string'}, 'until': {'type': 'string'},
                                                       'limit': {'type': 'integer'}}, 'required': ['repo']}},
    {'name': 'git_blame', 'description': 'Who/which commit last changed lines line_start..line_end (max 200 lines) of a file. A commit marked ^ is a boundary of the cloned history: the real origin is older.',
     'inputSchema': {'type': 'object', 'properties': {'repo': {'type': 'string'}, 'path': {'type': 'string'},
                                                       'line_start': {'type': 'integer'}, 'line_end': {'type': 'integer'}},
                     'required': ['repo', 'path', 'line_start', 'line_end']}},
    {'name': 'git_show', 'description': 'Files changed and diff of one commit, optionally limited to a path. Output is truncated.',
     'inputSchema': {'type': 'object', 'properties': {'repo': {'type': 'string'}, 'sha': {'type': 'string'}, 'path': {'type': 'string'}},
                     'required': ['repo', 'sha']}},
]


def repo_dir(name):
    d = (ROOT / str(name)).resolve()
    if not re.fullmatch(r'[\w.\-]+', str(name)) or d.parent != ROOT.resolve() or not (d / '.git').exists():
        raise ValueError(f'unknown repo {name!r}; available: {sorted(p.name for p in ROOT.iterdir() if (p / ".git").exists())}')
    return d


def safe_path(p):
    p = str(p or '').replace('\\', '/')
    if p and (not SAFE_PATH.match(p) or p.startswith(('/', '-')) or '..' in p.split('/')):
        raise ValueError('invalid path (relative path inside the repo, no "..")')
    return p


def git(d, *args):
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'APPDATA') if k in os.environ}
    env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
    p = subprocess.run(['git', '--no-pager', '-C', str(d), *args], capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=60, env=env)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip()[:400] or f'git exited {p.returncode}')
    return p.stdout


def clip(s):
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f'\n... [truncated, {len(s) - MAX_OUT} more chars]'


def call(name, a):
    d = repo_dir(a.get('repo'))
    if name == 'git_head':
        head = git(d, 'log', '-1', '--format=%H%n%cI%n%s').splitlines()
        branch = git(d, 'rev-parse', '--abbrev-ref', 'HEAD').strip()
        n = git(d, 'rev-list', '--count', 'HEAD').strip()
        shallow = (d / '.git' / 'shallow').exists()
        return (f'sha: {head[0]}\ndate: {head[1]}\nsubject: {head[2]}\nbranch: {branch}\n'
                f'commits in clone: {n}{" (SHALLOW: older history not available)" if shallow else ""}')
    if name == 'git_log':
        args = ['log', f"-n{max(1, min(int(a.get('limit') or 15), 30))}", '--date=iso', '--format=%h | %an | %ad | %s']
        for key in ('since', 'until'):
            if a.get(key):
                if not SAFE_DATE.match(str(a[key])):
                    raise ValueError(f'invalid {key}')
                args.append(f'--{key}={a[key]}')
        path = safe_path(a.get('path'))
        return clip(git(d, *args, *(['--', path] if path else [])) or '(no commits in range; clone history may be shorter than the range)')
    if name == 'git_blame':
        path = safe_path(a.get('path'))
        s, e = int(a['line_start']), int(a['line_end'])
        if not (1 <= s <= e <= s + 199):
            raise ValueError('line range must satisfy 1 <= line_start <= line_end <= line_start+199')
        return clip(git(d, 'blame', '-w', '--date=short', '-L', f'{s},{e}', '--', path))
    if name == 'git_show':
        sha = str(a.get('sha', ''))
        if not SHA.match(sha):
            raise ValueError('invalid sha')
        path = safe_path(a.get('path'))
        return clip(git(d, 'show', '--stat', '--patch', '--format=%H%n%an | %ad%n%s%n%b', '--date=iso', sha, *(['--', path] if path else [])))
    raise ValueError(f'unknown tool {name}')


def handle(msg):
    """JSON-RPC message -> response dict, or None for notifications."""
    mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
    if mid is None:
        return None
    if method == 'initialize':
        result = {'protocolVersion': params.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'repogit', 'version': '1.0'}}
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


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    ROOT = Path(ap.parse_args().root).resolve()
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


if __name__ == '__main__':
    main()
