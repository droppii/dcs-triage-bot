#!/usr/bin/env bash
# One-time setup (safe to re-run): read-only knowledge clones listed in config.json (with a bounded git history, so
# git blame/log work) + code anchors index + pinned DBHub 1.5.0.
# Windows: core.longpaths is set on every clone (mono-backend has paths over 260 chars; without it checkout stops half way, silently
# leaving files deleted in the working tree). Prefer a short repo location. Needs: git with access to the droppii org, Node 18+ (npm), python3.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p knowledge

# name url branch since(YYYY-MM-DD) [sparse paths]
python3 -I -c '
import json, datetime
for k in json.load(open("config.json", encoding="utf-8"))["knowledge"]:
    since = (datetime.date.today() - datetime.timedelta(days=int(k.get("history_days", 180)))).isoformat()
    print(k["name"], k["url"], k["branch"], since, ",".join(k.get("sparse", [])))
' |
while read -r name url branch since sparse; do
  dir="knowledge/$name"
  if [ -d "$dir/.git" ]; then
    # bot-owned read-only clone: no local edits, so jump straight to the latest commit; --shallow-since also
    # deepens an old depth-1 clone to the configured history window
    echo "update $name"
    git -C "$dir" config core.longpaths true
    git -C "$dir" fetch -q --shallow-since="$since" --filter=blob:limit=1m origin "$branch" && git -C "$dir" reset -q --hard FETCH_HEAD \
      || echo "  (update failed, keeping current copy)"
  elif [ -n "$sparse" ]; then
    echo "clone $name ($branch since $since, only ${sparse})"
    git clone -q -c core.longpaths=true --shallow-since="$since" --filter=blob:none --sparse --branch "$branch" "$url" "$dir"
    git -C "$dir" sparse-checkout set ${sparse//,/ }
  else
    echo "clone $name ($branch since $since)"
    git clone -q -c core.longpaths=true --shallow-since="$since" --filter=blob:limit=1m --branch "$branch" "$url" "$dir"
  fi
done

(cd tools/dbhub && npm ci --ignore-scripts --no-audit --no-fund --silent)
python3 -I tools/gen_anchors.py
python3 -I tools/code_index.py build --knowledge knowledge --config config.json
echo "done. Next: cp .env.example .env and fill it; see README 'Chạy thử'."
