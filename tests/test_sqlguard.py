#!/usr/bin/env python3
"""Offline tests for sqlguard.py: what the brain may and may not run on a production database. Run: python3 tests/test_sqlguard.py"""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sqlguard
from sqlguard import GuardError, check

SRC = {'database': 'vn_prod_order', 'tables': {
    'orders': ['id', 'code', 'status', 'user_id', 'total', 'created_at', 'shipping_fee'],
    'order_items': ['id', 'order_id', 'sku', 'qty', 'price']}}


def refused(sql, *words):
    try:
        out = check(sql, SRC)
    except GuardError as e:
        for w in words:
            assert w.lower() in str(e).lower(), f'{sql!r}: message {str(e)!r} lacks {w!r}'
        return
    raise AssertionError(f'NOT refused: {sql!r} -> {out}')


def allowed(sql):
    out = check(sql, SRC)
    assert out.upper().startswith(('SELECT', 'WITH')), out
    return out


# ---- legitimate triage queries pass ----
allowed("select id, code, status from orders where code = '2611060001'")
allowed("select o.code, count(*) from orders o join order_items i on i.order_id = o.id group by o.code order by 2 desc limit 20")
allowed("select status, sum(total), avg(shipping_fee) from orders where created_at >= now() - interval '1 day' group by status")
allowed("select date_trunc('day', created_at), count(*) from orders group by 1")
allowed("with x as (select id from orders where status = 'X') select count(*) from x")
allowed("select coalesce(shipping_fee, 0), lower(status), cast(total as text), total::int from orders")
allowed("select id from orders union select order_id from order_items")
allowed("select id from orders where user_id in (select user_id from orders where status = 'X')")

# ---- boolean operators (sqlglot models AND / OR as functions: they must not be mistaken for function calls) ----
allowed("select id from orders where id = 1 and status = 'a'")
allowed("select id from orders where id = 1 or id = 2")
allowed("select id from orders where (id = 1 or id = 2) and not (status = 'x') and code is not null")
allowed("select id from orders where id between 1 and 3 and code like 'x%' and status in ('a', 'b') and id <> 7")
allowed("select status, count(*) from orders group by status having count(*) > 1 and sum(total) > 0")
allowed("select o.id from orders o join order_items i on i.order_id = o.id and i.qty > 0 where o.id > 1 or o.id < 0")
allowed("select id from orders where id > any(array[1, 2]) and code ilike 'x'")
allowed("select case when id > 1 and status = 'a' then 1 else 0 end from orders")
refused("select id from orders where id = 1 and phone = '0912345678'", 'phone')           # AND does not hide a bad column
refused("select id from orders where id = 1 or pg_sleep(5) is null", 'pg_sleep')          # nor a bad function
refused("select id from orders where id = 1 and id in (select id from users)", 'users')   # nor a bad table

# ---- CTE scoping: a name is a CTE only where it is in scope ----
allowed('with a as (select id from orders), b as (select id from a) select id from b')
allowed('with a as (select id from orders) select a.id from a join order_items i on i.order_id = a.id')
refused('with pg_stat_activity as (select count(*) as n from pg_stat_activity) select n from pg_stat_activity', 'pg_stat_activity')
refused('with users as (select id from users) select id from users')            # body reads the real table
refused('with a as (select id from b), b as (select id from orders) select id from a', 'b')   # forward reference = real table
refused('with recursive a as (select id from orders) select id from a', 'RECURSIVE')
refused('select id from orders where id in (with x as (select id from orders) select id from x)', 'WITH')

# ---- SELECT * is safe: expanded to the allowed columns, never more ----
out = allowed('select * from orders')
assert '"code"' in out and '*' not in out
assert 'phone' not in out
assert '*' not in allowed('select o.*, i.sku from orders o join order_items i on i.order_id = o.id')

# ---- the output is regenerated: comments and the original text never reach the database ----
out = allowed("select code /* hello */ from orders -- trailing")
assert 'hello' not in out and 'trailing' not in out and '--' not in out and '/*' not in out, out

# ---- columns and tables outside the allowlist ----
refused('select phone from orders', 'phone')
refused('select u.phone from users u', 'users')
refused('select * from users', 'users')
refused('select "Code" from orders', 'code')                         # quoted, different case: not the same identifier
refused('select to_jsonb(o) from orders o', 'o')                     # whole-row reference cannot resolve
refused('select row_to_json(orders) from orders', 'orders')
refused('select (select phone from users limit 1) from orders')       # subquery
refused("select id from orders where user_id in (select id from users)", 'users')
refused('select * from information_schema.columns', 'information_schema')
refused('select * from pg_catalog.pg_shadow', 'pg_catalog')
refused('select * from pg_user', 'pg_user')
refused('select * from public.users', 'users')
refused('select * from other_db.public.orders')                      # catalog-qualified
refused('select * from generate_series(1, 3)')
refused('select * from unnest(array[1,2])')
refused("select * from (values (1)) v")

# ---- not a single read-only statement ----
refused('select 1; drop table orders', 'one statement')
refused('select id from orders; select id from orders', 'one statement')
refused('drop table orders', 'select')
refused("update orders set status = 'X'", 'select')
refused('delete from orders', 'select')
refused('insert into orders(id) values (1)', 'select')
refused("copy orders to '/tmp/x'", 'select')
refused('select id into newt from orders', 'not allowed')
refused('select id from orders for update', 'not allowed')
refused("set role postgres", 'select')
refused('with d as (delete from orders returning id) select * from d', 'not allowed')
refused("create table t as select id from orders", 'select')
refused('select id from orders where id = $1', 'not allowed')
refused('', 'empty')
refused('select ' + 'a' * 5000 + ' from orders', 'longer')
refused('selec id frm orders', 'parse')

# ---- functions ----
refused("select pg_read_file('/etc/passwd')", 'pg_read_file')
refused("select query_to_xml('select phone from users', true, true, '')", 'query_to_xml')
refused('select pg_sleep(30)', 'pg_sleep')
refused("select set_config('role', 'postgres', false)", 'set_config')
refused("select current_setting('server_version')", 'not allowed')
refused("select dblink('host=x', 'select 1')", 'dblink')
refused("select lo_import('/etc/passwd')", 'lo_import')
refused('select md5(code) from orders', 'not allowed')               # hashes would let a masked value be tested
refused('select version()', 'not allowed')
refused('select current_user', 'not allowed')
refused('select id from orders order by (select pg_sleep(5))', 'pg_sleep')   # hidden in ORDER BY / subqueries too
refused("select * from orders o, lateral (select pg_read_file('x')) x", 'Lateral')

# ---- casts: plain data types only (::regclass turns ids into catalog names, ::json opens text for parsing) ----
refused('select id::regclass from orders', 'REGCLASS')
refused('select id::oid from orders', 'OID')
refused('select code::json from orders', 'JSON')
allowed('select id::text, id::bigint, code::varchar(10), cast(id as numeric(10,2)), code::timestamptz from orders')
allowed("select id from orders where code ~ 'x'")

# ---- injection style strings are only data ----
allowed("select id from orders where code = 'x'' or ''1''=''1'")
allowed("select id from orders where code = '; drop table orders; --'")
out = allowed("select id from orders where code = $$a'b$$")
assert 'drop' not in out.lower()


# ---- allowlist file: personal-data columns cannot slip in unnoticed ----
def write(doc):
    d = Path(tempfile.mkdtemp())
    f = d / 'a.json'
    f.write_text(json.dumps(doc), encoding='utf-8')
    return f


good = {'sources': {'prod_order': {'database': 'vn_prod_order', 'tables': {'orders': ['id', 'code']}}}}
assert sqlguard.load_allowlist(write(good))['prod_order']['tables']['orders'] == ['id', 'code']
for col in ('phone', 'receiver_name', 'email', 'address_line', 'bank_account_no', 'cccd', 'password_hash', 'access_token', 'full_name'):
    bad = {'sources': {'prod_order': {'database': 'd', 'tables': {'orders': ['id', col]}}}}
    try:
        sqlguard.load_allowlist(write(bad))
        raise AssertionError(f'sensitive column accepted: {col}')
    except GuardError as e:
        assert col in str(e)
acked = {**bad, 'allow_sensitive': ['prod_order.orders.full_name']}
sqlguard.load_allowlist(write(acked))                                  # explicit sign-off is accepted
for broken in ({}, {'sources': {}}, {'sources': {'a': {'database': 'd', 'tables': {}}}},
               {'sources': {'A b': {'database': 'd', 'tables': {'t': ['id']}}}},
               {'sources': {'a': {'tables': {'t': ['id']}}}},
               {'sources': {'a': {'database': 'd', 'tables': {'T': ['id']}}}}):
    try:
        sqlguard.load_allowlist(write(broken))
        raise AssertionError(f'broken allowlist accepted: {broken}')
    except GuardError:
        pass
try:
    sqlguard.load_allowlist('/no/such/file.json')
    raise AssertionError('missing file accepted')
except GuardError:
    pass

print('sqlguard tests passed')
