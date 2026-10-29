# Security

## Reporting a vulnerability

Please report security problems privately through GitHub: open the repository's **Security**
tab and choose **Report a vulnerability**. Do not open a public issue for them.

Include what you found, how to reproduce it, and what an attacker could do with it. We aim to
acknowledge a report within five working days and to tell you what we will do about it.

Fixes go into the latest release; there is no long-term support for older versions.

## What the design protects

| Risk | How the code handles it |
|---|---|
| The agent (or a prompt injection) sends email on its own | The agent holds no provider credentials. Only the internal `gate` service gets `env/provider.env`; tests and CI check the container split. The agent can only ask through `POST /send-requests`, and every request passes the same checks. |
| A reply tells the agent to email everyone | Replies are marked as untrusted data. `request_send` takes a contact id from the database, not an address, and the gate's caps and suppression list apply whatever the agent asks for. |
| The agent approves its own drafts | The review page needs `GATE_REVIEW_PASSWORD`, which the agent never has; the agent's API token cannot approve. Review forms carry a token derived from the password, against cross-site requests. |
| Someone unsubscribes another person | The unsubscribe URL holds only a send id signed with HMAC-SHA256 (`UNSUBSCRIBE_SECRET`); it contains no address and cannot be edited. |
| Forged bounces, complaints or replies | `/inbound/sns` verifies every SNS signature (certificates only from `sns.<region>.amazonaws.com`) and accepts only the topics you list. `/inbound/events` and `/inbound/raw` need `INBOUND_TOKEN`. All inbound endpoints are off until configured. A reply lets a conversation continue past the per-person cap only if it answers one of our sends (our random send id in `In-Reply-To`), comes from the address we wrote to and did not fail DMARC; anything else is recorded but changes no cap. |
| Live sending with a misconfigured provider | Live sending needs `DRY_RUN=false` and a passing `gate selftest` for the exact provider settings in use. With `--via inbound`, the gate keeps a selftest message only for a marker it sent itself, and the first message wins, so nobody can plant one. |
| Clickjacking the review or unsubscribe buttons | Every response carries `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` (the unsubscribe URL holds a token). |
| The optional reply classifier is manipulated | Its answer is limited to a fixed list of labels, and a label can only suppress someone or record a reply. |
| Web research turns up addresses to write to | The optional web research tools can only read. No agent tool adds a contact or an address; `record_signal` refuses any summary containing an email address. |
| A data provider sends around the gate | The Apollo adapter can call three endpoints (search and enrich) and refuses any other path. |

## Running it safely

- Expose only `gate-public` to the internet, behind an HTTPS reverse proxy, and only its `/u/`
  and `/inbound/` paths. Keep the internal `gate` service (port 8000, the agent API and the
  review page) on `127.0.0.1` and reach the review page through an SSH tunnel.
- Generate long random values for `UNSUBSCRIBE_SECRET`, `GATE_API_TOKEN`,
  `GATE_REVIEW_PASSWORD` and `INBOUND_TOKEN`, keep the `env/*.env` files out of version control
  (the `.gitignore` does this), and rotate a value if it may have leaked. Changing
  `UNSUBSCRIBE_SECRET` breaks the unsubscribe links in messages already sent, so rotate it
  only when you must.
- Give the email provider credentials only the permission to send, from your sending domain.
- Back up the `gate-data` volume. Its suppression list is what keeps people who opted out from
  being emailed again.
- Keep dependencies current and watch this repository's releases.
