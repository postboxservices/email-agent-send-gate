-- A reply only counts as a reply (and lets the conversation continue) when it answers one
-- of our sends, comes from the address we wrote to, and passed the receiver's checks.
ALTER TABLE replies ADD COLUMN verified INTEGER NOT NULL DEFAULT 0;

-- The company an address belongs to (acme.com for uk.acme.com), for the per-company cap.
ALTER TABLE send_requests ADD COLUMN to_company TEXT NOT NULL DEFAULT '';
UPDATE send_requests SET to_company = to_domain;
CREATE INDEX send_requests_to_company ON send_requests (to_company, created_at);
