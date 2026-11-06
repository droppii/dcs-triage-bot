#!/usr/bin/env bash
# One-time setup (safe to re-run): read-only knowledge clones listed in config.json + pinned DBHub 1.5.0.
# Needs: git with access to the droppii org, Node 18+ (npm), python3.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p knowledge

python3 -I -c 'import json; [print(k["name"], k["url"], k["branch"], ",".join(k.get("sparse", []))) for k in json.load(open("config.json"))["knowledge"]]' |
while read -r name url branch sparse; do
  dir="knowledge/$name"
  if [ -d "$dir/.git" ]; then
    # bot-owned read-only clone: no local edits, so jump straight to the latest commit (pull fails on shallow clones)
    echo "update $name"
    git -C "$dir" fetch -q --depth 1 origin "$branch" && git -C "$dir" reset -q --hard FETCH_HEAD \
      || echo "  (update failed, keeping current copy)"
  elif [ -n "$sparse" ]; then
    echo "clone $name ($branch, only ${sparse})"
    git clone -q --depth 1 --filter=blob:none --sparse --branch "$branch" "$url" "$dir"
    git -C "$dir" sparse-checkout set ${sparse//,/ }
  else
    echo "clone $name ($branch)"
    git clone -q --depth 1 --filter=blob:limit=1m --branch "$branch" "$url" "$dir"
  fi
done

(cd tools/dbhub && npm ci --ignore-scripts --no-audit --no-fund --silent)
echo "done. Next: cp .env.example .env and fill it; see README 'Chạy thử'."
