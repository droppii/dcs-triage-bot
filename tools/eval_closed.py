#!/usr/bin/env python3
"""Phase 1a: classify closed DCS tickets from their TEXT ONLY (no tools, no data access) and compare with how
devs actually closed them (RC Category). Measures triage quality before any data access is wired.

Input: ndjson export(s) of the real DCS Base, read-only, kept in data/ (git-ignored, never commit), e.g.
  lark-cli base +record-list --as user --base-token <DCS> --table-id <Tickets> --field-id Code ... --output data/x.ndjson
Run:   python3 tools/eval_closed.py data/prod-readonly/all_*.ndjson --n 100
Out:   data/eval-closed-<ts>.json (per ticket) + summary on stdout.
"""
import argparse, collections, json, subprocess, sys, tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot

# RC Category -> what the bot should have said. 'Support' = dev answered OR did a manual fix, so A, B or D all count.
# Labels that do not tell support from bug (Duplicate, Enviroment, QA, PO) and multi-label tickets are skipped.
TRUTH = {'Not a bug': 'AB', 'Human Error': 'AB', 'Support': 'S', 'BE': 'C', 'FE': 'C', 'ME': 'C', '3rdPARTY': 'C',
         'Monitoring': 'C', 'Data': 'D', 'Enhancement': 'E'}
OK = {'AB': 'AB', 'S': 'ABD', 'C': 'C', 'D': 'D', 'E': 'E'}
BATCH = 10
SCHEMA = {'type': 'object', 'required': ['results'], 'properties': {'results': {'type': 'array', 'items': {
    'type': 'object', 'required': ['code', 'type', 'needs_info', 'priority', 'reason'], 'properties': {
        'code': {'type': 'string'}, 'type': {'enum': ['A', 'B', 'C', 'D', 'E', '?']}, 'needs_info': {'type': 'boolean'},
        'priority': {'enum': ['High', 'Medium', 'Low']}, 'reason': {'type': 'string'}}}}}}


def truth(row):
    labels = [bot.text_of(x) for x in (row.get('RC Category') or [])]
    return TRUTH.get(labels[0]) if len(labels) == 1 else None


def pick(rows, n):
    done = [r for r in rows if bot.text_of(r.get('Status')) == 'Done' and truth(r)]
    return sorted(done, key=lambda r: str(r.get('Date Created', '')), reverse=True)[:n]


def classify(batch, model):
    tickets = [{'code': r['Code'], 'Title': r.get('Title') or '', 'Description': bot.sanitize(r.get('Description') or '')[:1500],
                'Product': bot.text_of(r.get('Product')), 'Feature': bot.text_of(r.get('Feature')),
                'Priority': bot.text_of(r.get('Priority'))} for r in batch]
    prompt = ('GIAI ĐOẠN 1a (đánh giá): bạn KHÔNG có công cụ và KHÔNG đọc được dữ liệu. Chỉ dựa vào nội dung ticket, '
              'phân loại từng ticket theo RULES (A/B/C/D/E/?), needs_info = thiếu thông tin bắt buộc để bắt đầu điều tra, '
              'priority đề xuất, reason <= 20 từ. Trả đúng JSON schema, đủ mọi ticket.\n'
              f'<tickets>\n{json.dumps(tickets, ensure_ascii=False, indent=1)}\n</tickets>')
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json', '--tools', '', '--strict-mcp-config',
           '--setting-sources', '', '--no-session-persistence', '--append-system-prompt', bot.rules(),
           '--json-schema', json.dumps(SCHEMA)]
    with tempfile.TemporaryDirectory() as d:
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=bot.CLAUDE_TIMEOUT)
    out = json.loads(p.stdout)
    return out['structured_output']['results'], out.get('total_cost_usd', 0)


def summarize(items):
    conf = collections.Counter((i['truth'], i['type']) for i in items)
    bugs = [i for i in items if i['truth'] in ('C', 'D')]
    notbug = [i for i in items if i['truth'] == 'AB']
    return {
        'n': len(items),
        'match': sum(i['type'] in OK[i['truth']] for i in items),
        'notbug_recognised': f"{sum(i['type'] in 'AB' for i in notbug)}/{len(notbug)}",
        # the risky miss: a real bug/data issue the bot would treat as lookup / by-design
        'bug_called_support': f"{sum(i['type'] in 'AB' for i in bugs)}/{len(bugs)}",
        'unsure': sum(i['type'] == '?' for i in items),
        'needs_info': sum(i['needs_info'] for i in items),
        'priority_suggested': dict(collections.Counter(i['priority'] for i in items)),
        'priority_reported': dict(collections.Counter(i['reported_priority'] for i in items)),
        'confusion_truth_x_pred': {f'{t}->{p}': c for (t, p), c in sorted(conf.items())},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exports', nargs='+')
    ap.add_argument('--n', type=int, default=100)
    ap.add_argument('--model', default=bot.MODEL)
    a = ap.parse_args()
    rows = [json.loads(l) for f in a.exports for l in open(f) if l.strip()]
    chosen = pick(rows, a.n)
    by_code = {r['Code']: r for r in chosen}
    items, cost = [], 0.0
    for k in range(0, len(chosen), BATCH):
        res, c = classify(chosen[k:k + BATCH], a.model)
        cost += c
        for x in res:
            r = by_code.get(x['code'])
            if r:  # ignore codes the model invented
                items.append({**x, 'truth': truth(r), 'rc': bot.text_of(r.get('RC Category')),
                              'reported_priority': bot.text_of(r.get('Priority')), 'title': r.get('Title')})
        print(f'{len(items)}/{len(chosen)} classified, ${cost:.2f}', file=sys.stderr)
    s = {**summarize(items), 'cost_usd': round(cost, 2), 'model': a.model, 'missing': len(chosen) - len(items)}
    out = ROOT / 'data' / f'eval-closed-{datetime.now(bot.TZ):%Y%m%d-%H%M%S}.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({'summary': s, 'items': items}, ensure_ascii=False, indent=1))
    print(json.dumps(s, ensure_ascii=False, indent=1))
    print('details:', out)


if __name__ == '__main__':
    main()
