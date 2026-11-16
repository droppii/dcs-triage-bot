#!/usr/bin/env python3
"""MCP proxy in front of DBHub: the only way the brain reaches a PRODUCTION database.

    brain (claude -p)  --MCP stdio-->  dbhub_guard_mcp.py  --MCP stdio-->  DBHub (readonly, 100 rows, 15 s)  --> replica

Every `execute_sql_<source>` call is checked by sqlguard.py (allowlist of tables/columns/functions, config/db_allowlist.json)
and DBHub receives only the SQL regenerated from the checked tree. Tools and sources outside the allowlist are hidden
and refused. A refused call returns an error that tells the model what is allowed. `allowed_schema` lists the allowed
tables and columns (information_schema is not reachable). Every decision is appended to logs/db-audit-YYYYMMDD.jsonl (one file a day, purged with the run logs; string literals
are replaced by '?', so the audit shows the shape of each query, never the names or addresses it filtered on).
Fails closed: if the allowlist or sqlglot cannot be loaded, this server does not start and the brain has no database.

Run: python3 tools/dbhub_guard_mcp.py --allowlist config/db_allowlist.json --upstream tools/dbhub-prod.py
"""
import argparse, json, os, signal, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # python -I does not put the script's directory on sys.path
import guard
import sqlguard

SCHEMA_TOOL = {
    'name': 'allowed_schema',
    'description': 'Tables and columns this bot may query on a source (information_schema is not available). '
                   'Call it before writing SQL. Arguments: source (e.g. prod_order).',
    'inputSchema': {'type': 'object', 'properties': {'source': {'type': 'string'}}, 'required': ['source']}}
PREFIX = 'execute_sql_'
write_lock = threading.Lock()
audit_lock = threading.Lock()


class Proxy:
    def __init__(self, allowlist, upstream_cmd, audit_path):
        self.allow = allowlist
        self.audit_path = Path(audit_path) if audit_path else None
        self.pending_list = set()      # ids of tools/list requests, whose responses are filtered
        self.inflight = set()          # ids of requests sent to DBHub and not answered yet
        self.up = subprocess.Popen(upstream_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0,
                                   start_new_session=(os.name != 'nt'))     # own process group: the whole tree can be killed

    # --- plumbing ---
    @staticmethod
    def send(stream, obj):
        data = (json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8')
        with write_lock:
            stream.write(data)
            stream.flush()

    def to_client(self, obj):
        self.send(sys.stdout.buffer, obj)

    def to_upstream(self, obj):
        """Forward to DBHub. If DBHub is gone the caller must still get an answer, never a hang or a crashed proxy."""
        if obj.get('id') is not None and 'method' in obj:
            self.inflight.add(obj['id'])
        try:
            self.send(self.up.stdin, obj)
        except (OSError, ValueError):
            self.inflight.discard(obj.get('id'))
            if obj.get('id') is not None:
                self.to_client(self.result(obj['id'], 'the database tool is unavailable (DBHub stopped); report it, do not retry', True))

    def audit(self, source, verdict, reason, sql, run_sql=None):
        if not self.audit_path:
            return
        shape = lambda s: guard.sanitize(guard.mask_literals(s))[:2000]
        row = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'source': source, 'verdict': verdict,
               'reason': shape(reason)[:300], 'sql': shape(sql)}
        if run_sql:
            row['run'] = shape(run_sql)
        path = self.audit_path.with_name(f'{self.audit_path.stem}-{time.strftime("%Y%m%d")}{self.audit_path.suffix}')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with audit_lock, open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')
        except OSError as e:
            print(f'audit log failed: {e}', file=sys.stderr)

    def source_of(self, tool):
        """DBHub names the tool `execute_sql_<source>` when it serves several sources, but plain `execute_sql` when there is
        only one (verified against DBHub 1.5.0). None = not a SQL tool of an allowed source."""
        if tool == 'execute_sql':
            return next(iter(self.allow)) if len(self.allow) == 1 else None
        src = tool[len(PREFIX):] if tool.startswith(PREFIX) else None
        return src if src in self.allow else None

    @staticmethod
    def result(rid, text, error=False):
        return {'jsonrpc': '2.0', 'id': rid, 'result': {'content': [{'type': 'text', 'text': text}], 'isError': error}}

    # --- client -> upstream ---
    def from_client(self, msg):
        method, rid = msg.get('method'), msg.get('id')
        if method == 'tools/list' and rid is not None:
            self.pending_list.add(rid)
        elif method == 'tools/call':
            return self.on_call(msg)
        self.to_upstream(msg)

    def on_call(self, msg):
        rid = msg.get('id')
        params = msg.get('params') if isinstance(msg.get('params'), dict) else {}
        name, args = str(params.get('name', '')), params.get('arguments')
        if not isinstance(args, dict):                 # a malformed call is refused below, it must not crash the proxy
            args = {}
        if name == SCHEMA_TOOL['name']:
            cfg = self.allow.get(str(args.get('source', '')))
            text = (f"source {args.get('source')!r} unknown. Sources: {', '.join(sorted(self.allow))}" if not cfg
                    else sqlguard.describe(cfg))
            return self.to_client(self.result(rid, text, error=not cfg))
        source = self.source_of(name)
        cfg = self.allow.get(source) if source else None
        if not cfg:
            self.audit(name, 'refused', 'tool or source not allowed', json.dumps(sorted(args)))      # argument names only
            return self.to_client(self.result(rid, f'tool {name!r} is not available. Use allowed_schema, or '
                                                   f'{PREFIX}<source> with a source from: {", ".join(sorted(self.allow))}', True))
        sql = args.get('sql')
        try:
            run_sql = sqlguard.check(sql, cfg)
        except sqlguard.GuardError as e:
            self.audit(source, 'refused', str(e), sql)
            return self.to_client(self.result(rid, f'SQL refused by the production guard: {e}', True))
        except Exception as e:                       # a bug in the checker must refuse, never pass the SQL through
            self.audit(source, 'refused', f'guard error {type(e).__name__}: {e}', sql)
            return self.to_client(self.result(rid, 'SQL refused: the production guard could not check it', True))
        self.audit(source, 'allowed', '', sql, run_sql)
        self.to_upstream({**msg, 'params': {**params, 'arguments': {**args, 'sql': run_sql}}})

    # --- upstream -> client ---
    def from_upstream(self, msg):
        rid = msg.get('id')
        self.inflight.discard(rid)
        if rid in self.pending_list and 'result' in msg:
            self.pending_list.discard(rid)
            tools = [t for t in (msg['result'].get('tools') or []) if self.source_of(str(t.get('name', '')))]
            msg['result']['tools'] = tools + [SCHEMA_TOOL]
        self.to_client(msg)

    def pump_upstream(self):
        for line in self.up.stdout:
            try:
                self.from_upstream(json.loads(line.decode('utf-8')))
            except (ValueError, UnicodeDecodeError):
                print(f'upstream sent a non-JSON line, dropped: {line[:80]!r}', file=sys.stderr)
        for rid in list(self.inflight):        # DBHub stopped with requests unanswered: say so instead of leaving the caller hanging
            self.to_client(self.result(rid, 'the database tool stopped before it answered; report it, do not retry', True))
            self.inflight.discard(rid)

    def kill_tree(self):
        """DBHub (node) is a child of the wrapper script: killing only the wrapper would orphan it and keep its config on disk."""
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/F', '/T', '/PID', str(self.up.pid)], capture_output=True, timeout=10)
            else:
                os.killpg(self.up.pid, signal.SIGKILL)
        except Exception:
            self.up.kill()

    def run(self):
        pump = threading.Thread(target=self.pump_upstream, daemon=True)
        pump.start()
        for line in sys.stdin.buffer:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(msg, dict):
                self.from_client(msg)
        deadline = time.time() + 15            # the client closed its side: let requests already sent to DBHub finish first
        while self.inflight and self.up.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        try:
            self.up.stdin.close()
            self.up.wait(timeout=5)
        except Exception:
            self.kill_tree()
        pump.join(timeout=5)                # let the last upstream answers reach the client before exiting


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allowlist', required=True)
    ap.add_argument('--upstream', required=True, help='script that runs DBHub for the same allowlist (tools/dbhub-prod.py)')
    ap.add_argument('--audit', default=str(ROOT / 'logs' / 'db-audit.jsonl'))
    a = ap.parse_args()
    try:
        allow = sqlguard.load_allowlist(a.allowlist)
    except sqlguard.GuardError as e:
        sys.exit(f'dbhub_guard_mcp: refusing to start: {e}')
    Proxy(allow, [sys.executable, '-I', a.upstream, '--allowlist', a.allowlist], a.audit).run()


if __name__ == '__main__':
    main()
