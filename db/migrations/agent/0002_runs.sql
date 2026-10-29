-- One row per agent run: what started it, what it cost, how it ended.
CREATE TABLE runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    trigger         TEXT NOT NULL,      -- daily | replies | task
    task            TEXT NOT NULL,
    model           TEXT NOT NULL,
    turns           INTEGER NOT NULL DEFAULT 0,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    stop_reason     TEXT,
    summary         TEXT NOT NULL DEFAULT ''
);

-- Small key/value state, such as the last reply id the agent has read.
CREATE TABLE agent_state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
