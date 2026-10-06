#!/bin/bash
# Deploy the latest code: sudo /opt/scrimmage/deploy/update.sh [branch]
# Running games are interrupted and requeued; nothing is lost.
set -euo pipefail
cd /opt/scrimmage
if [ -n "${1:-}" ]; then
  git fetch origin "$1"
  git checkout -B "$1" "origin/$1"
else
  git pull --ff-only
fi
exec /opt/scrimmage/deploy/install.sh
