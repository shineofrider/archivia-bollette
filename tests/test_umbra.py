from email.message import EmailMessage

import pytest

from app.umbra import (
    UmbraAcqueError,
    UMBRA_TUTORIAL_CLOSE_LABEL,
    _dismiss_umbra_tutorial,
    _cookie_preferences_scope,
    _safe_umbra_page_location,
    _collect_umbra_consent_state,
    _is_optional_cookie_label,
    COOKIEBOT_DIALOG_SELECTOR,
    COOKIEBOT_REJECT_SELECTORS,
    _contains_umbra_cookie_banner_text,
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



class FakeUmbraLocator:
    def __init__(self, page):
        self.page = page
        self.clicked = False

    @property
    def last(self):
        return self

    def count(self):
        return 1 if UMBRA_TUTORIAL_CLOSE_LABEL in self.page.body_text else 0

    def is_visible(self):
        return self.count() > 0

    def click(self, timeout=None):
        self.clicked = True
        self.page.body_text = "Ciao. Fattura n. 9026011001234291. Scarica la tua bolletta in pdf."

    def wait_for(self, state=None, timeout=None):
        if state == "hidden" and self.count() > 0:
            raise AssertionError("Tutorial should have been dismissed by click")


class FakeUmbraBody:
    def __init__(self, page):
        self.page = page

    def inner_text(self, timeout=None):
        return self.page.body_text


class FakeUmbraPage:
    def __init__(self, body_text):
        self.body_text = body_text
        self.locator_instance = FakeUmbraLocator(self)
        self.waits = []

    def locator(self, selector):
        assert selector == "body"
        return FakeUmbraBody(self)

    def get_by_text(self, pattern):
        return self.locator_instance

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_umbra_tutorial_is_closed_before_searching_for_pdf_button():
    page = FakeUmbraPage(
        "Tutorial. " + UMBRA_TUTORIAL_CLOSE_LABEL + ". Non mostrare più."
    )

    assert _dismiss_umbra_tutorial(page) is True
    assert page.locator_instance.clicked is True
    assert UMBRA_TUTORIAL_CLOSE_LABEL not in page.body_text
    assert 700 in page.waits


def test_umbra_tutorial_dismiss_is_noop_when_tutorial_is_absent():
    page = FakeUmbraPage(
        "Ciao Gianluca. Fattura n. 9026011001234291. "
        "Scarica la tua bolletta in pdf."
    )

    assert _dismiss_umbra_tutorial(page) is False
    assert page.locator_instance.clicked is False



def test_cookie_preference_label_classifies_only_optional_tracking_categories():
    assert _is_optional_cookie_label("Cookie analitici")
    assert _is_optional_cookie_label("Statistiche")
    assert _is_optional_cookie_label("Marketing e pubblicità")
    assert _is_optional_cookie_label("Partner social media")
    assert not _is_optional_cookie_label("Cookie tecnici necessari")
    assert not _is_optional_cookie_label("Gestione sessione")



def test_cookie_reject_labels_do_not_treat_close_or_accept_as_rejection():
    from app.umbra import UMBRA_COOKIE_REJECT_LABELS

    assert any(pattern.fullmatch("Rifiuta tutti") for pattern in UMBRA_COOKIE_REJECT_LABELS)
    assert any(pattern.fullmatch("Solo cookie necessari") for pattern in UMBRA_COOKIE_REJECT_LABELS)
    assert not any(pattern.fullmatch("Chiudi") for pattern in UMBRA_COOKIE_REJECT_LABELS)
    assert not any(pattern.fullmatch("Accetta") for pattern in UMBRA_COOKIE_REJECT_LABELS)


def test_cookie_optional_label_classifier_ignores_essential_technical_cookie():
    assert not _is_optional_cookie_label("Cookie tecnici")
    assert not _is_optional_cookie_label("Gestione sessione necessaria")
    assert _is_optional_cookie_label("Cookie marketing e profilazione")



class FakeScopeLocator:
    def __init__(self, count=0, visible=False):
        self._count = count
        self._visible = visible

    def count(self):
        return self._count

    def nth(self, index):
        return self

    def is_visible(self):
        return self._visible


class FakeCookieScopePage:
    def __init__(self, body_text, url="https://bollettainterattiva.umbraacque.com/mi/test#/", checkbox_count=0):
        self.body_text = body_text
        self.url = url
        self.checkbox_count = checkbox_count

    def locator(self, selector):
        if selector == "body":
            class Body:
                def inner_text(inner_self, timeout=None):
                    return self.body_text
            return Body()
        if selector == 'input[type="checkbox"]':
            return FakeScopeLocator(count=self.checkbox_count)
        return FakeScopeLocator(count=0)


def test_cookie_preferences_scope_does_not_scan_invoice_page_globally():
    page = FakeCookieScopePage(
        "La bolletta è disponibile. Questo sito usa cookie tecnici. "
        "Negare il consenso clicca qui. Chiudi."
    )

    assert _cookie_preferences_scope(
        page,
        origin_url=page.url,
        is_popup=False,
    ) is None


def test_cookie_preferences_scope_accepts_a_real_preferences_panel_with_checkboxes():
    page = FakeCookieScopePage(
        "Preferenze cookie: scegli le categorie",
        checkbox_count=1,
    )

    assert _cookie_preferences_scope(
        page,
        origin_url=page.url,
        is_popup=False,
    ) is page


def test_cookie_preferences_scope_accepts_a_separate_preferences_popup():
    page = FakeCookieScopePage("Rifiuta tutti")

    assert _cookie_preferences_scope(
        page,
        origin_url="https://bollettainterattiva.umbraacque.com/mi/test#/",
        is_popup=True,
    ) is page



def test_cookiebot_reject_selector_targets_explicit_opt_out_not_acceptance():
    assert COOKIEBOT_DIALOG_SELECTOR == "#CybotCookiebotDialog"
    assert "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll" in COOKIEBOT_REJECT_SELECTORS
    assert "#CybotCookiebotDialogBodyButtonDecline" in COOKIEBOT_REJECT_SELECTORS
    assert not any("AllowAll" in selector or "Accept" in selector for selector in COOKIEBOT_REJECT_SELECTORS)


def test_umbra_cookie_banner_detector_supports_both_seen_banner_texts():
    assert _contains_umbra_cookie_banner_text(
        "Questo sito usa cookie tecnici, anche di terze parti"
    )
    assert _contains_umbra_cookie_banner_text(
        "Questo sito web utilizza i cookie. Accetta tutti. Rifiuta."
    )
    assert not _contains_umbra_cookie_banner_text(
        "Bolletta interattiva: quanto devo pagare?"
    )



class FakeConsentDiagnosticPage:
    def __init__(self, url):
        self.url = url

    def is_closed(self):
        return False

    def evaluate(self, script):
        return {"local": ["cookieConsent", "portalState"], "session": ["visitId"]}

    def get_by_text(self, pattern):
        class HiddenLocator:
            def count(self):
                return 0
        return HiddenLocator()


class FakeConsentDiagnosticContext:
    def cookies(self):
        return [
            {
                "name": "CookieConsent",
                "domain": ".umbraacque.com",
                "path": "/",
                "value": "MUST_NOT_APPEAR_IN_DIAGNOSTICS",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax",
            }
        ]


def test_safe_invoice_location_redacts_opaque_link_token():
    page = FakeConsentDiagnosticPage(
        "https://bollettainterattiva.umbraacque.com/mi/secret-token#/"
    )

    safe = _safe_umbra_page_location(page)

    assert safe == "https://bollettainterattiva.umbraacque.com/mi/<id-redacted>"
    assert "secret-token" not in safe


def test_consent_diagnostics_include_cookie_scope_but_never_values():
    text = _collect_umbra_consent_state(
        FakeConsentDiagnosticContext(),
        [FakeConsentDiagnosticPage(
            "https://bollettainterattiva.umbraacque.com/mi/secret-token#/"
        )],
    )

    assert "CookieConsent" in text
    assert ".umbraacque.com" in text
    assert "cookieConsent" in text
    assert "portalState" in text
    assert "visitId" in text
    assert "MUST_NOT_APPEAR_IN_DIAGNOSTICS" not in text
    assert "secret-token" not in text
