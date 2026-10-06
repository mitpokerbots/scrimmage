"""HTTP client for the worker API (scrimmage/web/worker_api.py)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

TIMEOUT = (10, 300)  # connect, read


class Outdated(Exception):
    """The server runs a different commit; this worker should be replaced."""


class LeaseLost(Exception):
    """The server gave the job to someone else (our lease expired)."""


class ServerClient:
    def __init__(self, url: str, token: str, worker: str, commit: str) -> None:
        self.url = url.rstrip("/") + "/api/worker"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "X-Worker": worker,
                "X-Scrimmage-Commit": commit,
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        response = self.session.request(method, self.url + path, timeout=TIMEOUT, **kwargs)
        if response.status_code == 409:
            error = response.json().get("error")
            if error == "outdated":
                raise Outdated(response.json().get("commit"))
            raise LeaseLost(error)
        response.raise_for_status()
        return response

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = self._request("POST", path, json=body or {}).json()
        return result

    def start(self, status: dict[str, Any]) -> None:
        """Announce this worker, retrying until the server is reachable."""
        delay = 2.0
        while True:
            try:
                requeued = self._post("/start", status)["requeued"]
                if requeued:
                    log.info("Server requeued %d jobs this worker held before", requeued)
                return
            except requests.RequestException as exc:
                log.warning("Server not reachable (%s); retrying in %.0fs", exc, delay)
                time.sleep(delay)
                delay = min(delay * 2, 30)

    def stop(self) -> None:
        self._post("/stop")

    def heartbeat(
        self, status: dict[str, Any], games: list[int], builds: list[int]
    ) -> dict[str, list[int]]:
        """Renew leases; returns {"cancel": games, "cancel_builds": builds} taken back."""
        reply: dict[str, list[int]] = self._post(
            "/heartbeat", {**status, "games": games, "builds": builds}
        )
        return reply

    def claim(self, status: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """``status`` = {"total", "free"}. Returns {"builds": [...], "games": [...]}."""
        jobs: dict[str, list[dict[str, Any]]] = self._post("/claim", status)
        return jobs

    def _download(self, path: str, sha256: str, dest: Path) -> None:
        digest = hashlib.sha256()
        with self._request("GET", path, stream=True) as response, open(dest, "wb") as f:
            for chunk in response.iter_content(1 << 20):
                digest.update(chunk)
                f.write(chunk)
        if digest.hexdigest() != sha256:
            raise RuntimeError(f"{path} download is corrupt (sha256 mismatch)")

    def download_source(self, bot_id: int, sha256: str, dest: Path) -> None:
        self._download(f"/bots/{bot_id}/source", sha256, dest)

    def download_build(self, bot_id: int, sha256: str, dest: Path) -> None:
        self._download(f"/bots/{bot_id}/build", sha256, dest)

    def submit(self, game_id: int, outcome: dict[str, Any], logs: dict[str, bytes]) -> None:
        files = {kind: (f"{kind}.log.gz", data, "application/gzip") for kind, data in logs.items()}
        self._request(
            "POST",
            f"/games/{game_id}/result",
            data={"outcome": json.dumps(outcome)},
            files=files,
        )

    def submit_build(
        self, build_id: int, outcome: dict[str, Any], log: bytes, archive: Path | None
    ) -> None:
        files: dict[str, Any] = {"log": ("build.log.gz", log, "application/gzip")}
        with contextlib.ExitStack() as stack:
            if archive is not None:
                handle = stack.enter_context(open(archive, "rb"))
                files["bot"] = ("bot.tar.gz", handle, "application/gzip")
            self._request(
                "POST",
                f"/builds/{build_id}/result",
                data={"outcome": json.dumps(outcome)},
                files=files,
            )
