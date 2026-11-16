from email.message import EmailMessage

import pytest

from app.umbra import (
    UmbraAcqueError,
    extract_umbra_invoice_link,
    parse_umbra_invoice_text,
)


def test_extract_direct_umbra_acque_link_from_html_email():
    message = EmailMessage()
    message["From"] = "Umbra Acque <no-reply@umbraacque.com>"
    message["Subject"] = "My Umbra Acque: nuova bolletta web 123"
    message.set_content("La tua bolletta è disponibile.")
    message.add_alternative(
        '<html><body><a href="https://bollettainterattiva.umbraacque.com/mi/'
        'opaque-token#/">Apri bolletta</a></body></html>',
        subtype="html",
    )

    url = extract_umbra_invoice_link(message)

    assert url == "https://bollettainterattiva.umbraacque.com/mi/opaque-token#/"


def test_extract_umbra_link_from_plain_text_email():
    message = EmailMessage()
    message.set_content(
        "Apri la bolletta: "
        "https://bollettainterattiva.umbraacque.com/mi/opaque-token#/"
    )

    assert extract_umbra_invoice_link(message).endswith("/mi/opaque-token#/")


def test_reject_link_to_unexpected_domain():
    message = EmailMessage()
    message.set_content("https://example.com/mi/not-an-umbra-invoice")

    with pytest.raises(UmbraAcqueError, match="non è presente un link HTTPS diretto"):
        extract_umbra_invoice_link(message)


def test_reject_multiple_distinct_umbra_links():
    message = EmailMessage()
    message.set_content(
        "https://bollettainterattiva.umbraacque.com/mi/first#/ "
        "https://bollettainterattiva.umbraacque.com/mi/second#/"
    )

    with pytest.raises(UmbraAcqueError, match="2 link diversi"):
        extract_umbra_invoice_link(message)


def test_parse_umbra_bimonthly_period_and_invoice_details_from_user_example():
    text = """Fattura n. 9026011001234291
    dal 12/08/26 al 08/10/26
    Data di emissione 08/10/26
    Quanto devo pagare? 52,79 €
    Scade il 07/11/26"""

    invoice = parse_umbra_invoice_text(text)

    assert invoice.invoice_number == "9026011001234291"
    assert invoice.amount == "52,79 €"
    assert invoice.issue_date == "08/10/26"
    assert invoice.due_date == "07/11/26"
    assert invoice.filename == "26_08-09.pdf"
    assert invoice.period_key == "2026_08-09"
    assert invoice.period_description == "agosto-settembre 2026"


def test_parse_umbra_billing_period_across_year_boundary():
    invoice = parse_umbra_invoice_text(
        "Fattura n. 9026011001234291 dal 12/12/26 al 08/02/27 "
        "Quanto devo pagare? 52,79 € Scade il 07/03/27"
    )

    assert invoice.filename == "26_12-01.pdf"
    assert invoice.period_key == "2026_12-01"
    assert invoice.period_description == "dicembre 2026 - gennaio 2027"


def test_fail_when_billing_period_is_missing_or_invalid():
    with pytest.raises(UmbraAcqueError, match="Non riesco a ricavare il periodo"):
        parse_umbra_invoice_text("Fattura n. 9026011001234291 Importo 52,79 €")
