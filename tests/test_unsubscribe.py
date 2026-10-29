from fastapi.testclient import TestClient

from db import utcnow
from gate.api import create_public_app
from gate.checks import Draft
from gate.headers import make_token
from gate.sending import submit


def setup(settings, conn):
    sent = submit(conn, settings, Draft("lee@acme-corp.com", "Lee", "Hello", "Hi Lee."), now=utcnow())
    token = make_token(settings.unsubscribe_secret, sent["id"])
    return TestClient(create_public_app(settings)), token


def suppressed(conn):
    return [r["entry"] for r in conn.execute("SELECT entry FROM suppression")]


def test_one_click_post(settings, conn):
    client, token = setup(settings, conn)
    r = client.post(f"/u/{token}", content="List-Unsubscribe=One-Click",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert suppressed(conn) == ["lee@acme-corp.com"]
    event = conn.execute("SELECT kind, detail FROM events").fetchone()
    assert event["kind"] == "unsubscribe"
    assert '"one_click": true' in event["detail"]


def test_post_is_idempotent(settings, conn):
    client, token = setup(settings, conn)
    for _ in range(3):
        assert client.post(f"/u/{token}", content="List-Unsubscribe=One-Click").status_code == 200
    assert suppressed(conn) == ["lee@acme-corp.com"]
    assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_get_shows_a_button_and_changes_nothing(settings, conn):
    client, token = setup(settings, conn)
    r = client.get(f"/u/{token}")
    assert r.status_code == 200
    assert f'action="/u/{token}"' in r.text
    assert "set-cookie" not in r.headers
    assert suppressed(conn) == []


def test_bad_tokens_get_404_and_change_nothing(settings, conn):
    client, token = setup(settings, conn)
    forged = make_token("someone-elses-secret-" + "x" * 20, token.split(".")[0])
    for bad in ("nope", token[:-3] + "AAA", forged, make_token(settings.unsubscribe_secret, "no-such-send")):
        assert client.post(f"/u/{bad}", content="List-Unsubscribe=One-Click").status_code == 404
        assert client.get(f"/u/{bad}").status_code == 404
    assert suppressed(conn) == []


def test_unsubscribed_person_is_refused_next_time(settings, conn):
    client, token = setup(settings, conn)
    client.post(f"/u/{token}", content="List-Unsubscribe=One-Click")
    again = submit(conn, settings, Draft("lee@acme-corp.com", "Lee", "Hi", "Again."), now=utcnow())
    assert again["status"] == "refused"
    assert "suppressed" in [r["code"] for r in again["refusals"]]
