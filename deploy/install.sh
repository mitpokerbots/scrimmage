#!/bin/bash
# Install or update the main scrimmage server on Ubuntu 24.04. Idempotent: the
# first boot runs it from EC2 user data, and deploy/update.sh reruns it.
#
# Reads /etc/scrimmage/install.env:
#   DOMAIN, ADMINS, CONTACT_EMAIL     site settings
#   DATA_VOLUME_ID                    EBS volume to mount at /srv (optional)
#   AWS_REGION, FLEET_GROUP, WORKER_TOKEN_SECRET, IMAGE_REPO
#                                     the worker fleet (optional: without it
#                                     only this server's own worker plays)
#   WORKER_TEMPLATE, BAKE_SUBNET      bake worker machine images (optional)
#   ARCHIVE_BUCKET                    S3 bucket for the nightly archive (optional)
#   ALERT_TOPIC                       SNS topic for alert emails (optional)
#
# Everything worth keeping is on the data volume, so a rebuilt instance picks
# up where the old one left off:
#   /srv/scrimmage    database, bots, logs (DATA_DIR)
#   /srv/shibboleth   the SP keys registered with MIT, and the Okta external ID
#   /srv/apache-md    Let's Encrypt account and certificates
set -euo pipefail

# shellcheck source=deploy/lib.sh
source "$(dirname "$0")/lib.sh"
MOUNT=/srv
DATA=/srv/scrimmage

source "$ETC/install.env"
: "${DOMAIN:?} ${CONTACT_EMAIL:?}"
ADMINS="${ADMINS:-}"
AWS_REGION="${AWS_REGION:-}" FLEET_GROUP="${FLEET_GROUP:-}"
WORKER_TOKEN_SECRET="${WORKER_TOKEN_SECRET:-}" IMAGE_REPO="${IMAGE_REPO:-}"
WORKER_TEMPLATE="${WORKER_TEMPLATE:-}" BAKE_SUBNET="${BAKE_SUBNET:-}" ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-}"
ALERT_TOPIC="${ALERT_TOPIC:-}"
COMMIT=$(git -C "$APP" rev-parse HEAD)
export DEBIAN_FRONTEND=noninteractive

# --- Packages --------------------------------------------------------------------
log "Installing packages"
apt-get update -q
apt-get install -yq --no-install-recommends \
  apache2 libapache2-mod-shib docker.io docker-buildx amazon-ecr-credential-helper git curl sqlite3 \
  ca-certificates unattended-upgrades chrony
# Kernel security updates only take effect after a reboot; do it at 5:30am ET.
cat > /etc/apt/apt.conf.d/52scrimmage-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "09:30";
EOF

# --- Data volume -------------------------------------------------------------------
if [ -n "${DATA_VOLUME_ID:-}" ] && ! mountpoint -q "$MOUNT"; then
  device="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${DATA_VOLUME_ID//-/}"
  log "Waiting for data volume $DATA_VOLUME_ID"
  for _ in $(seq 120); do [ -e "$device" ] && break; sleep 5; done
  [ -e "$device" ] || { echo "Data volume $DATA_VOLUME_ID never appeared" >&2; exit 1; }
  if ! blkid "$device" >/dev/null; then
    log "Formatting new data volume"
    mkfs.ext4 -q -L scrimmage-data "$device"
  fi
  uuid=$(blkid -s UUID -o value "$device")
  mkdir -p "$MOUNT"
  grep -q "$uuid" /etc/fstab || echo "UUID=$uuid $MOUNT ext4 defaults,noatime,nofail 0 2" >> /etc/fstab
  mount "$MOUNT"
  # Grow the filesystem if the volume was enlarged in CloudFormation.
  resize2fs "$device" >/dev/null 2>&1 || true
fi

# --- Users and directories ------------------------------------------------------------
# scrimmage runs the website: database access, no Docker.
id scrimmage >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin scrimmage
gpasswd -d scrimmage docker >/dev/null 2>&1 || true
# Apache reaches gunicorn's socket through the scrimmage group.
usermod -aG scrimmage www-data
install -d -m 0755 "$MOUNT"
install -d -m 0700 -o scrimmage -g scrimmage "$DATA"
install -d -m 0755 -o root -g root "$MOUNT/apache-md"
install -d -m 0700 -o root -g root "$MOUNT/shibboleth"
setup_worker_user

# --- Application ----------------------------------------------------------------------
log "Installing the application ($COMMIT)"
install_app

cat > "$ETC/env" <<EOF
DATA_DIR=$DATA
PUBLIC_URL=https://$DOMAIN
AUTH_MODE=touchstone
ADMINS=$ADMINS
CONTACT_EMAIL=$CONTACT_EMAIL
SCRIMMAGE_COMMIT=$COMMIT
AWS_REGION=$AWS_REGION
FLEET_GROUP=$FLEET_GROUP
WORKER_TOKEN_SECRET=$WORKER_TOKEN_SECRET
WORKER_TEMPLATE=$WORKER_TEMPLATE
BAKE_SUBNET=$BAKE_SUBNET
ARCHIVE_BUCKET=$ARCHIVE_BUCKET
ALERT_TOPIC=$ALERT_TOPIC
EOF
# Local overrides survive reinstalls.
touch "$ETC/env.local"
chmod 0640 "$ETC/env" "$ETC/env.local"
chgrp scrimmage "$ETC/env" "$ETC/env.local"
install -m 0755 "$APP/deploy/scrimmage-cli" /usr/local/bin/scrimmage

# --- Docker and the game image ------------------------------------------------------------
setup_docker "${IMAGE_REPO%%/*}"
log "Building the game image (several minutes the first time)"
docker build --quiet -t scrimmage-game:latest "$APP/game"
if [ -n "$IMAGE_REPO" ]; then
  log "Publishing the game image for fleet workers"
  docker tag scrimmage-game:latest "$IMAGE_REPO:$COMMIT"
  docker push --quiet "$IMAGE_REPO:$COMMIT"
  docker rmi "$IMAGE_REPO:$COMMIT" >/dev/null
fi
docker image prune -f >/dev/null

# This server's own worker: named "main", talks to the API over loopback.
write_worker_env \
  SERVER_URL=http://127.0.0.1:8080 WORKER_NAME=main GAME_IMAGE=scrimmage-game:latest \
  WORKER_MACHINE="$(imds instance-type || uname -m)" \
  SCRIMMAGE_COMMIT="$COMMIT" AWS_REGION="$AWS_REGION" WORKER_TOKEN_SECRET="$WORKER_TOKEN_SECRET"
if [ -z "$WORKER_TOKEN_SECRET" ]; then
  # No Secrets Manager: share a token file between the website and the worker.
  token_file="$DATA/worker_token"
  [ -s "$token_file" ] || (umask 077 && head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$token_file")
  chown scrimmage:scrimmage "$token_file"
  echo "WORKER_TOKEN=$(cat "$token_file")" >> "$ETC/worker.env"
fi

# --- Shibboleth (Touchstone) --------------------------------------------------------------
log "Configuring Shibboleth"
"$APP/deploy/touchstone.sh" configure

# --- Apache ------------------------------------------------------------------------------------
log "Configuring Apache"
a2enmod -q ssl md proxy proxy_http headers http2 shib deflate alias >/dev/null
a2dissite -q 000-default >/dev/null 2>&1 || true
sed -e "s|__DOMAIN__|$DOMAIN|g" -e "s|__CONTACT_EMAIL__|$CONTACT_EMAIL|g" \
  -e "s|__MD_STORE__|$MOUNT/apache-md|g" -e "s|__APP__|$APP|g" \
  "$APP/deploy/apache-scrimmage.conf" > /etc/apache2/sites-available/scrimmage.conf
a2ensite -q scrimmage >/dev/null
apache2ctl configtest

# --- Services ------------------------------------------------------------------------------------
log "Starting services"
systemctl disable --now scrimmage-backup.timer >/dev/null 2>&1 || true
rm -f /etc/systemd/system/scrimmage-backup.*
for unit in scrimmage-web.service scrimmage-worker.service scrimmage-maintain.service \
  scrimmage-maintain.timer scrimmage-certs.service scrimmage-certs.timer \
  scrimmage-archive.service scrimmage-archive.timer scrimmage-bake.service; do
  install_units "$unit"
done
systemctl daemon-reload
systemctl enable --now shibd apache2 scrimmage-web scrimmage-worker \
  scrimmage-maintain.timer scrimmage-certs.timer
if [ -n "$ARCHIVE_BUCKET" ]; then
  systemctl enable --now scrimmage-archive.timer
fi
systemctl restart shibd scrimmage-web scrimmage-worker
systemctl reload apache2
if [ -n "$WORKER_TEMPLATE" ]; then
  # Fleet machines boot from an image with this commit already installed. Takes
  # about 15 minutes, in the background; until then they install at boot.
  log "Baking the worker machine image in the background (journalctl -u scrimmage-bake)"
  systemctl restart --no-block scrimmage-bake
fi

log "Done. Site: https://$DOMAIN/"
"$APP/deploy/touchstone.sh" status || true
