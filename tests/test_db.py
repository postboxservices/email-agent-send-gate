import threading

import db


def test_migrate_is_idempotent_and_safe_in_parallel(tmp_path):
    path = str(tmp_path / "gate.db")
    versions, errors = [], []

    def start():
        try:
            conn = db.connect(path)
            versions.append(db.migrate(conn, "gate"))
            conn.close()
        except Exception as e:  # pragma: no cover - the assertion below reports it
            errors.append(e)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(set(versions)) == 1 and versions[0] >= 1
    conn = db.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"send_requests", "suppression", "events", "selftest_results"} <= tables
