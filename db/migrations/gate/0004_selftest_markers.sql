-- Markers of the selftest messages the gate itself sent. inbound/ keeps a raw selftest
-- message only for one of these, so nobody can plant a message for a selftest to read.
CREATE TABLE selftest_markers (
    marker      TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);
