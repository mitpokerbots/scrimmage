-- All timestamps are integer Unix seconds (UTC).

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE teams (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    elo            REAL    NOT NULL DEFAULT 1500,
    wins           INTEGER NOT NULL DEFAULT 0,
    losses         INTEGER NOT NULL DEFAULT 0,
    ties           INTEGER NOT NULL DEFAULT 0,
    current_bot_id INTEGER REFERENCES bots (id),
    is_disabled    INTEGER NOT NULL DEFAULT 0,
    -- Reference teams accept every challenge automatically.
    is_reference   INTEGER NOT NULL DEFAULT 0,
    created_at     INTEGER NOT NULL
) STRICT;

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    kerberos      TEXT    NOT NULL UNIQUE,
    display_name  TEXT,
    team_id       INTEGER REFERENCES teams (id),
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL,
    last_login_at INTEGER
) STRICT;
CREATE INDEX users_team ON users (team_id);

-- At most one outstanding request per user.
CREATE TABLE join_requests (
    user_id    INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    team_id    INTEGER NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL
) STRICT;
CREATE INDEX join_requests_team ON join_requests (team_id);

CREATE TABLE bots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL REFERENCES teams (id),
    name        TEXT    NOT NULL,
    uploaded_by INTEGER REFERENCES users (id) ON DELETE SET NULL,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT    NOT NULL,
    -- Directory inside the zip that holds commands.json ('' = zip root).
    root        TEXT    NOT NULL,
    wins        INTEGER NOT NULL DEFAULT 0,
    losses      INTEGER NOT NULL DEFAULT 0,
    ties        INTEGER NOT NULL DEFAULT 0,
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
) STRICT;
CREATE INDEX bots_team ON bots (team_id);

-- A bot's "build" command runs once, after the upload, alone in a sandbox;
-- every game uses the result. Builds are queued and leased to workers like
-- games, and run before them.
CREATE TABLE builds (
    id          INTEGER PRIMARY KEY,
    bot_id      INTEGER NOT NULL UNIQUE REFERENCES bots (id),
    status      TEXT    NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed')),
    worker      TEXT,
    lease_until INTEGER,
    error       TEXT,
    seconds     REAL,
    size_bytes  INTEGER,
    sha256      TEXT,
    created_at  INTEGER NOT NULL,
    finished_at INTEGER
) STRICT;
CREATE INDEX builds_queue ON builds (status, id);

CREATE TABLE game_requests (
    id            INTEGER PRIMARY KEY,
    challenger_id INTEGER NOT NULL REFERENCES teams (id),
    opponent_id   INTEGER NOT NULL REFERENCES teams (id),
    status        TEXT    NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'cancelled')),
    created_at    INTEGER NOT NULL,
    decided_at    INTEGER
) STRICT;
CREATE INDEX game_requests_opponent ON game_requests (opponent_id, status);
CREATE INDEX game_requests_challenger ON game_requests (challenger_id, status);

CREATE TABLE tournaments (
    id             INTEGER PRIMARY KEY,
    title          TEXT    NOT NULL,
    games_per_pair INTEGER NOT NULL CHECK (games_per_pair > 0),
    is_private     INTEGER NOT NULL DEFAULT 0,
    -- 'running' until every game has finished.
    status         TEXT    NOT NULL CHECK (status IN ('running', 'done')),
    created_by     INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at     INTEGER NOT NULL,
    finished_at    INTEGER
) STRICT;

CREATE TABLE tournament_entries (
    id            INTEGER PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments (id) ON DELETE CASCADE,
    team_id       INTEGER NOT NULL REFERENCES teams (id),
    bot_id        INTEGER NOT NULL REFERENCES bots (id),
    -- Fitted rating and its 95% error bar, refreshed while the tournament runs.
    rating        REAL,
    rating_error  REAL,
    UNIQUE (tournament_id, team_id)
) STRICT;

-- Scrimmage and tournament games share one table, which is also the work
-- queue: workers claim the lowest (priority, id) queued rows, and hold each
-- running game under a lease they renew; expired leases are requeued.
CREATE TABLE games (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL CHECK (kind IN ('scrimmage', 'tournament')),
    tournament_id INTEGER REFERENCES tournaments (id) ON DELETE CASCADE,
    request_id    INTEGER REFERENCES game_requests (id),
    -- Team charged against spawn_limit_per_team (scrimmages only).
    initiator_id  INTEGER REFERENCES teams (id),
    team_a_id     INTEGER NOT NULL REFERENCES teams (id),
    team_b_id     INTEGER NOT NULL REFERENCES teams (id),
    bot_a_id      INTEGER NOT NULL REFERENCES bots (id),
    bot_b_id      INTEGER NOT NULL REFERENCES bots (id),
    status        TEXT    NOT NULL CHECK (status IN ('queued', 'running', 'done', 'error')),
    priority      INTEGER NOT NULL,
    -- Hardware, fixed when the game is created (see services/hardware.py).
    cores         INTEGER NOT NULL DEFAULT 1,
    bot_memory_mb INTEGER NOT NULL DEFAULT 1536,
    worker        TEXT,
    lease_until   INTEGER,
    score_a       INTEGER,
    score_b       INTEGER,
    winner        TEXT    CHECK (winner IN ('a', 'b', 'tie')),
    elo_a_before  REAL,
    elo_b_before  REAL,
    elo_a_after   REAL,
    elo_b_after   REAL,
    error         TEXT,
    -- What the worker measured (JSON): durations, time each bot used, hands
    -- played, why a bot was disconnected, machine type, ...
    stats         TEXT,
    created_at    INTEGER NOT NULL,
    started_at    INTEGER,
    finished_at   INTEGER,
    CHECK ((kind = 'tournament') = (tournament_id IS NOT NULL))
) STRICT;
CREATE INDEX games_queue ON games (status, priority, id);
CREATE INDEX games_team_a ON games (team_a_id, id);
CREATE INDEX games_team_b ON games (team_b_id, id);
CREATE INDEX games_tournament ON games (tournament_id, status);
CREATE INDEX games_initiator ON games (initiator_id, status);
CREATE INDEX games_leases ON games (status, lease_until);

CREATE TABLE announcements (
    id         INTEGER PRIMARY KEY,
    author_id  INTEGER REFERENCES users (id) ON DELETE SET NULL,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL,
    is_public  INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
) STRICT;

-- One-time login links minted from the server shell (`scrimmage login-link`).
CREATE TABLE login_tokens (
    token_hash TEXT    PRIMARY KEY,
    kerberos   TEXT    NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at    INTEGER
) STRICT;

CREATE TABLE worker_status (
    name       TEXT    PRIMARY KEY,
    cores      INTEGER NOT NULL,
    busy_cores INTEGER NOT NULL,
    games      INTEGER NOT NULL DEFAULT 0,
    commit_id  TEXT    NOT NULL DEFAULT '',
    started_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
) STRICT;
