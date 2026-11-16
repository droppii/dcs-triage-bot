"""Allowlist guard for SQL the brain sends to a PRODUCTION database. Pure functions, no network.

Why: DBHub's readonly=true only stops writes. It does not stop `SELECT phone FROM users`. The strongest control is the DB
account itself (read-only role on a replica, SELECT only on masked views: docs/prod-db-role.sql). This module is the second,
independent layer, so a mistake in one of them is not enough to expose personal data.

How: the SQL is parsed (sqlglot, postgres dialect), qualified against an allowlist schema (config/db_allowlist.json) and
then ONLY THE REGENERATED SQL is executed, never the model's text. So comments, dollar quoting, unicode tricks and parser
differences cannot smuggle anything: what Postgres runs is exactly the tree that was checked. Everything not explicitly
allowed is refused (fail closed):
  - not exactly one SELECT / UNION statement (no DDL/DML, SELECT INTO, FOR UPDATE, SET, COPY, bind parameters)
  - a table or column that is not in the allowlist (SELECT * is expanded to the allowed columns only; a whole-row
    reference such as to_jsonb(t) cannot resolve)
  - a function that is not on ALLOWED_FUNCS (pg_read_file, query_to_xml, dblink, pg_sleep, set_config...)
  - table functions (generate_series, unnest...), information_schema / pg_catalog
"""
import json, logging, re
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify

logging.getLogger('sqlglot').setLevel(logging.ERROR)   # 'unsupported syntax' warnings are noise: refused statements are reported by check()

MAX_SQL_CHARS = 4000

# Allowlist sanity check: a column that looks like personal data must be acknowledged one by one ("allow_sensitive"),
# so a wholesale paste of a schema into the allowlist is caught at startup instead of in production.
SENSITIVE = re.compile(
    r'phone|mobile|tel(?:ephone)?$|email|e_mail|address|addr$|street|cccd|cmnd|citizen|id_?card|passport|national_id|'
    r'bank|iban|account_?(?:no|num|number)|card_?(?:no|num|number)|cvv|password|passwd|pwd|secret|token|otp|pin_?code|'
    r'birth|dob|full_?name|first_?name|last_?name|receiver|recipient|consignee|customer_?name|user_?name|tax_?(?:code|id)|'
    r'ssn|latitude|longitude|geo|ip_?address', re.I)

# Functions (sqlglot sql_name, upper case) the brain may call. Aggregates, null handling, strings, dates, casts. Deliberately
# no JSON extraction, no md5/hash (would let a masked value be tested), no system or file functions.
ALLOWED_FUNCS = {
    'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'NULLIF', 'LOWER', 'UPPER', 'LENGTH', 'ROUND', 'ABS', 'CEIL', 'FLOOR',
    'TIMESTAMP_TRUNC', 'DATE_TRUNC', 'DATETIME_TRUNC', 'EXTRACT', 'TIME_TO_STR', 'UNIX_TO_TIME', 'DATE', 'CURRENT_TIMESTAMP',
    'CURRENT_DATE', 'CURRENT_TIME', 'CAST', 'TRY_CAST', 'SUBSTRING', 'TRIM', 'CONCAT', 'CONCAT_WS', 'GREATEST', 'LEAST', 'LEFT',
    'RIGHT', 'SPLIT_PART', 'GROUP_CONCAT', 'ARRAY_AGG', 'REPLACE', 'CASE', 'IF', 'STR_POSITION', 'ROW_NUMBER', 'RANK',
    'DENSE_RANK', 'LAG', 'LEAD', 'LOGICAL_AND', 'LOGICAL_OR', 'EXISTS', 'PERCENTILE_CONT', 'DATE_ADD', 'DATE_SUB', 'DATE_DIFF',
    'TS_OR_DS_TO_DATE', 'STR_TO_TIME', 'STR_TO_DATE', 'TIME_STR_TO_TIME', 'TS_OR_DS_ADD', 'TS_OR_DS_DIFF', 'STDDEV', 'ANY_VALUE',
    'ARRAY_LENGTH', 'ARRAY_SIZE', 'CARDINALITY', 'ANY', 'ARRAY', 'REGEXP_LIKE', 'REGEXP_ILIKE',
}
# Casts only to plain data types: ::regclass / ::regproc / ::oid turn ids into catalog names, ::json opens a text value for parsing.
T = exp.DataType.Type
SAFE_CAST_TYPES = {T.TEXT, T.VARCHAR, T.CHAR, T.NVARCHAR, T.NCHAR, T.INT, T.BIGINT, T.SMALLINT, T.TINYINT, T.DECIMAL, T.DOUBLE,
                   T.FLOAT, T.BOOLEAN, T.DATE, T.TIMESTAMP, T.TIMESTAMPTZ, T.TIME, T.TIMETZ, T.INTERVAL, T.UUID}
ALLOWED_ANON_FUNCS = {'age', 'to_timestamp', 'to_date', 'btrim', 'date_part'}   # names sqlglot does not model
FORBIDDEN_NODES = (exp.Command, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Copy,
                   exp.Set, exp.Into, exp.Lock, exp.Parameter, exp.Placeholder, exp.Transaction, exp.Commit, exp.Rollback,
                   exp.Use, exp.Pragma, exp.Analyze, exp.LoadData, exp.Describe, exp.Show)
VALID_ID = re.compile(r'^[a-z][a-z0-9_]{0,62}$')


class GuardError(ValueError):
    """The SQL is refused. The message is shown to the model, so it says what to change."""


# ---------- allowlist file ----------

def load_allowlist(path):
    """-> {source_id: {'database': str, 'tables': {table: [columns]}}}. Raises GuardError on an unusable file."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise GuardError(f'allowlist {p} unreadable: {e}')
    sources = doc.get('sources')
    if not isinstance(sources, dict) or not sources:
        raise GuardError(f'allowlist {p}: "sources" is empty (the bot would see no database)')
    ack = {str(x).lower() for x in doc.get('allow_sensitive', [])}
    out = {}
    for sid, s in sources.items():
        if not VALID_ID.match(sid):
            raise GuardError(f'allowlist: bad source id {sid!r}')
        if not s.get('database'):
            raise GuardError(f'allowlist: source {sid} has no "database"')
        tables = {}
        for t, cols in (s.get('tables') or {}).items():
            if not VALID_ID.match(t) or not cols or not all(VALID_ID.match(c) for c in cols):
                raise GuardError(f'allowlist: {sid}.{t}: table and column names must be lower_snake_case, columns not empty')
            bad = [c for c in cols if SENSITIVE.search(c) and f'{sid}.{t}.{c}' not in ack]
            if bad:
                raise GuardError(f'allowlist: {sid}.{t} has columns that look like personal data: {bad}. Use a masked view, or '
                                 f'list "{sid}.{t}.<column>" in "allow_sensitive" after Dev BE and security sign-off.')
            tables[t] = list(dict.fromkeys(cols))
        if not tables:
            raise GuardError(f'allowlist: source {sid} has no tables')
        out[sid] = {'database': s['database'], 'tables': tables}
    return out


def schema_of(source_cfg):
    return {t: {c: 'text' for c in cols} for t, cols in source_cfg['tables'].items()}


def describe(source_cfg):
    return '\n'.join(f'{t}({", ".join(cols)})' for t, cols in sorted(source_cfg['tables'].items()))


# ---------- the check ----------

def check(sql, source_cfg):
    """Return the SQL to execute (regenerated from the checked tree), or raise GuardError."""
    sql = str(sql or '').strip()
    if not sql:
        raise GuardError('empty SQL')
    if len(sql) > MAX_SQL_CHARS:
        raise GuardError(f'SQL longer than {MAX_SQL_CHARS} characters')
    try:
        trees = sqlglot.parse(sql, read='postgres')
    except SqlglotError as e:
        raise GuardError(f'cannot parse as a PostgreSQL SELECT: {str(e)[:160]}')
    if len(trees) != 1 or trees[0] is None:
        raise GuardError('exactly one statement is allowed')
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise GuardError('only SELECT is allowed')
    _forbid_nodes(tree)
    schema = schema_of(source_cfg)
    try:
        q = qualify(tree, schema=schema, dialect='postgres', validate_qualify_columns=True, identify=True, quote_identifiers=True)
    except SqlglotError as e:
        raise GuardError(f'{_clean(e)}. Allowed tables and columns:\n{describe(source_cfg)}')
    _forbid_nodes(q)
    _check_tables(q, schema)
    _check_funcs(q)
    _check_casts(q)
    for star in q.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):          # count(*) only; every other * was expanded to allowed columns
            raise GuardError('a bare * could not be expanded: list the columns you need')
    return q.sql(dialect='postgres', comments=False)


def _clean(e):
    return re.sub(r'\s+', ' ', str(e)).strip()[:200]


def _forbid_nodes(tree):
    for n in tree.walk():
        if isinstance(n, FORBIDDEN_NODES):
            raise GuardError(f'{type(n).__name__} is not allowed: read-only SELECT only')


def _check_tables(q, schema):
    """Every table is an allowlisted one, or a CTE that is really in scope at that point. A table named like a CTE is the CTE only
    inside the queries that can see it: in a non-recursive WITH, the body of `WITH pg_stat_activity AS (SELECT ... FROM
    pg_stat_activity)` reads the REAL view, so the name alone proves nothing. Hence: WITH only at the top, never RECURSIVE, and a
    CTE body sees only the CTEs defined before it."""
    w = q.args.get('with') or q.args.get('with_')       # the key name changed between sqlglot versions
    ctes = list(w.expressions) if w else []
    if w and w.args.get('recursive'):
        raise GuardError('WITH RECURSIVE is not allowed')
    if {id(c) for c in q.find_all(exp.CTE)} != {id(c) for c in ctes}:
        raise GuardError('WITH is only allowed once, at the top of the query')
    names = [c.alias_or_name.lower() for c in ctes]
    inside = {}                                         # id(table node) -> index of the CTE body it sits in
    for i, c in enumerate(ctes):
        for t in c.find_all(exp.Table):
            inside[id(t)] = i
    for t in q.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise GuardError('table functions (generate_series, unnest, ...) are not allowed')
        name = t.name.lower()
        if t.catalog or (t.db and t.db.lower() != 'public'):
            raise GuardError(f'schema {t.db!r} is not allowed (information_schema / pg_catalog included); '
                             f'tables: {", ".join(sorted(schema))}')
        visible = set(names[:inside[id(t)]]) if id(t) in inside else set(names)
        if name in visible and not t.db:
            continue
        if name not in schema:
            raise GuardError(f'table {t.name!r} is not allowed. Allowed tables: {", ".join(sorted(schema))}')
    for n in q.walk():
        if isinstance(n, (exp.Unnest, exp.Lateral, exp.Values)) and not isinstance(n.parent, exp.In):
            raise GuardError(f'{type(n).__name__} sources are not allowed')


def _check_funcs(q):
    for n in q.walk():
        if isinstance(n, exp.Connector):          # AND / OR: sqlglot models them as Func subclasses, but they are operators
            continue
        if isinstance(n, exp.Anonymous):
            if n.name.lower() not in ALLOWED_ANON_FUNCS:
                raise GuardError(f'function {n.name}() is not allowed. Allowed: {", ".join(sorted(ALLOWED_FUNCS | {x.upper() for x in ALLOWED_ANON_FUNCS}))}')
        elif isinstance(n, exp.Func) and n.sql_name() not in ALLOWED_FUNCS:
            raise GuardError(f'function {n.sql_name()}() is not allowed')


def _check_casts(q):
    for c in q.find_all(exp.Cast):                              # TryCast is a subclass
        to = c.args.get('to')
        if not isinstance(to, exp.DataType) or to.this not in SAFE_CAST_TYPES:
            raise GuardError(f'cast to {to.sql(dialect="postgres") if to is not None else "?"} is not allowed '
                             '(text, numbers, boolean, date/time, uuid only)')
