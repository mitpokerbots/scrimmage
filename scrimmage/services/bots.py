"""
Bot archives: validation at upload time and extraction on the worker.

Everything that can be checked statically is checked when the zip is
uploaded, so a broken upload is rejected immediately with a clear message
instead of losing games later.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scrimmage.db import now, transaction
from scrimmage.services import builds
from scrimmage.services.errors import UserError
from scrimmage.services.storage import Storage
from scrimmage.settings import Settings

# Bots are unpacked into a tmpfs that counts against their memory share.
MAX_UNCOMPRESSED_BYTES = 1 << 30
MAX_FILES = 20_000
MAX_NAME_LENGTH = 60
IGNORED_PREFIXES = ("__MACOSX/",)


@dataclass(frozen=True)
class BotArchive:
    root: str
    size_bytes: int
    sha256: str


def _is_ignored(name: str) -> bool:
    base = PurePosixPath(name).name
    return name.startswith(IGNORED_PREFIXES) or base.startswith("._") or base == ".DS_Store"


def _safe_parts(name: str) -> tuple[str, ...]:
    """Split an archive member name, rejecting anything that could escape."""
    if "\\" in name or name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise UserError(f"Zip contains an unsafe path: {name!r}")
    parts = tuple(p for p in name.split("/") if p not in ("", "."))
    if ".." in parts:
        raise UserError(f"Zip contains an unsafe path: {name!r}")
    return parts


def _members(zf: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, tuple[str, ...]]]:
    members = []
    for info in zf.infolist():
        if _is_ignored(info.filename):
            continue
        parts = _safe_parts(info.filename)
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise UserError(f"Zip contains a symlink, which is not allowed: {info.filename!r}")
        if info.flag_bits & 0x1:
            raise UserError("Zip is encrypted.")
        if parts:
            members.append((info, parts))
    return members


def _parse_commands(raw: bytes) -> None:
    try:
        commands = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UserError(f"commands.json is not valid JSON: {exc}") from None
    if not isinstance(commands, dict):
        raise UserError('commands.json must be an object with "build" and "run" lists.')
    for key in ("build", "run"):
        value = commands.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise UserError(f'commands.json "{key}" must be a list of strings.')
    if not commands["run"]:
        raise UserError('commands.json "run" must not be empty.')


def inspect_zip(path: Path) -> BotArchive:
    """Validate an uploaded bot zip. Raises UserError with a readable reason."""
    if not zipfile.is_zipfile(path):
        raise UserError("That file is not a zip archive.")
    try:
        with zipfile.ZipFile(path) as zf:
            members = _members(zf)
            if len(members) > MAX_FILES:
                raise UserError(f"Zip has more than {MAX_FILES} files.")
            total = sum(info.file_size for info, _ in members)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise UserError("Zip is larger than 1 GiB once extracted.")
            candidates = [
                (parts, info)
                for info, parts in members
                if parts[-1] == "commands.json" and not info.is_dir()
            ]
            if not candidates:
                raise UserError("Zip has no commands.json.")
            depth = min(len(parts) for parts, _ in candidates)
            shallowest = [(p, i) for p, i in candidates if len(p) == depth]
            if len(shallowest) > 1:
                where = ", ".join("/".join(p) for p, _ in shallowest)
                raise UserError(f"Zip has more than one top-level commands.json: {where}")
            parts, info = shallowest[0]
            _parse_commands(zf.read(info))
            bad = zf.testzip()
            if bad is not None:
                raise UserError(f"Zip is corrupt (bad CRC in {bad!r}).")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, EOFError) as exc:
        raise UserError(f"Zip could not be read: {exc}") from None

    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return BotArchive(
        root="/".join(parts[:-1]), size_bytes=path.stat().st_size, sha256=digest.hexdigest()
    )


def extract(zip_path: Path, root: str, dest: Path) -> None:
    """Extract the bot directory ``root`` of a validated zip into ``dest``.

    Only members under ``root`` are written, with ``root`` stripped. Unix
    executable bits are preserved so prebuilt scripts stay runnable.
    """
    prefix = tuple(p for p in root.split("/") if p)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info, parts in _members(zf):
            if parts[: len(prefix)] != prefix or len(parts) == len(prefix):
                continue
            target = dest.joinpath(*parts[len(prefix) :])
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            executable = (info.external_attr >> 16) & 0o111
            os.chmod(target, 0o755 if executable else 0o644)


def create(
    conn: sqlite3.Connection,
    storage: Storage,
    settings: Settings,
    team_id: int,
    user_id: int | None,
    name: str,
    upload: Path,
) -> int:
    """Validate and store ``upload``, and queue its build.

    The bot becomes the team's current bot when the build succeeds (builds.py).
    """
    storage.require_space()
    name = " ".join(name.split())[:MAX_NAME_LENGTH]
    archive = inspect_zip(upload)
    with transaction(conn):
        recent = conn.execute(
            "SELECT count(*) FROM bots WHERE team_id = ? AND created_at > ?",
            (team_id, now() - 86400),
        ).fetchone()[0]
        if recent >= settings.number("bot_uploads_per_day"):
            raise UserError("Your team has hit its upload limit for the last 24 hours.")
        if not name:
            count = conn.execute("SELECT count(*) FROM bots WHERE team_id = ?", (team_id,))
            name = f"v{count.fetchone()[0] + 1}"
        cur = conn.execute(
            "INSERT INTO bots (team_id, name, uploaded_by, size_bytes, sha256, root, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (team_id, name, user_id, archive.size_bytes, archive.sha256, archive.root, now()),
        )
        bot_id = cur.lastrowid
        assert bot_id is not None
        builds.request(conn, bot_id)
        os.replace(upload, storage.bot_zip(bot_id))
    return bot_id


def set_current(conn: sqlite3.Connection, team_id: int, bot_id: int) -> None:
    with transaction(conn):
        builds.require_ready(conn, bot_id)
        cur = conn.execute(
            "UPDATE teams SET current_bot_id = ? WHERE id = ? AND EXISTS "
            "(SELECT 1 FROM bots WHERE id = ? AND team_id = ? AND NOT is_deleted)",
            (bot_id, team_id, bot_id, team_id),
        )
        if cur.rowcount != 1:
            raise UserError("That bot does not belong to your team.")


def delete(conn: sqlite3.Connection, team_id: int, bot_id: int) -> None:
    """Hide a bot from the team. Its file is kept: past games reference it."""
    with transaction(conn):
        current = conn.execute(
            "SELECT current_bot_id FROM teams WHERE id = ?", (team_id,)
        ).fetchone()[0]
        if current == bot_id:
            raise UserError("You cannot delete your current bot. Pick another one first.")
        cur = conn.execute(
            "UPDATE bots SET is_deleted = 1 WHERE id = ? AND team_id = ?", (bot_id, team_id)
        )
        if cur.rowcount != 1:
            raise UserError("That bot does not belong to your team.")
