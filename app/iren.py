from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)

IREN_HOME_URL = "https://clienti.irenyou.gruppoiren.it/area-riservata/home"
IREN_BILLS_URL = "https://clienti.irenyou.gruppoiren.it/area-riservata/spese"
INFO_BILL_PATH = "/area-riservata/info-bolletta"


class IrenPortalError(RuntimeError):
    pass


@dataclass(frozen=True)
class IrenInvoice:
    # portal_id is the stable identifier shown in the bill-list card.
    # invoice_number is the formal number shown on the detail page, when available.
    portal_id: str
    invoice_number: str = ""
    amount: str = ""
    issue_date: str = ""
    due_date: str = ""
    card_text: str = ""

    def as_db_dict(self) -> dict[str, str | None]:
        return {
            "portal_id": self.portal_id,
            "invoice_number": self.invoice_number or None,
            "amount": self.amount or None,
            "issue_date": self.issue_date or None,
            "due_date": self.due_date or None,
        }


@dataclass(frozen=True)
class IrenDownloadedInvoice:
    invoice: IrenInvoice
    content: bytes
    filename: str


@dataclass
class IrenSyncResult:
    invoices: list[IrenInvoice]
    selected_portal_ids: list[str]
    downloads: list[IrenDownloadedInvoice]
    failures: list[tuple[str, str]]
    first_sync: bool


def _iso_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _extract_iren_portal_id(text: str) -> str:
    """Extract the digits immediately following a 'Fattura n' heading.

    IrenYou renders punctuation and spacing around the identifier inconsistently
    (for example 'Fattura n° .5026…'); normalize by collecting digits from the
    heading line instead of relying on one exact punctuation pattern.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        heading = re.search(r"fattura\s*n", line, flags=re.IGNORECASE)
        if not heading:
            continue

        candidates = [line[heading.end():]]
        candidates.extend(lines[index + 1:index + 3])
        for candidate in candidates:
            digits = "".join(character for character in candidate if character.isdigit())
            if 6 <= len(digits) <= 22:
                return digits
            # If the heading line contains a smaller numeric fragment, prefer the
            # first contiguous long identifier on the following line.
            long_match = re.search(r"\d{6,22}", candidate)
            if long_match:
                return long_match.group(0)
    return ""


def parse_iren_invoice_card(text: str) -> IrenInvoice | None:
    """Extract the stable card id, amount and due date from one bill-list card."""
    portal_id = _extract_iren_portal_id(text)
    if not portal_id:
        return None

    amount_match = re.search(
        r"([0-9]{1,3}(?:[. \u00a0\u202f][0-9]{3})*(?:,[0-9]{2}|[.][0-9]{2})|[0-9]+,[0-9]{2})\s*€",
        text,
    )
    amount = f"{amount_match.group(1).strip()} €" if amount_match else ""

    due_match = re.search(
        r"Scadenza\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    due_date = _iso_date(due_match.group(1)) if due_match else ""

    return IrenInvoice(
        portal_id=portal_id,
        amount=amount,
        due_date=due_date,
        card_text=text[:1000],
    )

def sort_iren_invoices_newest_first(invoices: list[IrenInvoice]) -> list[IrenInvoice]:
    """Use the due date shown in the list; preserve portal order where dates are absent/equal."""
    indexed = list(enumerate(invoices))
    indexed.sort(key=lambda pair: (pair[1].due_date or "", -pair[0]), reverse=True)
    return [invoice for _, invoice in indexed]


def parse_iren_invoice_detail(text: str, invoice: IrenInvoice) -> IrenInvoice:
    invoice_number_match = re.search(
        r"Numero\s+fattura\s*:?\s*([0-9][0-9 ./-]{4,})",
        text,
        flags=re.IGNORECASE,
    )
    invoice_number = ""
    if invoice_number_match:
        invoice_number = re.sub(r"\D", "", invoice_number_match.group(1))

    issue_match = re.search(
        r"Emessa\s+il\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
        text,
        flags=re.IGNORECASE,
    )
    issue_date = _iso_date(issue_match.group(1)) if issue_match else invoice.issue_date

    amount_match = re.search(
        r"([0-9]{1,3}(?:[. ][0-9]{3})*(?:,[0-9]{2}|[.][0-9]{2})|[0-9]+,[0-9]{2})\s*€",
        text,
    )
    amount = f"{amount_match.group(1).strip()} €" if amount_match else invoice.amount

    return replace(
        invoice,
        invoice_number=invoice_number or invoice.invoice_number,
        amount=amount,
        issue_date=issue_date,
    )


def parse_iren_invoice_list_text(text: str) -> list[IrenInvoice]:
    """Split page text at each invoice heading and parse the visible bill cards."""
    lines = text.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if re.search(r"Fattura\s*n", line, flags=re.IGNORECASE)
    ]

    invoices: list[IrenInvoice] = []
    seen_ids: set[str] = set()
    for position, start_line in enumerate(starts):
        end_line = starts[position + 1] if position + 1 < len(starts) else len(lines)
        card_text = "\n".join(lines[start_line:end_line]).strip()
        invoice = parse_iren_invoice_card(card_text)
        if invoice and invoice.portal_id not in seen_ids:
            seen_ids.add(invoice.portal_id)
            invoices.append(invoice)

    return sort_iren_invoices_newest_first(invoices)

def extract_iren_invoice_cards(page) -> list[IrenInvoice]:
    """Read invoice cards from visible page text, independently of generated CSS classes."""
    page_text = page.locator("body").inner_text(timeout=10000)
    invoices = parse_iren_invoice_list_text(page_text)

    if not invoices:
        # Keep the diagnostic useful without logging the customer's name/address or full card contents.
        heading_count = len(re.findall(r"Fattura\s*n", page_text, flags=re.IGNORECASE))
        log.error(
            "Pagina Spese caricata ma parser fatture senza risultati: titoli_fattura=%d, testo_pagina=%d caratteri",
            heading_count, len(page_text),
        )
        raise IrenPortalError(
            "La pagina Spese è visibile, ma non riesco a interpretare le schede delle fatture. "
            "Screenshot salvato se IREN_DEBUG_SCREENSHOT=true."
        )

    log.info("Riconosciute %d fatture nella pagina Spese: %s",
             len(invoices), [invoice.portal_id for invoice in invoices])
    return invoices

def _dismiss_cookie_notice(page) -> bool:
    """Reject the IrenYou cookie notice so it cannot cover the bills list.

    The consent manager may be rendered in the main document or an iframe.
    We explicitly choose the non-essential-cookie rejection action.
    """
    reject_pattern = re.compile(r"^\s*rifiuta(?:\s+tutti)?\s*$", re.IGNORECASE)
    cookie_notice_found = False

    for frame in list(page.frames):
        try:
            body_text = frame.locator("body").inner_text(timeout=1500).lower()
        except Exception:  # noqa: BLE001
            body_text = ""

        has_notice = (
            "cookie o tecnologie simili" in body_text
            or ("cookie" in body_text and "rifiuta" in body_text and "accetta" in body_text)
        )
        cookie_notice_found = cookie_notice_found or has_notice

        candidates = []
        try:
            candidates.append(frame.get_by_role("button", name=reject_pattern).first)
        except Exception:  # noqa: BLE001
            pass
        try:
            candidates.append(frame.get_by_role("link", name=reject_pattern).first)
        except Exception:  # noqa: BLE001
            pass
        try:
            candidates.append(frame.get_by_text(reject_pattern).last)
        except Exception:  # noqa: BLE001
            pass

        for candidate in candidates:
            try:
                if candidate.count() and candidate.is_visible():
                    candidate.click(timeout=5000)
                    page.wait_for_timeout(900)
                    log.info("Informativa cookie IrenYou: selezionato Rifiuta")
                    return True
            except Exception:  # noqa: BLE001
                continue

    if cookie_notice_found:
        raise IrenPortalError(
            "Informativa cookie IrenYou visibile, ma il comando Rifiuta non è stato individuato. "
            "Screenshot salvato se IREN_DEBUG_SCREENSHOT=true."
        )

    log.info("Informativa cookie IrenYou non presente")
    return False


def _login_and_open_bills(page, runtime: dict, timeout: int) -> str:
    login_url = runtime.get("iren_login_url") or "https://clienti.irenyou.gruppoiren.it/login"
    bills_url = runtime.get("iren_bills_url") or IREN_BILLS_URL
    home_url = runtime.get("iren_home_url") or IREN_HOME_URL

    page.goto(login_url, wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(1500)

    password_selector = runtime.get("iren_password_selector") or 'input[type="password"]'
    username_selector = runtime.get("iren_username_selector") or (
        'input[type="email"], input[name*="user" i], input[name*="email" i]'
    )
    password_box = page.locator(password_selector).first

    if password_box.count() and password_box.is_visible():
        user_box = page.locator(username_selector).first
        if not user_box.count() or not user_box.is_visible():
            user_box = page.locator('input[type="text"]').first
        if not user_box.count() or not user_box.is_visible():
            raise IrenPortalError("Campo email/username non individuato nella pagina di accesso")

        user_box.fill(runtime["iren_username"])
        password_box.fill(runtime["iren_password"])

        submit_selector = runtime.get("iren_submit_selector", "").strip()
        if submit_selector:
            submit = page.locator(submit_selector).first
        else:
            submit = page.get_by_role(
                "button", name=re.compile(r"^\s*ACCEDI\s*$", re.IGNORECASE)
            ).first
            if not submit.count():
                submit = page.get_by_text(re.compile(r"^\s*ACCEDI\s*$", re.IGNORECASE)).last

        if not submit.count() or not submit.is_visible():
            raise IrenPortalError(
                "Campi login compilati, ma il pulsante ACCEDI non è stato individuato"
            )
        submit.click()
        page.wait_for_timeout(3500)

    body_text = page.locator("body").inner_text(timeout=10000).lower()
    if any(token in body_text for token in (
        "codice otp", "codice di verifica", "autenticazione a due", "captcha", "recaptcha"
    )):
        raise IrenPortalError(
            "IrenYou richiede un controllo aggiuntivo (OTP/CAPTCHA): serve un passaggio manuale"
        )
    if page.locator(password_selector).count() and page.locator(password_selector).first.is_visible():
        raise IrenPortalError("Accesso IrenYou non completato; verificare credenziali o selettori")

    # These are the routes confirmed by the account owner; no menu scraping is needed.
    page.goto(home_url, wait_until="domcontentloaded", timeout=timeout)
    page.goto(bills_url, wait_until="domcontentloaded", timeout=timeout)
    page.wait_for_timeout(1800)
    _dismiss_cookie_notice(page)
    page.wait_for_timeout(500)

    if "/area-riservata/spese" not in page.url:
        raise IrenPortalError(
            "IrenYou non ha aperto la sezione Spese; verificare sessione e IREN_BILLS_URL"
        )
    return bills_url


def _open_bill_detail(page, invoice: IrenInvoice, timeout: int) -> str:
    identifier = page.get_by_text(re.compile(re.escape(invoice.portal_id)))
    if not identifier.count():
        raise IrenPortalError(
            f"Fattura {invoice.portal_id}: elemento non più presente nell'elenco"
        )

    identifier.first.click()
    try:
        page.wait_for_url(
            re.compile(r".*/area-riservata/info-bolletta(?:[/?#].*)?$"),
            timeout=timeout,
        )
    except PlaywrightTimeoutError:
        # Some portal releases update content through SPA navigation only.
        page.get_by_text(re.compile(r"Voci di spesa", re.IGNORECASE)).wait_for(timeout=timeout)
        if INFO_BILL_PATH not in page.url:
            raise IrenPortalError(
                f"Fattura {invoice.portal_id}: aperto un contenuto inatteso ({page.url})"
            )

    return page.locator("body").inner_text(timeout=10000)


def _download_from_detail(
    page, invoice: IrenInvoice, output_dir: Path, download_timeout: int
) -> tuple[bytes, str]:
    actions = page.get_by_text(re.compile(r"^\s*Azioni\s*$", re.IGNORECASE)).first
    if not actions.count():
        actions = page.get_by_role("button", name=re.compile(r"^\s*Azioni\s*$", re.IGNORECASE)).first
    if not actions.count():
        raise IrenPortalError(
            f"Fattura {invoice.portal_id}: menu Azioni non individuato nel dettaglio"
        )
    actions.click()

    download_action = page.get_by_text(
        re.compile(r"^\s*Scarica\s+Bolletta\s*$", re.IGNORECASE)
    ).last
    download_action.wait_for(state="visible", timeout=10000)

    with page.expect_download(timeout=download_timeout) as download_info:
        download_action.click()
    download = download_info.value

    safe_suggested = Path(download.suggested_filename or "iren-bolletta.pdf").name
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_dir / f"download-{invoice.portal_id}-{safe_suggested}"
    download.save_as(str(temporary))
    try:
        content = temporary.read_bytes()
    finally:
        temporary.unlink(missing_ok=True)

    if not content.startswith(b"%PDF-"):
        raise IrenPortalError(
            f"Fattura {invoice.portal_id}: il portale non ha restituito un PDF valido"
        )
    return content, f"Iren_{invoice.portal_id}_{safe_suggested}"


def sync_iren_invoices(
    runtime: dict,
    config: dict,
    output_dir: Path,
    known_statuses: dict[str, str],
    baseline_initialized: bool,
) -> IrenSyncResult:
    """Inspect IrenYou and download only new/pending bills, establishing a first-run baseline."""
    if not runtime.get("iren_username") or not runtime.get("iren_password"):
        raise IrenPortalError("Credenziali IrenYou non configurate nel campo Environment")

    portal_cfg = config.get("iren", {})
    timeout = int(portal_cfg.get("navigation_timeout_ms", 45000))
    download_timeout = int(portal_cfg.get("download_timeout_ms", 45000))
    profile_dir = output_dir / "iren-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    downloads: list[IrenDownloadedInvoice] = []
    failures: list[tuple[str, str]] = []
    page = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=bool(portal_cfg.get("headless", True)),
            accept_downloads=True,
            args=["--disable-dev-shm-usage"],
        )
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.set_default_timeout(timeout)
            bills_url = _login_and_open_bills(page, runtime, timeout)
            invoices = extract_iren_invoice_cards(page)

            if not baseline_initialized:
                selected = invoices[:1]
                log.info(
                    "Prima sincronizzazione Iren: %d fatture visibili; scarico soltanto la più recente "
                    "e registro le altre come storico",
                    len(invoices),
                )
            else:
                selected = [
                    invoice for invoice in invoices
                    if known_statuses.get(invoice.portal_id) not in {"historical", "downloaded"}
                ]
                log.info(
                    "Sincronizzazione Iren: %d fatture visibili, %d nuove o da completare",
                    len(invoices), len(selected),
                )

            selected_ids = [invoice.portal_id for invoice in selected]
            updated_by_id = {invoice.portal_id: invoice for invoice in invoices}

            for index, invoice in enumerate(selected):
                try:
                    detail_text = _open_bill_detail(page, invoice, timeout)
                    detailed_invoice = parse_iren_invoice_detail(detail_text, invoice)
                    content, filename = _download_from_detail(
                        page, detailed_invoice, output_dir, download_timeout
                    )
                    updated_by_id[invoice.portal_id] = detailed_invoice
                    downloads.append(
                        IrenDownloadedInvoice(detailed_invoice, content, filename)
                    )
                    log.info(
                        "Iren fattura %s scaricata dal portale (importo=%s, scadenza=%s)",
                        invoice.portal_id,
                        detailed_invoice.amount or "non rilevato",
                        detailed_invoice.due_date or "non rilevata",
                    )
                except Exception as exc:  # noqa: BLE001
                    failures.append((invoice.portal_id, str(exc)))
                    log.exception("Download Iren non riuscito per fattura %s", invoice.portal_id)
                finally:
                    if index < len(selected) - 1:
                        try:
                            page.goto(bills_url, wait_until="domcontentloaded", timeout=timeout)
                            page.wait_for_timeout(1000)
                        except Exception as exc:  # noqa: BLE001
                            failures.append(
                                (invoice.portal_id, f"Impossibile tornare all'elenco delle bollette: {exc}")
                            )
                            break

            updated_invoices = [
                updated_by_id[invoice.portal_id] for invoice in invoices
            ]
            return IrenSyncResult(
                invoices=updated_invoices,
                selected_portal_ids=selected_ids,
                downloads=downloads,
                failures=failures,
                first_sync=not baseline_initialized,
            )
        except PlaywrightTimeoutError as exc:
            if runtime.get("iren_debug_screenshot") and page:
                try:
                    page.screenshot(path=str(output_dir / "iren-error.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
            raise IrenPortalError(
                "Timeout durante accesso, lettura elenco o download da IrenYou"
            ) from exc
        except Exception:
            if runtime.get("iren_debug_screenshot") and page:
                try:
                    page.screenshot(path=str(output_dir / "iren-error.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            browser.close()
