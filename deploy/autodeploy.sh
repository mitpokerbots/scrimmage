#!/bin/bash
# Deploy new commits of the deployed branch automatically, once CI has passed.
# Run by scrimmage-autodeploy.timer every ~30 seconds; logs:
#   journalctl -u scrimmage-autodeploy
#
# A push to the branch goes live once GitHub Actions' "python" job (tests) has
# passed for it, plus the "deploy" job (installer test) if deploy/ changed. A
# commit whose CI fails, or whose install fails, is skipped; the next push is
# tried as usual. Turn it off with:
#   sudo systemctl disable --now scrimmage-autodeploy.timer
set -euo pipefail

# shellcheck source=deploy/lib.sh
source "$(dirname "$0")/lib.sh"
STATE=/var/lib/scrimmage-deploy
mkdir -p "$STATE"
cd "$APP"

branch=$(git rev-parse --abbrev-ref HEAD)
git fetch --quiet origin "$branch"
new=$(git rev-parse FETCH_HEAD)
deployed=$(cat "$STATE/deployed" 2>/dev/null || git rev-parse HEAD)
if [ "$new" = "$deployed" ] || [ "$new" = "$(cat "$STATE/failed" 2>/dev/null)" ]; then
  exit 0
fi

# Which CI jobs must pass first.
required=python
if git diff --name-only "$deployed" "$new" 2>/dev/null | grep -q '^deploy/'; then
  required="python deploy"
fi
repo=$(git remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')
verdict=$(curl -fsS -m 20 -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$repo/commits/$new/check-runs?per_page=100" \
  | python3 -c '
import json, sys
required = sys.argv[1].split()
runs = {run["name"]: run for run in json.load(sys.stdin)["check_runs"]}
for name in required:
    run = runs.get(name)
    if run is None or run["status"] != "completed":
        print("wait")
        break
    if run["conclusion"] != "success":
        print("fail " + name)
        break
else:
    print("ok")
' "$required") || verdict="wait"

case "$verdict" in
  wait) exit 0 ;;  # CI still running (or GitHub unreachable): try again shortly
  fail*)
    log "Not deploying ${new:0:12}: CI job '${verdict#fail }' failed"
    echo "$new" > "$STATE/failed"
    exit 0
    ;;
esac

log "Deploying ${new:0:12} (was ${deployed:0:12})"
git checkout --quiet --force -B "$branch" "$new"
if ! "$APP/deploy/install.sh"; then
  log "Install of ${new:0:12} failed; fix forward with a new commit, or run update.sh"
  echo "$new" > "$STATE/failed"
  exit 1
fi
