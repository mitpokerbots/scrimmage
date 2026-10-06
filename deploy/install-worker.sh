#!/bin/bash
# Set up a fleet worker on a fresh Ubuntu 24.04 instance. Run by the worker
# launch template's user data after it checked out the main server's commit.
# Any failure shuts the instance down, and the fleet launches a replacement.
#
# Reads /etc/scrimmage/install.env: SERVER_URL, AWS_REGION,
# WORKER_TOKEN_SECRET, IMAGE_REPO.
#
# With SCRIMMAGE_BAKE=1 (scrimmage bake-images), it installs everything but
# starts nothing, then powers off to be saved as the fleet's machine image. Machines started from that image rerun
# this script, which then only has to catch up on what changed.
set -euo pipefail

# shellcheck source=deploy/lib.sh
source "$(dirname "$0")/lib.sh"
source "$ETC/install.env"
: "${SERVER_URL:?} ${AWS_REGION:?} ${WORKER_TOKEN_SECRET:?} ${IMAGE_REPO:?}"
COMMIT=$(git -C "$APP" rev-parse HEAD)
export DEBIAN_FRONTEND=noninteractive

instance_id=$(imds instance-id)
bake="${SCRIMMAGE_BAKE:-}"

# Machines booted from a baked image already have these.
if ! dpkg -s docker.io amazon-ecr-credential-helper >/dev/null 2>&1; then
  log "Installing packages"
  apt-get update -q
  apt-get install -yq --no-install-recommends docker.io amazon-ecr-credential-helper ca-certificates curl
fi
setup_worker_user
install_app
setup_docker "${IMAGE_REPO%%/*}"

image="$IMAGE_REPO:$COMMIT"

log "Pulling the game image $image"
# The main server publishes it while it installs; wait for it if needed.
for _ in $(seq 180); do
  docker pull --quiet "$image" && break
  sleep 20
done
docker image inspect "$image" >/dev/null
# Older game images only waste disk.
docker images --format '{{.Repository}}:{{.Tag}}' | grep -vxF "$image" | xargs -r docker rmi >/dev/null 2>&1 || true

if [ -n "$bake" ]; then
  log "Baked: powering off to be imaged"
  apt-get clean
  # Machines started from the image run their user data again, as new instances.
  cloud-init clean --logs
  shutdown -h now
  exit 0
fi

write_worker_env \
  SERVER_URL="$SERVER_URL" WORKER_NAME="$instance_id" GAME_IMAGE="$image" \
  WORKER_MACHINE="$(imds instance-type)" \
  SCRIMMAGE_COMMIT="$COMMIT" AWS_REGION="$AWS_REGION" WORKER_TOKEN_SECRET="$WORKER_TOKEN_SECRET" \
  FLEET_WORKER=1
install_units scrimmage-worker.service
systemctl daemon-reload
systemctl enable --now scrimmage-worker
log "Worker $instance_id is running"
