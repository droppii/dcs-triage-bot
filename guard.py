"""Output, log and startup safety shared by bot.py and the tools. Pure functions, no network, standard library only.

Defence in depth for PRODUCTION data. The controls that matter most live outside the model and outside this file:
the DB role (read-only replica, masked views), the identity the bot writes with, and the network. What is here are the
layers the bot can enforce by itself:
  sanitize_counted / sanitize  mask personal data and secrets in anything leaving the bot (a blacklist: it can miss
                               things, which is why the DB must not hand out personal data in the first place)
  leak_blocked                 too many masks in one reply = the model is dumping data: withhold the reply
  purge_old                    retention for logs and downloaded attachments
  SECRET_FILE_GLOBS            files in the code clones the brain may never read
  preflight                    production refuses to start unless the safety settings and approvals are in place
"""
import fnmatch, json, re, time
from pathlib import Path

PROD_NAMES = {'production', 'prod'}


# ---------- config ----------

def deep_merge(base, over):
    """Objects merge key by key, everything else (lists, scalars) is replaced by the override."""
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path, _seen=()):
    """A config file may say "extends": "config.json" (relative to itself): only its differences are written down, so the
    production file cannot drift from the staging one."""
    path = Path(path).resolve()
    if path in _seen:
        raise ValueError(f'config "extends" loops at {path}')
    cfg = json.loads(path.read_text(encoding='utf-8'))
    parent = cfg.pop('extends', None)
    return deep_merge(load_config(path.parent / parent, _seen + (path,)), cfg) if parent else cfg

# ---------- masking ----------

def luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def iban_ok(s):
    """ISO 13616 checksum (mod 97). A random tracking code passes it 1 time in 97."""
    moved = s[4:] + s[:4]
    return int(''.join(str(int(c, 36)) for c in moved)) % 97 == 1


def card_like(d):
    """Luhn AND a real card prefix and length (Visa, Mastercard, Amex, JCB, Discover, UnionPay, Napas 9704). Luhn alone matches
    1 in 10 of all long numbers, such as order ids and epoch milliseconds, and those must survive."""
    n = len(d)
    if n < 15 or n > 19 or not luhn_ok(d):
        return False
    if n == 15:
        return d[:2] in ('34', '37')
    two, four = int(d[:2]), int(d[:4])
    return (d[0] == '4' and n in (16, 19)) or 51 <= two <= 55 or 2221 <= four <= 2720 or d[:2] in ('35', '62', '65') \
        or d[:4] in ('6011', '9704')


_CARD = re.compile(r'(?<![\w.-])(?:\d[ -]?){14,18}\d(?![\w-])')

# (regex, replacement). Order matters: connection strings and keys first, so a password is not left behind by a later rule.
MASKS = [
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)'), '***PRIVATE KEY***'),
    (re.compile(r'postgres(?:ql)?://(?!\*\*\*)\S+', re.I), 'postgres://***'),
    (re.compile(r'(?i)\bjdbc:[a-z0-9]+:\S+'), 'jdbc:***'),
    (re.compile(r'\b[a-z][a-z0-9+.\-]{1,20}://[^\s/:@]+:[^\s/@]+@\S+', re.I), '***://***'),      # any scheme://user:pass@host
    # Bugsnag/Sentry payloads carry request headers: bearer tokens and JWTs must never reach a reply or a log
    (re.compile(r'(?i)\bbearer\s+(?!\*\*\*)\S+'), 'Bearer ***'),
    (re.compile(r'\beyJ[\w-]{8,}\.[\w-]+\.[\w-]+'), '***JWT***'),
    (re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'), '***AWS KEY***'),
    (re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'), '***GITHUB TOKEN***'),
    (re.compile(r'\bxox[abprs]-[\w-]{10,}'), '***SLACK TOKEN***'),
    (re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'), '***API KEY***'),
    (re.compile(r'(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)(?!\*\*\*)\S+'), r'\1\2***'),
    (re.compile(r'(?<![\w.])[\w.+-]+@[\w-]+\.[\w.-]+'), '***@***'),
    # Vietnamese mobile numbers, also written 0912 345 678 / 091.234.5678 / +84 912 345 678
    (re.compile(r'(?<!\d)(?:\+?84|0)[\s.-]?[35789](?:[\s.-]?\d){8}(?!\d)'), '***SĐT***'),
    (re.compile(r'(?i)((?:cccd|cmnd|căn cước)\D{0,5})\d{9,12}'), r'\1***'),
    # bank account: only when the text says so (order and tracking numbers are digits too and must survive)
    (re.compile(r'(?i)((?:stk|số tài khoản|so tai khoan|tk ngân hàng|bank account|account (?:no|num|number))\D{0,12})'
                r'(\d[\d .\-]{7,25}\d)'), r'\1***'),
]

_IBAN = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b')


def sanitize_counted(text):
    """-> (masked text, number of masks applied). Card numbers are masked only when they pass the Luhn check."""
    n = 0
    for rx, rep in MASKS:
        text, k = rx.subn(rep, text)
        n += k
    hits = []

    def card(m):
        if card_like(re.sub(r'\D', '', m.group(0))):
            hits.append(1)
            return '***THẺ***'
        return m.group(0)

    def iban(m):
        if iban_ok(m.group(0)):
            hits.append(1)
            return '***IBAN***'
        return m.group(0)

    text = _IBAN.sub(iban, _CARD.sub(card, text))
    return text, n + len(hits)


def sanitize(text):
    return sanitize_counted(text)[0]


_STR_LITERAL = re.compile(r"'(?:[^']|'')*'|\$\$.*?\$\$|\$[A-Za-z_]\w*\$.*?\$[A-Za-z_]\w*\$", re.S)


def mask_literals(sql):
    """SQL with every string literal replaced by '?': names and addresses a query filters on never reach the audit log."""
    return _STR_LITERAL.sub("'?'", str(sql))


def leak_blocked(masks, limit):
    """A normal reply needs no personal data, so a reply that needed many masks is withheld (limit 0 = never)."""
    return bool(limit) and masks > limit


# ---------- retention ----------

def purge_old(folder, days, pattern='*', now=None):
    """Delete files older than `days` (by mtime) in `folder`. Returns how many were removed. days <= 0 = keep everything."""
    folder = Path(folder)
    if not folder.is_dir() or not days or days <= 0:
        return 0
    cutoff = (now or time.time()) - days * 86400
    removed = 0
    for f in folder.glob(pattern):
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            pass
    return removed


# ---------- files the brain may never read ----------

# Matched under every knowledge clone (Read/Grep/Glob deny rules). Real hits found by tools/scan_secrets.py are added per file.
SECRET_FILE_GLOBS = ['.env', '.env.*', '*.pem', '*.key', '*.p12', '*.pfx', '*.jks', '*.keystore', 'id_rsa*', 'id_ed25519*',
                     '.npmrc', '.pypirc', '.netrc', '.git-credentials', 'credentials*', 'secrets*', '*secret*.y*ml',
                     'application-prod*', 'application-production*', 'appsettings.Production*', '*.tfvars', 'terraform.tfstate*',
                     'service-account*.json', 'google-services.json', 'GoogleService-Info.plist']


def is_secret_path(rel, listed=()):
    """rel = path relative to knowledge/ (repo/dir/file). True for a well-known credential file name or a file the scanner listed."""
    rel = str(rel).replace('\\', '/').lstrip('/')
    name = rel.rsplit('/', 1)[-1].lower()
    return rel in set(listed) or any(fnmatch.fnmatchcase(name, g.lower()) for g in SECRET_FILE_GLOBS)


def secret_pathspecs(repo, listed=()):
    """git pathspec exclusions for `git show` of a whole commit, so a diff cannot print a credential file."""
    specs = [f':(exclude,glob)**/{g}' for g in SECRET_FILE_GLOBS]
    specs += [f':(exclude,literal){p[len(repo) + 1:]}' for p in listed if p.startswith(repo + '/')]
    return specs


# ---------- production preflight ----------

APPROVALS = {
    'data_scope': 'who approved which replica, tables and columns the bot may read (docs/PROD-READINESS.md section 5)',
    'log_policy': 'who approved where logs/ live and how long they are kept',
    'model_provider': 'who approved sending data to the model provider under a company account',
    'code_version': 'who confirmed knowledge[].branch is the code that runs in production',
}


def _placeholders(obj, path=''):
    """(config path, value) of every string that still carries an example marker (REPLACE_..., CONFIRM_...)."""
    if isinstance(obj, str):
        return [(path, obj)] if re.search(r'REPLACE_|CONFIRM_', obj) else []
    items = ([(k, v) for k, v in obj.items() if k != 'note' and not k.startswith('_')] if isinstance(obj, dict)
             else enumerate(obj) if isinstance(obj, list) else ())
    return [hit for k, v in items for hit in _placeholders(v, f'{path}.{k}'.lstrip('.'))]


def is_prod(cfg):
    return str((cfg.get('env') or {}).get('name', '')).strip().lower() in PROD_NAMES


def preflight(cfg, root):
    """Problems that must stop the bot before it touches production data. Empty list = fine (and always empty off production)."""
    if not is_prod(cfg):
        return []
    root = Path(root)
    env, problems = cfg.get('env') or {}, []
    if cfg.get('redact_logs') is False:
        problems.append('redact_logs is false: production logs must be masked (leave it unset or true)')
    days = cfg.get('log_retention_days')
    if not isinstance(days, int) or not 0 < days <= 30:
        problems.append('log_retention_days must be an integer between 1 and 30')
    for key, want in (('sentry_environment', 'production'), ('bugsnag_release_stage', 'production')):
        if str(env.get(key, '')).lower() != want:
            problems.append(f'env.{key} must be "{want}" (it is {env.get(key)!r}): a half-converted config reads staging errors')
    if 'stg' in str(env.get('nr_prefix', '')).lower() or str(env.get('db_prefix', '')).lower().startswith('stg'):
        problems.append('env.nr_prefix / env.db_prefix still point at staging')
    approvals = cfg.get('approvals') or {}
    for key, what in APPROVALS.items():
        if not str(approvals.get(key, '')).strip():
            problems.append(f'approvals.{key} is empty: {what}')
    g = cfg.get('db_guard') or {}
    if not g.get('enabled'):
        problems.append('db_guard.enabled must be true: production DB access always goes through tools/dbhub_guard_mcp.py')
    else:
        try:
            import sqlguard
            allow = sqlguard.load_allowlist(root / g.get('allowlist', 'config/db_allowlist.json'))
            for sid, s in allow.items():
                if 'stg' in s['database'].lower() or 'staging' in s['database'].lower():
                    problems.append(f'db_guard source {sid} points at {s["database"]}: looks like staging')
                if 'REPLACE' in s['database']:
                    problems.append(f'db_guard source {sid}: database {s["database"]!r} is still the example placeholder')
        except ImportError as e:
            problems.append(f'sqlglot is not installed ({e}): pip install -r requirements.txt')
        except ValueError as e:                       # sqlguard.GuardError
            problems.append(str(e))
    src = cfg.get('ticket_source') or {}
    if src.get('mode') == 'shadow':
        for key in ('base_token', 'tickets_table'):
            if not str(src.get(key, '')).strip():
                problems.append(f'ticket_source.{key} is empty: shadow mode needs the real DCS Base and its tickets table')
    problems += [f'{path} still holds a placeholder ({val!r}): fill it in' for path, val in _placeholders(cfg)]
    mode = (cfg.get('ticket_source') or {}).get('mode', 'sandbox')
    if mode not in ('sandbox', 'shadow'):
        problems.append(f'ticket_source.mode {mode!r}: only "sandbox" and "shadow" exist (the bot never writes to the real DCS Base or group)')
    return problems
