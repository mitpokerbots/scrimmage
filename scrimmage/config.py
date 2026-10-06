"""
Process configuration, read once from the environment.

Everything an operator might reasonably change at runtime (game parameters,
team size, challenge rules, match capacity) lives in the database instead;
see settings.py.
"""

from __future__ import annotations

import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path

# touchstone: Apache + Shibboleth authenticate /auth/touchstone and pass the
#             user's eduPersonPrincipalName in X-Remote-User.
# dev:        anyone can log in as any kerberos. Never use on a public host.
AUTH_MODES = ("touchstone", "dev")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    public_url: str
    auth_mode: str
    bootstrap_admins: frozenset[str]
    contact_email: str
    secret_key: str
    # Shared by the web app and every worker; authenticates the worker API.
    worker_token: str
    # Git commit of the running code. Workers on another commit are retired.
    commit: str
    # The worker fleet's Auto Scaling group. Empty = no fleet, only the main
    # server's own worker.
    fleet_group: str
    aws_region: str
    # SNS topic that emails the admins (AlertEmail in CloudFormation). Optional.
    alert_topic: str
    # Worker settings.
    server_url: str
    game_image: str
    worker_name: str
    worker_cores: int  # 0 = all of the machine's CPUs
    machine_type: str  # EC2 instance type, for game records

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "scrimmage.sqlite3"

    @property
    def secure_cookies(self) -> bool:
        return self.public_url.startswith("https://")


def _local_secret(data_dir: Path, name: str) -> str:
    """A random secret generated once and kept (0600) in the data dir."""
    path = data_dir / name
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # another process created it first
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
    return path.read_text().strip()


def _worker_token(data_dir: Path, region: str) -> str:
    if os.environ.get("WORKER_TOKEN"):
        return os.environ["WORKER_TOKEN"]
    secret_id = os.environ.get("WORKER_TOKEN_SECRET", "")
    if secret_id:
        import boto3  # noqa: PLC0415 -- only needed on AWS

        client = boto3.client("secretsmanager", region_name=region or None)
        value: str = client.get_secret_value(SecretId=secret_id)["SecretString"]
        return value.strip()
    return _local_secret(data_dir, "worker_token")


def load_config() -> Config:
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).resolve()
    auth_mode = os.environ.get("AUTH_MODE", "touchstone").strip().lower()
    if auth_mode not in AUTH_MODES:
        raise ValueError(f"AUTH_MODE={auth_mode!r}; expected one of {AUTH_MODES}")
    public_url = os.environ.get("PUBLIC_URL", "http://localhost:8000").rstrip("/")
    admins = frozenset(
        k.strip().lower() for k in os.environ.get("ADMINS", "").split(",") if k.strip()
    )
    region = os.environ.get("AWS_REGION", "")
    return Config(
        data_dir=data_dir,
        public_url=public_url,
        auth_mode=auth_mode,
        bootstrap_admins=admins,
        contact_email=os.environ.get("CONTACT_EMAIL", "pokerbots@mit.edu"),
        secret_key=os.environ.get("SECRET_KEY") or _local_secret(data_dir, "secret_key"),
        worker_token=_worker_token(data_dir, region),
        commit=os.environ.get("SCRIMMAGE_COMMIT", "dev"),
        fleet_group=os.environ.get("FLEET_GROUP", ""),
        aws_region=region,
        alert_topic=os.environ.get("ALERT_TOPIC", ""),
        server_url=os.environ.get("SERVER_URL", "http://127.0.0.1:8000").rstrip("/"),
        game_image=os.environ.get("GAME_IMAGE", "scrimmage-game:latest"),
        worker_name=os.environ.get("WORKER_NAME", socket.gethostname()),
        worker_cores=int(os.environ.get("WORKER_CORES", "0")),
        machine_type=os.environ.get("WORKER_MACHINE", ""),
    )
