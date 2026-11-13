from pathlib import Path

from app.database import Database


def test_forced_test_reprocess_can_be_claimed_only_once(tmp_path: Path):
    db = Database(tmp_path / "data" / "bollette.sqlite3")
    assert db.claim_test_force("message-1") is True
    assert db.claim_test_force("message-1") is False
    assert db.claim_test_force("message-2") is True
