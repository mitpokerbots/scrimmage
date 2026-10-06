# Shared by install.sh (main server) and install-worker.sh (fleet workers).
# shellcheck shell=bash

APP=/opt/scrimmage
ETC=/etc/scrimmage
# Bot cache and scratch space: disposable, so not on the backed-up data volume.
WORKER_DATA=/var/lib/scrimmage-worker

log() { echo "==> $*"; }

# imds PATH: EC2 instance metadata (fails off EC2).
imds() {
  local token
  token=$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60") || return 1
  curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $token" "http://169.254.169.254/latest/meta-data/$1"
}

install_app() {
  if ! command -v uv >/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  fi
  export UV_PYTHON_INSTALL_DIR=/opt/uv/python UV_CACHE_DIR=/var/cache/uv
  (cd "$APP" && uv sync --frozen --no-dev --python 3.13 --quiet)
  chmod -R a+rX "$APP" /opt/uv
}

# setup_docker [ecr-registry]: Docker with small local logs, and ECR logins
# through the instance role when a registry is given. Keeps any existing
# daemon settings.
setup_docker() {
  install -d /etc/docker
  python3 - <<'EOF'
import json, pathlib
path = pathlib.Path("/etc/docker/daemon.json")
config = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
config.update({"log-driver": "local", "log-opts": {"max-size": "10m"}})
path.write_text(json.dumps(config, indent=2) + "\n")
EOF
  if [ -n "${1:-}" ]; then
    install -d -m 0700 /root/.docker
    printf '{ "credHelpers": { "%s": "ecr-login" } }\n' "$1" > /root/.docker/config.json
  fi
  systemctl enable docker
  systemctl restart docker
}

# The worker runs as its own user: the only account that can use Docker
# (which is root-equivalent), and one with no access to the database.
setup_worker_user() {
  id scrimmage-worker >/dev/null 2>&1 \
    || useradd --system --home-dir "$WORKER_DATA" --shell /usr/sbin/nologin scrimmage-worker
  usermod -aG docker scrimmage-worker
  install -d -m 0700 -o scrimmage-worker -g scrimmage-worker "$WORKER_DATA"
}

# write_worker_env KEY=VALUE...: the worker service's environment.
write_worker_env() {
  printf '%s\n' "DATA_DIR=$WORKER_DATA" "$@" > "$ETC/worker.env"
  chmod 0640 "$ETC/worker.env"
  chgrp scrimmage-worker "$ETC/worker.env"
}

install_units() {
  install -m 0644 "$APP"/deploy/systemd/"$1" /etc/systemd/system/
}
