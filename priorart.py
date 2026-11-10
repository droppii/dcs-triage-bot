"""Prior art: find closed past tickets that look like the new one (duplicates, 'not a bug', same fix).
Pure functions. bot.py fetches the rows (read-only) and passes them in."""
import math
import re

_WORD = re.compile(r'[^\W_]{3,}', re.UNICODE)
_STOP = {'the', 'and', 'cho', 'của', 'các', 'không', 'được', 'khi', 'này', 'với', 'trên', 'một', 'những', 'bị', 'lỗi',
         'nhờ', 'giúp', 'check', 'báo', 'app', 'đơn', 'hàng'}


def tokens(text):
    return {w for w in (m.lower() for m in _WORD.findall(text or '')) if w not in _STOP}


def similar(ticket, rows, fields, closed_statuses, k=3, min_score=0.25):
    """Rank closed rows with a resolution by cosine over word sets of title+description.
    `rows` are dicts keyed by the Base column names; `fields` maps code/title/description/status/resolution."""
    q = tokens(f"{ticket.get('Title', '')} {ticket.get('Description', '')}")
    if not q:
        return []
    scored = []
    for r in rows:
        status = _plain(r.get(fields['status']))
        resolution = _plain(r.get(fields['resolution']))
        if not resolution or (closed_statuses and status not in closed_statuses):
            continue
        t = tokens(f"{_plain(r.get(fields['title']))} {_plain(r.get(fields['description']))}")
        if not t:
            continue
        s = len(q & t) / math.sqrt(len(q) * len(t))
        if s >= min_score:
            scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    return [{'code': _plain(r.get(fields['code'])), 'title': _plain(r.get(fields['title']))[:200],
             'resolution': resolution_of(r, fields)[:400], 'score': round(s, 2)} for s, r in scored[:k]]


def resolution_of(r, fields):
    return _plain(r.get(fields['resolution']))


def _plain(v):
    if v is None:
        return ''
    if isinstance(v, list):
        return ', '.join(_plain(x) for x in v)
    if isinstance(v, dict):
        return str(v.get('name') or v.get('text') or v.get('id') or '')
    return str(v)
