PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE users (
    id INTEGER NOT NULL, 
    kerberos VARCHAR(128), 
    team_id INTEGER NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(team_id) REFERENCES teams (id)
);
CREATE TABLE game_requests (
    id INTEGER NOT NULL, 
    challenger_id INTEGER NOT NULL, 
    opponent_id INTEGER NOT NULL, 
    status VARCHAR(10) NOT NULL, 
    create_time DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(challenger_id) REFERENCES teams (id), 
    FOREIGN KEY(opponent_id) REFERENCES teams (id), 
    CONSTRAINT gamerequeststatus CHECK (status IN ('accepted', 'challenged', 'rejected'))
);
CREATE TABLE settings (
    id INTEGER NOT NULL, 
    "key" VARCHAR(128), 
    value VARCHAR(128), 
    PRIMARY KEY (id)
);
CREATE TABLE teams (
    id INTEGER NOT NULL, 
    name VARCHAR(128), 
    elo FLOAT, 
    wins INTEGER, 
    losses INTEGER, 
    current_bot_id INTEGER, 
    is_disabled BOOLEAN, 
    must_autoaccept BOOLEAN, 
    PRIMARY KEY (id), 
    UNIQUE (name), 
    FOREIGN KEY(current_bot_id) REFERENCES bots (id), 
    CHECK (is_disabled IN (0, 1)), 
    CHECK (must_autoaccept IN (0, 1))
);
CREATE TABLE games (
    id INTEGER NOT NULL, 
    game_request_id INTEGER NOT NULL, 
    initiator_id INTEGER NOT NULL, 
    challenger_id INTEGER NOT NULL, 
    opponent_id INTEGER NOT NULL, 
    challenger_elo FLOAT, 
    opponent_elo FLOAT, 
    create_time DATETIME, 
    completed_time DATETIME, 
    status VARCHAR(14) NOT NULL, 
    challenger_bot_id INTEGER NOT NULL, 
    opponent_bot_id INTEGER NOT NULL, 
    winner_id INTEGER, 
    loser_id INTEGER, 
    log_s3_key VARCHAR(256), 
    PRIMARY KEY (id), 
    UNIQUE (game_request_id), 
    FOREIGN KEY(game_request_id) REFERENCES game_requests (id), 
    FOREIGN KEY(initiator_id) REFERENCES teams (id), 
    FOREIGN KEY(challenger_id) REFERENCES teams (id), 
    FOREIGN KEY(opponent_id) REFERENCES teams (id), 
    CONSTRAINT gamestatus CHECK (status IN ('completed', 'created', 'in_progress', 'internal_error')), 
    FOREIGN KEY(challenger_bot_id) REFERENCES bots (id), 
    FOREIGN KEY(opponent_bot_id) REFERENCES bots (id), 
    FOREIGN KEY(winner_id) REFERENCES teams (id), 
    FOREIGN KEY(loser_id) REFERENCES teams (id)
);
CREATE TABLE bots (
    id INTEGER NOT NULL, 
    team_id INTEGER NOT NULL, 
    name VARCHAR(128) NOT NULL, 
    s3_key VARCHAR(256) NOT NULL, 
    wins INTEGER, 
    losses INTEGER, 
    create_time DATETIME, 
    PRIMARY KEY (id), 
    FOREIGN KEY(team_id) REFERENCES teams (id)
);
CREATE UNIQUE INDEX ix_users_kerberos ON users (kerberos);
CREATE UNIQUE INDEX ix_settings_value ON settings (value);
CREATE UNIQUE INDEX ix_settings_key ON settings ("key");
COMMIT;
