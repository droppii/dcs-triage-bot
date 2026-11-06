#!/usr/bin/env python3
"""Run DBHub 1.5.0 (stdio) against STAGING Postgres, read-only, same limits as the shared VM.

Mirrors engineering-knowledge handbook/ai-context/agent-connections/azure-host/postgres-observe.py:
readonly = true, max_rows = 100, query_timeout = 15s. Creds come from the automation-test .env
(staging service creds via the local cloudflared tunnel); never printed, temp config deleted on exit.
"""
import json, os, re, subprocess, sys, tempfile
from pathlib import Path

ENV = Path.home() / 'Documents/02_Playwright/automation-test/automation-test/.env'
DBHUB = Path(__file__).parent / 'dbhub/node_modules/@bytebase/dbhub/dist/index.js'
# source id -> staging database (vn_stg_*). Add a line here to expose another service DB.
DATABASES = {
    'stg_order': 'vn_stg_order_service_20241119',
    'stg_product': 'vn_stg_product_service_20241119',
    'stg_promotion': 'vn_stg_promotion_service_20241119',
    'stg_user': 'vn_stg_user_service_20241119',
    'stg_identity': 'vn_stg_identity_service_20241119',
    'stg_chat': 'vn_stg_chat_service',
    'stg_odoo_service': 'vn_stg_odoo_service',
    'stg_notifier': 'vn_stg_notifier_service_20241119',
    'stg_marketing': 'vn_stg_marketing_service_20241119_2',
}


def read_env():
    # The .env can't be sourced whole (unquoted values); take only the DB keys.
    env = {}
    for line in ENV.read_text().splitlines():
        m = re.match(r'^(DB_(?:HOST|PORT|USER|PASSWORD))=(.*)$', line.strip())
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"\'')
    missing = {'DB_HOST', 'DB_PORT', 'DB_USER', 'DB_PASSWORD'} - env.keys()
    if missing:
        sys.exit(f'dbhub-stg: missing {sorted(missing)} in {ENV}')
    return env


def render(env):
    blocks = []
    for source_id, db in DATABASES.items():
        values = {'id': source_id, 'type': 'postgres', 'host': env['DB_HOST'], 'port': int(env['DB_PORT']),
                  'database': db, 'user': env['DB_USER'], 'password': env['DB_PASSWORD'], 'sslmode': 'require',
                  'lazy': True, 'pool_max_connections': 1, 'query_timeout': 15, 'connection_timeout': 20}
        blocks.append('[[sources]]\n' + '\n'.join(k + ' = ' + json.dumps(v) for k, v in values.items()))
        blocks.append('[[tools]]\nname = "execute_sql"\nsource = ' + json.dumps(source_id) + '\nreadonly = true\nmax_rows = 100')
    return '\n\n'.join(blocks) + '\n'


def main():
    with tempfile.TemporaryDirectory(prefix='dbhub-stg-') as d:
        path = Path(d) / 'dbhub.toml'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(render(read_env()))
        sys.exit(subprocess.call(['node', str(DBHUB), '--transport', 'stdio', '--config', str(path)]))


if __name__ == '__main__':
    main()
