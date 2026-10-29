-- The gate's database. Only the gate process opens it.

-- Every request the agent makes, including the refused ones, so a person can see
-- what the agent tried and why the gate said no.
CREATE TABLE send_requests (
    id                  TEXT PRIMARY KEY,   -- uuid hex; also the unsubscribe token payload
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    to_addr             TEXT NOT NULL,      -- normalized, lower case
    to_domain           TEXT NOT NULL,
    to_name             TEXT NOT NULL DEFAULT '',
    from_addr           TEXT NOT NULL,      -- set by the gate from its config, never by the agent
    sending_domain      TEXT NOT NULL,
    subject             TEXT NOT NULL,
    body_text           TEXT NOT NULL,
    contact_ref         TEXT NOT NULL DEFAULT '',  -- the agent's contact id, for provenance
    reason              TEXT NOT NULL DEFAULT '',  -- why the agent picked this person (shown to reviewers)
    status              TEXT NOT NULL,      -- refused | pending_review | rejected | approved
                                            -- | dry_run | sent | failed
    refusals            TEXT NOT NULL DEFAULT '[]',  -- JSON list of {code, detail}
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_note         TEXT,
    sent_at             TEXT,
    provider            TEXT,
    provider_message_id TEXT,
    message_id          TEXT,
    outbox_path         TEXT,
    error               TEXT
);
CREATE INDEX send_requests_to_addr ON send_requests (to_addr, created_at);
CREATE INDEX send_requests_to_domain ON send_requests (to_domain, created_at);
CREATE INDEX send_requests_sending_domain ON send_requests (sending_domain, created_at);
CREATE INDEX send_requests_status ON send_requests (status, created_at);

-- People (or whole domains, stored as '@example.com') we never email again.
CREATE TABLE suppression (
    entry       TEXT PRIMARY KEY,
    reason      TEXT NOT NULL,      -- unsubscribe | complaint | hard_bounce | opt_out_reply | manual
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- What happened after sending: replies, bounces, complaints, unsubscribes.
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- reply | auto_reply | bounce | complaint | unsubscribe
    address     TEXT NOT NULL DEFAULT '',
    send_id     TEXT,
    detail      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX events_address ON events (address, kind, created_at);

-- gate selftest runs. Live sending needs a passing run for the current provider.
CREATE TABLE selftest_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    descriptor  TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    report      TEXT NOT NULL
);
CREATE INDEX selftest_results_fingerprint ON selftest_results (fingerprint, id);
