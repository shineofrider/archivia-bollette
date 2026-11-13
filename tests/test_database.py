from pathlib import Path

from app.database import Database


def test_forced_test_reprocess_can_be_claimed_only_once(tmp_path: Path):
    db = Database(tmp_path / "data" / "bollette.sqlite3")
    assert db.claim_test_force("message-1") is True
    assert db.claim_test_force("message-1") is False
    assert db.claim_test_force("message-2") is True


def test_iren_initial_baseline_marks_latest_pending_and_older_as_historical(tmp_path: Path):
    db = Database(tmp_path / "data" / "bollette.sqlite3")
    invoices = [
        {"portal_id": "newest", "invoice_number": "INV-NEW", "amount": "28,16 €", "due_date": "2026-11-24"},
        {"portal_id": "older-1", "invoice_number": "INV-OLD-1", "amount": "25,75 €", "due_date": "2026-10-23"},
        {"portal_id": "older-2", "invoice_number": "INV-OLD-2", "amount": "22,65 €", "due_date": "2026-09-23"},
    ]

    db.initialize_iren_baseline(invoices, newest_portal_id="newest")

    assert db.iren_baseline_initialized() is True
    assert db.iren_invoice_statuses() == {
        "newest": "pending",
        "older-1": "historical",
        "older-2": "historical",
    }


def test_iren_baseline_is_not_rewritten_on_later_call(tmp_path: Path):
    db = Database(tmp_path / "data" / "bollette.sqlite3")
    db.initialize_iren_baseline(
        [{"portal_id": "first", "amount": "10,00 €"}],
        newest_portal_id="first",
    )
    db.mark_iren_invoice_downloaded("first", "/archive/Gas/2026_11.pdf")
    db.initialize_iren_baseline(
        [{"portal_id": "second", "amount": "20,00 €"}],
        newest_portal_id="second",
    )

    assert db.iren_invoice_statuses() == {"first": "downloaded"}


def test_iren_new_invoice_is_pending_and_can_be_marked_downloaded(tmp_path: Path):
    db = Database(tmp_path / "data" / "bollette.sqlite3")
    invoice = {
        "portal_id": "new-bill",
        "invoice_number": "INV-123",
        "amount": "31,20 €",
        "issue_date": "2026-11-12",
        "due_date": "2026-11-30",
    }

    db.upsert_iren_invoice(invoice)
    assert db.iren_invoice_statuses()["new-bill"] == "pending"

    db.mark_iren_invoice_downloaded("new-bill", "/archive/Gas/2026_11.pdf", "INV-123")
    assert db.iren_invoice_statuses()["new-bill"] == "downloaded"
