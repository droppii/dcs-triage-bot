#!/usr/bin/env python3
"""Run DBHub 1.5.0 (stdio) against PRODUCTION Postgres, read-only. Never started by the brain directly: bot.py starts
tools/dbhub_guard_mcp.py, which starts this and filters every SQL (config/db_allowlist.json is the single list of
sources, databases, tables and columns; a source that is not in it cannot be reached).

Same limits as the shared VM and tools/dbhub-stg.py: readonly = true, max_rows = 100, query_timeout = 15 s.
Credentials are separate from staging on purpose (PROD_DB_*, never DB_*), from env or from the file named in
PROD_DB_ENV_FILE. They must belong to a role that can only SELECT on masked views of a read replica: docs/prod-db-role.sql.
Never printed; the temp config is deleted on exit.
"""
import argparse, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # python -I does not put the script's directory on sys.path
import sqlguard

DBHUB = Path(__file__).parent / 'dbhub/node_modules/@bytebase/dbhub/dist/index.js'
KEYS = {'PROD_DB_HOST', 'PROD_DB_PORT', 'PROD_DB_USER', 'PROD_DB_PASSWORD'}


def read_env():
    env = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
    src = os.environ.get('PROD_DB_ENV_FILE')
    if KEYS - env.keys() and src:
        for line in Path(src).expanduser().read_text(encoding='utf-8').splitlines():
            m = re.match(r'^(PROD_DB_(?:HOST|PORT|USER|PASSWORD))=(.*)$', line.strip())
            if m:
                env.setdefault(m.group(1), m.group(2).strip().strip('"\''))
    missing = KEYS - env.keys()
    if missing:
        sys.exit(f'dbhub-prod: missing {sorted(missing)}; set them in the environment or in the file named by PROD_DB_ENV_FILE')
    return env


def render(env, allow):
    blocks = []
    for source_id, s in allow.items():
        values = {'id': source_id, 'type': 'postgres', 'host': env['PROD_DB_HOST'], 'port': int(env['PROD_DB_PORT']),
                  'database': s['database'], 'user': env['PROD_DB_USER'], 'password': env['PROD_DB_PASSWORD'],
                  'sslmode': 'require', 'lazy': True, 'pool_max_connections': 1, 'query_timeout': 15, 'connection_timeout': 20}
        blocks.append('[[sources]]\n' + '\n'.join(k + ' = ' + json.dumps(v) for k, v in values.items()))
        blocks.append('[[tools]]\nname = "execute_sql"\nsource = ' + json.dumps(source_id) + '\nreadonly = true\nmax_rows = 100')
    return '\n\n'.join(blocks) + '\n'


def clean_stale_configs(max_age_s=3600):
    """A killed run leaves its temp dir (config with the DB password) behind: remove any dbhub-prod-* older than an hour."""
    root = Path(tempfile.gettempdir())
    for d in root.glob('dbhub-prod-*'):
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > max_age_s:
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allowlist', required=True)
    a = ap.parse_args()
    try:
        allow = sqlguard.load_allowlist(a.allowlist)
    except sqlguard.GuardError as e:
        sys.exit(f'dbhub-prod: {e}')
    clean_stale_configs()
    with tempfile.TemporaryDirectory(prefix='dbhub-prod-') as d:
        path = Path(d) / 'dbhub.toml'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(render(read_env(), allow))
        sys.exit(subprocess.call(['node', str(DBHUB), '--transport', 'stdio', '--config', str(path)]))


if __name__ == '__main__':
    main()
