from app.iren import (
    IrenInvoice,
    parse_iren_invoice_card,
    parse_iren_invoice_detail,
    sort_iren_invoices_newest_first,
)


def test_parse_iren_invoice_card_extracts_portal_id_amount_and_due_date():
    text = """Fattura n° .502614099672
Contratto N°15122474
Gianluca Caimmi
28,16 €
Scadenza 24.11.2026
ADDEBITO DIRETTO SU C/C"""

    invoice = parse_iren_invoice_card(text)

    assert invoice is not None
    assert invoice.portal_id == "502614099672"
    assert invoice.amount == "28,16 €"
    assert invoice.due_date == "2026-11-24"


def test_sort_iren_invoices_by_newest_due_date_and_keep_portal_order_as_fallback():
    invoices = [
        IrenInvoice("older", due_date="2026-09-23"),
        IrenInvoice("newest", due_date="2026-11-24"),
        IrenInvoice("middle", due_date="2026-10-23"),
    ]

    assert [x.portal_id for x in sort_iren_invoices_newest_first(invoices)] == [
        "newest", "middle", "older"
    ]


def test_parse_iren_detail_extracts_formal_invoice_number_issue_date_and_amount():
    invoice = IrenInvoice(
        portal_id="502614099672",
        amount="28,16 €",
        due_date="2026-11-24",
    )
    text = """28,16 €
Emessa il 09.11.2026
Numero fattura 38502614099672
Voci di spesa
Da pagare 28,16 €"""

    updated = parse_iren_invoice_detail(text, invoice)

    assert updated.invoice_number == "38502614099672"
    assert updated.issue_date == "2026-11-09"
    assert updated.amount == "28,16 €"
