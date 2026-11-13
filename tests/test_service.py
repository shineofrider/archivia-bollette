from datetime import datetime, timezone
from email.message import EmailMessage

from app.mail import MailItem
from app import service
from app.service import _iren_notification_details, _matching_provider


PROVIDERS = {
    "alperia": {
        "sender": "noreply@alperia.eu",
        "subject_prefix": "Nuova fattura disponibile",
        "archive_subdir": "Corrente",
    },
    "iren": {
        "sender": "noreply@mail.clienti.irenyou.gruppoiren.it",
        "subject_exact": "Emissione Bolletta",
        "archive_subdir": "Gas",
    },
}


def make_item(sender: str, subject: str) -> MailItem:
    return MailItem(
        uid="123",
        message_id="test-message",
        sender=sender,
        subject=subject,
        date=datetime(2026, 11, 13, tzinfo=timezone.utc),
        attachments=[],
        raw_message=EmailMessage(),
    )


def test_alperia_subject_matches_prefix_without_nr_requirement():
    item = make_item("noreply@alperia.eu", "Nuova fattura disponibile - la tua fattura è pronta")
    assert _matching_provider(item, PROVIDERS)[0] == "alperia"


def test_alperia_subject_matches_prefix_with_invoice_number():
    item = make_item("noreply@alperia.eu", "Nuova fattura disponibile Nr. 12345")
    assert _matching_provider(item, PROVIDERS)[0] == "alperia"


def test_alperia_subject_must_start_with_prefix():
    item = make_item("noreply@alperia.eu", "Avviso: Nuova fattura disponibile Nr. 12345")
    assert _matching_provider(item, PROVIDERS) is None


def test_alperia_sender_must_match_exactly():
    item = make_item("altro@example.com", "Nuova fattura disponibile Nr. 12345")
    assert _matching_provider(item, PROVIDERS) is None


def test_iren_subject_remains_exact_match():
    item = make_item("noreply@mail.clienti.irenyou.gruppoiren.it", "Emissione Bolletta")
    assert _matching_provider(item, PROVIDERS)[0] == "iren"



def test_forced_process_can_be_retried_after_container_restart(monkeypatch):
    monkeypatch.setattr(service, "_FORCED_TEST_ATTEMPTS", set())

    assert service._claim_test_force_once("iren-message") is True
    assert service._claim_test_force_once("iren-message") is False

    # A new container process starts with a fresh in-memory latch.
    monkeypatch.setattr(service, "_FORCED_TEST_ATTEMPTS", set())
    assert service._claim_test_force_once("iren-message") is True



def test_iren_notification_includes_amount_due_date_and_invoice_number():
    from types import SimpleNamespace

    invoice = SimpleNamespace(
        portal_id="502614099672",
        amount="28,16 €",
        due_date="2026-11-24",
        issue_date="2026-11-09",
        invoice_number="38502614099672",
    )

    message, extra = _iren_notification_details(invoice)

    assert message.splitlines() == [
        "Importo da pagare: 28,16 €",
        "Scadenza: 24/11/2026",
        "Data emissione: 09/11/2026",
        "Numero fattura: 38502614099672",
    ]
    assert extra == {
        "amount": "28,16 €",
        "due_date": "24/11/2026",
        "issue_date": "09/11/2026",
        "invoice_number": "38502614099672",
        "portal_invoice_id": "502614099672",
    }


def test_iren_notification_omits_unavailable_invoice_fields():
    from types import SimpleNamespace

    invoice = SimpleNamespace(
        portal_id="502614099672",
        amount="",
        due_date="",
        issue_date="",
        invoice_number="",
    )

    message, extra = _iren_notification_details(invoice)

    assert message == ""
    assert extra == {"portal_invoice_id": "502614099672"}


def test_iren_notification_preserves_unknown_date_format():
    from types import SimpleNamespace

    invoice = SimpleNamespace(
        portal_id="502614099672",
        amount="28,16 €",
        due_date="not-available",
        issue_date="",
        invoice_number="",
    )

    message, extra = _iren_notification_details(invoice)

    assert message.splitlines() == [
        "Importo da pagare: 28,16 €",
        "Scadenza: not-available",
    ]
    assert extra["due_date"] == "not-available"
