-- The agent's database: who we might write to, and where each one came from.
-- It holds no sends and no suppression list: those live in the gate's database.

CREATE TABLE companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    domain      TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL,          -- own_data | apollo | manual | research
    source_url  TEXT NOT NULL DEFAULT '',
    source_ref  TEXT NOT NULL DEFAULT '',   -- the provider's id, if any
    found_at    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Facts we measured about a company: the reason it is worth writing to.
CREATE TABLE signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  INTEGER NOT NULL REFERENCES companies (id),
    kind        TEXT NOT NULL,          -- hiring | funding | expansion | ...
    value       TEXT NOT NULL,
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    source_url  TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);
CREATE INDEX signals_company ON signals (company_id, kind, checked_at);

-- People. Every row can answer "where did you get my email address?".
CREATE TABLE contacts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id            INTEGER NOT NULL REFERENCES companies (id),
    email                 TEXT NOT NULL UNIQUE,
    first_name            TEXT NOT NULL DEFAULT '',
    last_name             TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL DEFAULT '',
    source_type           TEXT NOT NULL,      -- own_data | apollo | manual
    source_url            TEXT NOT NULL,      -- where the address was found
    source_ref            TEXT NOT NULL DEFAULT '',
    source_note           TEXT NOT NULL DEFAULT '',
    found_at              TEXT NOT NULL,      -- date the address was found
    lawful_basis          TEXT NOT NULL,      -- legitimate_interest | consent | existing_customer
    lawful_basis_note     TEXT NOT NULL DEFAULT '',
    verification_status   TEXT NOT NULL DEFAULT 'unverified',
                                              -- unverified | valid | invalid | catch_all | risky | unknown
    verification_provider TEXT NOT NULL DEFAULT '',
    verified_at           TEXT,
    status                TEXT NOT NULL DEFAULT 'new',  -- new | contacted | replied | do_not_contact
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX contacts_company ON contacts (company_id);
CREATE INDEX contacts_ready ON contacts (verification_status, status);
