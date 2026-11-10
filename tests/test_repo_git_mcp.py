#!/usr/bin/env python3
"""Drives tools/repo_git_mcp.py over stdio against a throwaway repo. Run: python3 tests/test_repo_git_mcp.py"""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / 'tools/repo_git_mcp.py'

base = Path(tempfile.mkdtemp(prefix='dcs-git-'))
repo = base / 'svc'
repo.mkdir()


def g(*a):
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=T', '-c', 'user.email=t@x.io', *a], check=True, capture_output=True)


g('init', '-q', '-b', 'dev')
(repo / 'src').mkdir()
f = repo / 'src/A.java'
f.write_text('class A {\n  int x = 1;\n  int y = 2;\n}\n')
g('add', '.'); g('commit', '-qm', 'add A')
f.write_text('class A {\n  int x = 1;\n  int y = null;\n}\n')
g('commit', '-qam', 'break y')
(base / 'other').mkdir()          # not a repo: must be unreachable
sha_break = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()

proc = subprocess.Popen([sys.executable, '-I', str(SERVER), '--root', str(base)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        text=True, encoding='utf-8')
_id = 0


def rpc(method, params=None, notify=False):
    global _id
    msg = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
    if not notify:
        _id += 1
        msg['id'] = _id
    proc.stdin.write(json.dumps(msg) + '\n')
    proc.stdin.flush()
    return None if notify else json.loads(proc.stdout.readline())


def tool(name, **args):
    r = rpc('tools/call', {'name': name, 'arguments': args})['result']
    return r['content'][0]['text'], r['isError']


init = rpc('initialize', {'protocolVersion': '2025-03-26'})['result']
assert init['serverInfo']['name'] == 'repogit' and init['protocolVersion'] == '2025-03-26'
rpc('notifications/initialized', notify=True)   # no reply expected; the next call must still line up
assert {t['name'] for t in rpc('tools/list')['result']['tools']} == {'git_head', 'git_log', 'git_blame', 'git_show'}

text, err = tool('git_head', repo='svc')
assert not err and sha_break in text and 'branch: dev' in text and 'commits in clone: 2' in text, text
text, err = tool('git_log', repo='svc', path='src/A.java', limit=5)
assert not err and 'break y' in text and 'add A' in text, text
text, err = tool('git_blame', repo='svc', path='src/A.java', line_start=3, line_end=3)
assert not err and sha_break[:7] in text and 'y = null' in text, text
text, err = tool('git_show', repo='svc', sha=sha_break[:10], path='src/A.java')
assert not err and '-  int y = 2;' in text and '+  int y = null;' in text, text

# everything unsafe is refused with isError, never executed
for name, args in [('git_head', {'repo': 'other'}), ('git_head', {'repo': '../'}), ('git_head', {'repo': 'svc/../other'}),
                   ('git_log', {'repo': 'svc', 'path': '../x'}), ('git_log', {'repo': 'svc', 'path': '-p'}),
                   ('git_log', {'repo': 'svc', 'path': '/etc/passwd'}),
                   ('git_log', {'repo': 'svc', 'since': '--output=/tmp/pwn'}),
                   ('git_show', {'repo': 'svc', 'sha': '--output=/tmp/pwn'}),
                   ('git_show', {'repo': 'svc', 'sha': 'HEAD; rm -rf /'}),
                   ('git_blame', {'repo': 'svc', 'path': 'src/A.java', 'line_start': 1, 'line_end': 900}),
                   ('git_push', {'repo': 'svc'})]:
    text, err = tool(name, **args)
    assert err and text.startswith('error:'), (name, args, text)
assert not (Path('/tmp/pwn').exists())
assert rpc('nonsense')['error']['code'] == -32601

proc.stdin.close()
proc.wait(timeout=10)
print('ok repo_git_mcp')
