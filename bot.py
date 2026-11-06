#!/usr/bin/env python3
"""DCS Triage Bot, STAGING sandbox only.

Each tick: read tickets from the sandbox Base -> find the ticket card in the sandbox group (or post one)
-> triage with `claude -p` (read-only tools only) -> reply in the card's thread -> fill the bot columns.
Prod is only READ (Schedule = on-call rota). Every write is guarded to the sandbox Base / sandbox chat.

  python3 bot.py --once            one tick
  python3 bot.py                   loop every 60s
  python3 bot.py --dry-ticket F    run the brain on a ticket JSON file, print result, write nothing
"""
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'logs'
TZ = timezone(timedelta(hours=7))

SANDBOX_BASE = 'AHDmbnt6eaAa6asZLHAlfOmkgpd'
SANDBOX_TICKETS = 'tbl4UiCUszPyEvdF'
SANDBOX_CHAT = 'oc_1861966f1b33af544be0bfadd6ebe6d3'
PROD_BASE = 'UMiebZAFMahsj2s54uhlz3Ojgeh'  # read-only
PROD_SCHEDULE = 'tblw7ZnVIRvIlrXz'

MODEL = os.environ.get('DCS_MODEL', 'sonnet')
POLL_SECONDS = 60
CARD_WAIT = timedelta(minutes=3)
CLAUDE_TIMEOUT = 900

TYPE_LABEL = {'A': 'A - Tra cứu', 'B': 'B - Cách dùng/Đúng thiết kế', 'C': 'C - Bug', 'D': 'D - Chỉnh dữ liệu',
              'E': 'E - Đề xuất', '?': '? - Không chắc'}
TICKET_FIELDS = ['Code', 'Title', 'Description', 'Priority', 'Product', 'Feature', 'Status', 'Date Created',
                 'ReportedBy', 'Bot Status', 'Bot Thread']

KNOWLEDGE = [str(ROOT / 'knowledge/engineering-knowledge'),  # bot's own shallow clone of main; `git pull` to refresh
             str(ROOT / 'knowledge/mono-backend'),  # shallow clone of branch dev (= what staging builds from)
             '/Users/nhudoan/Documents/12_QAworkflows/droppii-odoo']
READ_TOOLS = (
    [f'Read(/{p}/**)' for p in KNOWLEDGE] + [f'Grep(/{p}/**)' for p in KNOWLEDGE] + [f'Glob(/{p}/**)' for p in KNOWLEDGE]
    + ['mcp__dbhub']  # DBHub itself enforces readonly=true, max 100 rows, 15s
    + ['mcp__sentry__' + t for t in (
        'find_organizations find_projects find_releases get_release_details get_issue_details get_event_stacktrace '
        'get_issue_breadcrumbs get_issue_tag_values get_trace_details get_span_details get_sentry_resource '
        'search_issues search_errors search_issue_events search_logs').split()]
    + ['mcp__bugsnag__bugsnag_' + t for t in (
        'get_current_project list_projects list_project_errors list_project_event_filters get_error get_event '
        'get_events_on_an_error get_event_details_from_dashboard_url list_releases get_release get_build').split()]
    + ['mcp__newrelic__' + t for t in (
        'list_available_new_relic_accounts execute_nrql_query search_entity_with_tag get_entity list_related_entities '
        'list_recent_logs analyze_entity_logs get_log_statistics search_errors list_entity_error_groups analyze_errors '
        'get_distributed_trace_details analyze_transactions analyze_golden_metrics list_change_events '
        'list_recent_issues search_incident convert_time_period_to_epoch_ms').split()]
)


# ---------- lark-cli ----------

def parse_cli(stdout, stderr):
    """lark-cli prints the result JSON on stdout, but errors (e.g. 429) on stderr."""
    for raw in (stdout, stderr):
        raw = raw.strip()
        if '{' in raw:
            try:
                return json.loads(raw[raw.index('{'):])
            except json.JSONDecodeError:
                continue
    return None


def lark(*args, retries=3):
    for attempt in range(retries + 1):
        out = subprocess.run(['lark-cli', *args], capture_output=True, text=True, timeout=120)
        res = parse_cli(out.stdout, out.stderr)
        if res is None:
            raise RuntimeError(f'lark-cli {args[:2]} failed: {out.stderr.strip()[:300]}')
        if res.get('ok', True):
            return res.get('data', res)
        err = res.get('error') or {}
        if not err.get('retryable') or attempt == retries:
            raise RuntimeError(f'lark-cli {args[:2]}: {err.get("subtype")} {err.get("message")}')
        time.sleep(5 * 2 ** attempt)  # 429 asks for backoff; sends carry an idempotency key, so no double-post


def lark_write(*args):
    # Hard guard: the bot may only write to the sandbox Base / sandbox chat / threads it found there.
    a = list(args)
    if '--base-token' in a and a[a.index('--base-token') + 1] != SANDBOX_BASE:
        raise PermissionError('write outside sandbox Base blocked')
    if '--chat-id' in a and a[a.index('--chat-id') + 1] != SANDBOX_CHAT:
        raise PermissionError('send outside sandbox chat blocked')
    if '--message-id' in a and a[a.index('--message-id') + 1] not in _sandbox_message_ids:
        raise PermissionError('reply to a message not seen in sandbox chat blocked')
    return lark(*args)


_sandbox_message_ids = set()


def rows_to_dicts(data):
    fields = data['fields']
    return [dict(zip(fields, row), record_id=rid) for row, rid in zip(data['data'], data['record_id_list'])]


def text_of(v):
    """Base cell -> plain text (select lists, user lists, rich text segments)."""
    if v is None:
        return ''
    if isinstance(v, list):
        return ', '.join(text_of(x) for x in v)
    if isinstance(v, dict):
        return str(v.get('name') or v.get('text') or v.get('id') or '')
    return str(v)


# ---------- sandbox data ----------

def sandbox_tickets():
    args = ['base', '+record-list', '--base-token', SANDBOX_BASE, '--table-id', SANDBOX_TICKETS, '--as', 'user',
            '--format', 'json', '--limit', '200']
    for f in TICKET_FIELDS:
        args += ['--field-id', f]
    return rows_to_dicts(lark(*args))


def update_ticket(record_id, fields):
    lark_write('base', '+record-batch-update', '--base-token', SANDBOX_BASE, '--table-id', SANDBOX_TICKETS,
               '--as', 'user', '--json', json.dumps({'update_records': {record_id: fields}}, ensure_ascii=False))


def chat_messages(limit=50):
    data = lark('im', '+chat-messages-list', '--chat-id', SANDBOX_CHAT, '--as', 'user', '--page-size', str(limit),
                '--no-reactions')
    msgs = data.get('items') or data.get('messages') or []
    _sandbox_message_ids.update(m['message_id'] for m in msgs)
    return msgs


def find_card(code):
    # The copied "Notify new ticket" automation posts a card titled "<Code> - <Product> - <Priority> - <Title>".
    for m in chat_messages():
        if m.get('msg_type') == 'interactive' and f'title="{code} ' in str(m.get('content')):
            return m['message_id']
    return None


def thread_messages(root_id):
    data = lark('im', '+threads-messages-list', '--thread', root_id, '--as', 'user', '--no-reactions')
    msgs = data.get('items') or data.get('messages') or []
    _sandbox_message_ids.update(m['message_id'] for m in msgs)
    return msgs


def post_root(ticket):
    text = (f"**[STG] {text_of(ticket['Code'])} - {text_of(ticket['Product'])} - {text_of(ticket['Priority'])} - "
            f"{text_of(ticket['Title'])}**\n(Bot tự đăng vì không thấy thẻ của automation sau 3 phút)")
    mid = lark_write('im', '+messages-send', '--as', 'bot', '--chat-id', SANDBOX_CHAT, '--markdown', text)['message_id']
    _sandbox_message_ids.add(mid)
    return mid


def reply(root_id, markdown, key=None):
    args = ['im', '+messages-reply', '--as', 'bot', '--message-id', root_id, '--reply-in-thread',
            '--markdown', sanitize(markdown)]
    if key:
        args += ['--idempotency-key', key[:50]]
    return lark_write(*args)


# ---------- prod (read-only) ----------

def oncall(product, now=None):
    """(Person names, BA-QA names) on duty for a product, from the prod Schedule table (read-only)."""
    data = lark('base', '+record-list', '--base-token', PROD_BASE, '--table-id', PROD_SCHEDULE, '--as', 'user',
                '--format', 'json', '--limit', '200', '--sort-json', '[{"field":"FromDate","desc":true}]')
    return pick_oncall(rows_to_dicts(data), product, now or datetime.now(TZ))


def pick_oncall(rows, product, now):
    # shortcut: FromDate <= now < ToDate (+1 day slack); confirm the rota convention with QA before going live.
    for r in rows:
        try:
            start = datetime.fromisoformat(r['FromDate'])
            end = datetime.fromisoformat(r['ToDate']) + timedelta(days=1)
        except (TypeError, ValueError, KeyError):
            continue
        if product in (r.get('Product') or []) and start <= now < end:
            return text_of(r.get('Person')), text_of(r.get('BA-QA'))
    return '', ''


# ---------- brain ----------

def run_brain(ticket, thread_text='', label='dry'):
    prompt = (
        f"Thời điểm hiện tại: {datetime.now(TZ):%Y-%m-%d %H:%M} (Asia/Ho_Chi_Minh).\n"
        "Xử lý ticket DCS dưới đây theo RULES. Trả về đúng JSON schema.\n"
        f"<ticket>\n{json.dumps(ticket, ensure_ascii=False, indent=1)}\n</ticket>\n"
        f"<thread>\n{thread_text or '(chưa có trao đổi)'}\n</thread>"
    )
    cmd = ['claude', '-p', prompt, '--model', MODEL, '--output-format', 'stream-json', '--verbose',
           '--append-system-prompt', (ROOT / 'agent/RULES.md').read_text(),
           '--json-schema', (ROOT / 'agent/schema.json').read_text(),
           '--mcp-config', str(ROOT / 'agent/mcp.json'), '--strict-mcp-config', '--setting-sources', '',
           '--tools', 'Read,Grep,Glob', '--permission-mode', 'dontAsk', '--allowedTools', *READ_TOOLS]
    LOGS.mkdir(exist_ok=True)
    log = LOGS / f'{label}-{datetime.now(TZ):%Y%m%d-%H%M%S}.jsonl'
    with open(log, 'w') as f:  # full transcript = audit log of every tool call / query
        subprocess.run(cmd, cwd=ROOT / 'agent', stdout=f, stderr=subprocess.STDOUT, timeout=CLAUDE_TIMEOUT,
                       env={**os.environ, 'USER': os.environ.get('USER', 'nhudoan')})
    result = None
    for line in log.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get('type') == 'result':
            result = e
    if not result or result.get('is_error') or not result.get('structured_output'):
        raise RuntimeError(f'brain failed, see {log.name}')
    out = result['structured_output']
    out['_log'], out['_cost'] = log.name, result.get('total_cost_usd')
    return out


# ---------- output safety ----------

_MASKS = [
    (re.compile(r'postgres(?:ql)?://\S+', re.I), 'postgres://***'),
    (re.compile(r'(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)\S+'), r'\1\2***'),
    (re.compile(r'(?<![\w.])[\w.+-]+@[\w-]+\.[\w.-]+'), '***@***'),
    (re.compile(r'(?<!\d)(?:\+?84|0)[35789]\d{8}(?!\d)'), '***SĐT***'),
    (re.compile(r'(?i)((?:cccd|cmnd|căn cước)\D{0,5})\d{9,12}'), r'\1***'),
]


def sanitize(text):
    for rx, rep in _MASKS:
        text = rx.sub(rep, text)
    return text


CHECKS = (('source_be', 'Code BE'), ('db', 'DB'), ('newrelic', 'New Relic'))
MARK = {'confirmed': '✓', 'contradicts': '✗ ngược', 'not_found': '✗ không thấy', 'not_checked': '– chưa kiểm'}


def gated(out):
    return out['type'] in ('A', 'B', 'C', 'D') and not out['missing_info']


def verdict(out):
    """Dev rule: conclude only at 100%, i.e. code, DB and New Relic all confirmed. Decided here, not by the model."""
    if gated(out) and not all(out['evidence'][k]['status'] == 'confirmed' for k, _ in CHECKS):
        return '?'
    return out['type']


def compose_reply(out, ticket, person, baqa):
    eff = verdict(out)
    lines = [f"**[STG] DCS Triage Bot** · Loại: {TYPE_LABEL[eff]}"]
    if out['type'] != 'E' and not out['missing_info']:  # show what was checked whenever an investigation ran
        lines.append('Đã kiểm: ' + ' · '.join(f"{name} {MARK[out['evidence'][k]['status']]}" for k, name in CHECKS))
    lines += ['', out['reply_markdown'].strip()]
    if eff != out['type']:
        lines += ['', f"Bot chưa chắc 100% (nghi: {TYPE_LABEL[out['type']]}) nên không kết luận. "
                      "Người trực kiểm tra tiếp từ các bằng chứng trên."]
    reported = text_of(ticket.get('Priority'))
    if reported and out['priority_suggested'] != reported:
        lines += ['', f"Đề xuất Priority {reported} → {out['priority_suggested']}. Lý do: {out['priority_reason']}"]
    route = {'C': person, 'D': person, '?': person, 'E': baqa}.get(eff)
    # plan: a High ticket goes to on-call right away, even while the bot is still waiting for the reporter
    if route is not None and (not out['missing_info'] or out['priority_suggested'] == 'High'):
        who = route or '(không tìm thấy lịch trực cho Product này)'
        lines += ['', f"Chuyển: {who} (sandbox: chỉ ghi tên, không tag)"]
    return '\n'.join(lines)


def bot_fields(out, status):
    checks = ', '.join(f"{name} {out['evidence'][k]['status']}" for k, name in CHECKS)
    note = (f"{out['summary']} | nghi: {out['type']} | {checks} | nguồn: {'; '.join(out['sources'])[:500]} "
            f"| log {out['_log']}")
    return {'Bot Status': [status], 'AI Type': [TYPE_LABEL[verdict(out)]],
            'AI Priority': [out['priority_suggested']], 'AI Note': sanitize(note)}


# ---------- flow ----------

def brain_view(t):
    return {k: text_of(t.get(k)) for k in ('Code', 'Title', 'Description', 'Priority', 'Product', 'Feature', 'ReportedBy')}


def finish(t, root, out):
    person, baqa = oncall(text_of(t.get('Product')))
    reply(root, compose_reply(out, t, person, baqa), key=f"{text_of(t['Code'])}-{out['_log']}")
    eff = verdict(out)
    status = 'Chờ người báo' if out['missing_info'] else ('Đã trả lời' if eff in ('A', 'B') else 'Đã chuyển người')
    update_ticket(t['record_id'], bot_fields(out, status))
    print(f"{text_of(t['Code'])}: nghi {out['type']} -> {eff} {[out['evidence'][k]['status'] for k, _ in CHECKS]} "
          f"-> {status} (cost {out['_cost']})")


def created_at(t, now):
    """System 'Date Created' (ISO string or epoch ms); unknown -> now, so the bot still waits for the card."""
    v = t.get('Date Created')
    try:
        return datetime.fromtimestamp(int(v) / 1000, TZ) if str(v).isdigit() else datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return now


def handle_new(t, now):
    code = text_of(t['Code'])
    root = find_card(code)
    if not root:
        if now - created_at(t, now) < CARD_WAIT:
            return  # give the automation time to post its card
        root = post_root(t)
    update_ticket(t['record_id'], {'Bot Status': ['Đang xử lý'], 'Bot Thread': root})
    try:
        reply(root, '[STG] Bot đang kiểm tra ticket này, sẽ trả lời trong vài phút.', key=f'{code}-ack')
        finish(t, root, run_brain(brain_view(t), label=code))
    except Exception as e:
        fail(t, root, e)


def fail(t, root, e):
    """Never leave a ticket silent or stuck in 'Đang xử lý': hand it back to the normal on-call flow."""
    code = text_of(t.get('Code'))
    print(f'{code}: ERROR {e}', file=sys.stderr)
    for step in (lambda: update_ticket(t['record_id'], {'Bot Status': ['Lỗi bot'], 'AI Note': str(e)[:500]}),
                 lambda: reply(root, '[STG] Bot gặp lỗi khi kiểm tra, người trực xử lý theo cách thường.', key=f'{code}-err')):
        try:
            step()
        except Exception as e2:  # best effort; the watchdog (later) covers what is still missed
            print(f'{code}: fallback step failed: {e2}', file=sys.stderr)


def handle_waiting(t):
    root = t.get('Bot Thread')
    if not root:
        return
    _sandbox_message_ids.add(root)
    msgs = thread_messages(root)
    last_bot = max((i for i, m in enumerate(msgs) if (m.get('sender') or {}).get('sender_type') == 'app'), default=-1)
    if not any((m.get('sender') or {}).get('sender_type') == 'user' for m in msgs[last_bot + 1:]):
        return  # shortcut: no +4h reminder / +1 day auto-close yet; add when the flow is approved
    transcript = '\n'.join(f"[{(m.get('sender') or {}).get('sender_type')}] {text_of(m.get('content'))[:2000]}" for m in msgs)
    update_ticket(t['record_id'], {'Bot Status': ['Đang xử lý']})
    try:
        finish(t, root, run_brain(brain_view(t), transcript, label=text_of(t['Code'])))
    except Exception as e:
        fail(t, root, e)


def tick():
    now = datetime.now(TZ)
    for t in sandbox_tickets():
        status = text_of(t.get('Bot Status'))
        if not status:
            handle_new(t, now)
        elif status == 'Chờ người báo':
            handle_waiting(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry-ticket', help='ticket JSON file: run the brain only, write nothing')
    a = ap.parse_args()
    if a.dry_ticket:
        out = run_brain(json.loads(Path(a.dry_ticket).read_text()), label='dry')
        print(json.dumps(out, ensure_ascii=False, indent=1))
        print('\n--- reply preview ---\n' + sanitize(compose_reply(out, json.loads(Path(a.dry_ticket).read_text()), '<người trực>', '<BA-QA>')))
        return
    while True:
        try:
            tick()
        except Exception as e:
            print(f'tick error: {e}', file=sys.stderr)
        if a.once:
            return
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
