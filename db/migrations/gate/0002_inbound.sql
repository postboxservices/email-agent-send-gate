-- Replies from people (and opt-outs), kept for the agent to read through GET /replies.
-- Automatic replies and bounces are recorded only as events.
CREATE TABLE replies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at     TEXT NOT NULL,
    from_addr       TEXT NOT NULL,
    subject         TEXT NOT NULL,
    text            TEXT NOT NULL,      -- the new text only: quotes and our footer removed
    classification  TEXT NOT NULL,      -- reply | opt_out
    label           TEXT NOT NULL DEFAULT '',  -- the optional classifier's label
    send_id         TEXT,               -- the send it answers, when In-Reply-To matched
    message_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX replies_from ON replies (from_addr);

-- Every inbound message id seen, so redelivered webhooks are processed once.
CREATE TABLE inbound_seen (
    message_id  TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

-- gate selftest messages that arrived through inbound/ instead of IMAP (raw, as received).
CREATE TABLE inbound_selftest (
    marker      TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    raw         BLOB NOT NULL
);
