from app.iren import (
    IrenInvoice,
    IrenPortalError,
    _dismiss_cookie_notice,
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


class FakeLocator:
    def __init__(self, visible=True):
        self.visible = visible
        self.clicked = False

    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    def count(self):
        return 1 if self.visible else 0

    def is_visible(self):
        return self.visible

    def click(self, timeout=None):
        self.clicked = True
        self.visible = False


class FakeBody:
    def __init__(self, text):
        self.text = text

    def inner_text(self, timeout=None):
        return self.text


class FakeFrame:
    def __init__(self, body_text, reject_visible=True):
        self.body_text = body_text
        self.reject = FakeLocator(reject_visible)

    def locator(self, selector):
        return FakeBody(self.body_text)

    def get_by_role(self, role, name=None):
        return self.reject if role == "button" else FakeLocator(False)

    def get_by_text(self, pattern):
        return self.reject


class FakePage:
    def __init__(self, frames):
        self.frames = frames
        self.waits = []

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_cookie_notice_is_rejected_when_visible():
    frame = FakeFrame(
        "Informativa. Noi e terze parti selezionate utilizziamo cookie o tecnologie simili. "
        "Rifiuta Accetta",
        reject_visible=True,
    )
    page = FakePage([frame])

    assert _dismiss_cookie_notice(page) is True
    assert frame.reject.clicked is True
    assert 900 in page.waits


def test_cookie_notice_without_reject_control_fails_closed():
    frame = FakeFrame(
        "Informativa. Noi e terze parti selezionate utilizziamo cookie o tecnologie simili. "
        "Rifiuta Accetta",
        reject_visible=False,
    )
    page = FakePage([frame])

    try:
        _dismiss_cookie_notice(page)
    except IrenPortalError as exc:
        assert "comando Rifiuta" in str(exc)
    else:
        raise AssertionError("Un banner cookie non gestito deve bloccare il flusso")


def test_no_cookie_notice_does_not_block_portal():
    page = FakePage([FakeFrame("Bolletta e contratto", reject_visible=False)])

    assert _dismiss_cookie_notice(page) is False
