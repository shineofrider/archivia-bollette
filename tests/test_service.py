from datetime import datetime, timezone
from email.message import EmailMessage

from app.mail import MailItem
from app import service
from app.service import _matching_provider


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
