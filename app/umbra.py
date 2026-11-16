from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from email.message import Message
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)
UMBRA_HOST = "bollettainterattiva.umbraacque.com"
MONTH_NAMES = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre",
}


class UmbraAcqueError(RuntimeError):
    pass


@dataclass(frozen=True)
class UmbraInvoice:
    invoice_number: str
    amount: str
    issue_date: str
    due_date: str
    start_date: date
    end_date: date
    filename: str
    period_key: str
    period_description: str

    def notification_details(self) -> tuple[str, dict[str, str]]:
        lines: list[str] = []
        extra: dict[str, str] = {}

        if self.amount:
            lines.append(f"Importo da pagare: {self.amount}")
            extra["amount"] = self.amount
        if self.due_date:
            lines.append(f"Scadenza: {_format_date(self.due_date)}")
            extra["due_date"] = _format_date(self.due_date)
        if self.issue_date:
            lines.append(f"Data emissione: {_format_date(self.issue_date)}")
            extra["issue_date"] = _format_date(self.issue_date)
        if self.invoice_number:
            lines.append(f"Numero fattura: {self.invoice_number}")
            extra["invoice_number"] = self.invoice_number

        extra["billing_period"] = self.period_key
        extra["billing_period_description"] = self.period_description
        return "\n".join(lines), extra


@dataclass(frozen=True)
class UmbraDownload:
    content: bytes
    suggested_filename: str
    page_text: str


def _decode_part(part: Message) -> str:
    try:
        value = part.get_content()
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            charset = part.get_content_charset() or "utf-8"
            return value.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError, AttributeError):
        pass

    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_umbra_invoice_link(message: Message) -> str:
    """Find the direct Umbria Acque interactive-invoice URL without logging its token."""
    candidates: list[str] = []
    parts = message.walk() if message.is_multipart() else [message]

    for part in parts:
        if part.get_content_type() not in {"text/plain", "text/html"}:
            continue
        body = html.unescape(_decode_part(part))
        for raw_url in re.findall(r"https?://[^\s<>\"']+", body, flags=re.IGNORECASE):
            candidate = raw_url.rstrip(".,;:!?)]}")
            try:
                parsed = urlsplit(candidate)
                if parsed.scheme.lower() != "https":
                    continue
                if (parsed.hostname or "").lower() != UMBRA_HOST:
                    continue
                candidates.append(candidate)
            except ValueError:
                continue

    unique = list(dict.fromkeys(candidates))
    if not unique:
        raise UmbraAcqueError(
            f"Nell'email non è presente un link HTTPS diretto a {UMBRA_HOST}"
        )
    if len(unique) > 1:
        raise UmbraAcqueError(
            f"Nell'email sono presenti {len(unique)} link diversi al portale Umbra Acque; "
            "non è possibile scegliere in sicurezza quello della fattura"
        )

    log.info("Link diretto al portale Umbra Acque individuato nell'email")
    return unique[0]


def _parse_date(value: str) -> date | None:
    value = value.strip()
    for fmt in (
        "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
        "%d/%m/%y", "%d-%m-%y", "%d.%m.%y",
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _format_date(value: str) -> str:
    parsed = _parse_date(value)
    return parsed.strftime("%d/%m/%Y") if parsed else value


def _month_before(value: date) -> tuple[int, int]:
    if value.month == 1:
        return value.year - 1, 12
    return value.year, value.month - 1


def _billing_months(start: date, end: date) -> list[tuple[int, int]]:
    # Umbra Acque shows the reading-period end date at the start of the following
    # month (e.g. 08/10 for the August-September bill). That partial end month is
    # a closing/reading date, not an additional month of competence.
    last_year, last_month = end.year, end.month
    if end.day <= 15 and (end.year, end.month) != (start.year, start.month):
        last_year, last_month = _month_before(end)

    if (last_year, last_month) < (start.year, start.month):
        last_year, last_month = start.year, start.month

    months: list[tuple[int, int]] = []
    year, month = start.year, start.month
    while (year, month) <= (last_year, last_month):
        months.append((year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
        if len(months) > 3:
            raise UmbraAcqueError(
                "Il periodo di fatturazione Umbra Acque supera tre mesi; "
                "verificare le date prima di creare il nome del PDF"
            )
    return months


def parse_umbra_invoice_text(text: str) -> UmbraInvoice:
    """Extract invoice metadata and the bimonthly archive name from page/PDF text."""
    normalized = re.sub(r"\s+", " ", text or "").strip()

    period_match = re.search(
        r"\bdal\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*al\s*"
        r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
        normalized,
        flags=re.IGNORECASE,
    )
    if not period_match:
        raise UmbraAcqueError(
            "Non riesco a ricavare il periodo 'dal ... al ...' dalla pagina o dal PDF Umbra Acque"
        )

    start = _parse_date(period_match.group(1))
    end = _parse_date(period_match.group(2))
    if not start or not end or end < start:
        raise UmbraAcqueError("Le date del periodo di fatturazione Umbra Acque non sono valide")

    months = _billing_months(start, end)
    month_tokens = "-".join(f"{month:02d}" for _, month in months)
    filename = f"{start.year % 100:02d}_{month_tokens}.pdf"
    period_key = f"{start.year:04d}_{month_tokens}"

    if len(months) == 1:
        period_description = f"{MONTH_NAMES[months[0][1]]} {months[0][0]}"
    else:
        first_name = MONTH_NAMES[months[0][1]]
        last_name = MONTH_NAMES[months[-1][1]]
        if months[0][0] == months[-1][0]:
            period_description = f"{first_name}-{last_name} {months[0][0]}"
        else:
            period_description = (
                f"{first_name} {months[0][0]} - {last_name} {months[-1][0]}"
            )

    invoice_match = re.search(
        r"\bFattura\s*n\.?\s*([0-9]{8,24})",
        normalized,
        flags=re.IGNORECASE,
    )
    invoice_number = invoice_match.group(1) if invoice_match else ""

    amount_match = re.search(
        r"Quanto\s+devo\s+pagare\?\s*([0-9][0-9. ]*,[0-9]{2})\s*€",
        normalized,
        flags=re.IGNORECASE,
    )
    amount = f"{amount_match.group(1).strip()} €" if amount_match else ""

    date_pattern = r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
    due_match = re.search(
        r"(?:Scade\s+il|Data\s+di\s+scadenza)\s*:?\s*" + date_pattern,
        normalized,
        flags=re.IGNORECASE,
    )
    if not due_match:
        due_match = re.search(
            date_pattern + r"\s+(?:Data\s+di\s+scadenza)",
            normalized,
            flags=re.IGNORECASE,
        )

    issue_match = re.search(
        r"(?:Data\s+di\s+emissione|Emessa\s+il)\s*:?\s*" + date_pattern,
        normalized,
        flags=re.IGNORECASE,
    )
    if not issue_match:
        issue_match = re.search(
            date_pattern + r"\s+(?:Data\s+di\s+emissione)",
            normalized,
            flags=re.IGNORECASE,
        )

    return UmbraInvoice(
        invoice_number=invoice_number,
        amount=amount,
        issue_date=issue_match.group(1) if issue_match else "",
        due_date=due_match.group(1) if due_match else "",
        start_date=start,
        end_date=end,
        filename=filename,
        period_key=period_key,
        period_description=period_description,
    )


UMBRA_TUTORIAL_CLOSE_LABEL = (
    "Clicca qui per chiudere il tutorial e visualizzare la tua bolletta"
)


def _dismiss_umbra_tutorial(page) -> bool:
    """Close the first-visit tutorial overlay before locating the PDF download control."""
    body_text = page.locator("body").inner_text(timeout=10000)
    if UMBRA_TUTORIAL_CLOSE_LABEL.casefold() not in body_text.casefold():
        log.info("Tutorial iniziale Umbra Acque non presente")
        return False

    close_instruction = page.get_by_text(
        re.compile(re.escape(UMBRA_TUTORIAL_CLOSE_LABEL), re.IGNORECASE)
    ).last
    if not close_instruction.count() or not close_instruction.is_visible():
        raise UmbraAcqueError(
            "Tutorial iniziale Umbra Acque visualizzato, ma il comando per chiuderlo "
            "non è individuabile"
        )

    close_instruction.click(timeout=10000)
    try:
        close_instruction.wait_for(state="hidden", timeout=10000)
    except PlaywrightTimeoutError as exc:
        raise UmbraAcqueError(
            "Il clic sul comando di chiusura del tutorial Umbra Acque non lo ha chiuso"
        ) from exc

    page.wait_for_timeout(700)
    remaining_text = page.locator("body").inner_text(timeout=10000)
    if UMBRA_TUTORIAL_CLOSE_LABEL.casefold() in remaining_text.casefold():
        raise UmbraAcqueError(
            "Il tutorial Umbra Acque risulta ancora visibile dopo il comando di chiusura"
        )

    log.info("Tutorial iniziale Umbra Acque chiuso; bolletta visualizzata")
    return True


UMBRA_COOKIE_BANNER_MARKERS = (
    "Questo sito usa cookie tecnici",
    "Questo sito web utilizza i cookie",
)
COOKIEBOT_DIALOG_SELECTOR = "#CybotCookiebotDialog"
COOKIEBOT_REJECT_SELECTORS = (
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinDeclineAll",
    "#CybotCookiebotDialogBodyButtonDecline",
)
UMBRA_COOKIE_SETTINGS_LINK = re.compile(r"^\s*clicca\s+qui[.!]?\s*$", re.IGNORECASE)
UMBRA_COOKIE_REJECT_LABELS = (
    re.compile(r"^\s*rifiuta\s+tutti(?:\s+i\s+cookie)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*rifiuta\s+(?:i\s+)?cookie\s+non\s+necessari\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:solo\s+)?cookie\s+necessari(?:\s+soltanto)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*continua\s+senza\s+accettare\s*$", re.IGNORECASE),
    re.compile(r"^\s*rifiuta\s*$", re.IGNORECASE),
)
UMBRA_OPTIONAL_COOKIE_LABEL = re.compile(
    r"analytics|analitic|statistic|marketing|pubblicit|profilaz|social|terze\s+parti|"
    r"misurazione|tracciamento|personalizzaz",
    re.IGNORECASE,
)
UMBRA_COOKIE_SAVE_LABELS = (
    re.compile(r"^\s*salva\s+(?:le\s+)?preferenze\s*$", re.IGNORECASE),
    re.compile(r"^\s*conferma\s+(?:le\s+)?(?:mie\s+)?scelte\s*$", re.IGNORECASE),
    re.compile(r"^\s*salva\s+le\s+mie\s+scelte\s*$", re.IGNORECASE),
)


def _is_optional_cookie_label(label: str) -> bool:
    return bool(UMBRA_OPTIONAL_COOKIE_LABEL.search(label or ""))


def _click_first_visible_named_control(page, patterns) -> bool:
    for pattern in patterns:
        for role in ("button", "link"):
            try:
                control = page.get_by_role(role, name=pattern).last
                if control.count() and control.is_visible():
                    control.click(timeout=8000)
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _disable_optional_cookie_checkboxes(page) -> tuple[int, list[str]]:
    """Uncheck only controls whose accessible labels clearly identify optional tracking."""
    checkboxes = page.locator('input[type="checkbox"]')
    try:
        count = checkboxes.count()
    except Exception:  # noqa: BLE001
        return 0, []

    changed = 0
    changed_labels: list[str] = []
    for index in range(count):
        checkbox = checkboxes.nth(index)
        try:
            details = checkbox.evaluate(
                """el => {
                    const id = el.id;
                    const labels = [];
                    if (id) {
                        document.querySelectorAll('label[for="' + CSS.escape(id) + '"]')
                            .forEach(label => labels.push(label.innerText || label.textContent || ""));
                    }
                    const wrapping = el.closest("label");
                    if (wrapping) labels.push(wrapping.innerText || wrapping.textContent || "");
                    const aria = el.getAttribute("aria-label");
                    if (aria) labels.push(aria);
                    return {
                        checked: !!el.checked,
                        disabled: !!el.disabled,
                        label: labels.join(" ").replace(/\\s+/g, " ").trim()
                    };
                }"""
            )
            label = str(details.get("label", "")).strip()
            if not label or details.get("disabled") or not details.get("checked"):
                continue
            if not _is_optional_cookie_label(label):
                continue
            checkbox.uncheck(timeout=5000)
            changed += 1
            changed_labels.append(label[:100])
        except Exception:  # noqa: BLE001
            continue
    return changed, changed_labels


def _contains_umbra_cookie_banner_text(body_text: str) -> bool:
    normalized = (body_text or "").casefold()
    return any(marker.casefold() in normalized for marker in UMBRA_COOKIE_BANNER_MARKERS)


def _visible_umbra_cookie_banner(page) -> bool:
    """Check known CMP dialogs and visible banner text, not hidden residual DOM text."""
    try:
        cookiebot = page.locator(COOKIEBOT_DIALOG_SELECTOR)
        if cookiebot.count() and cookiebot.last.is_visible():
            return True
    except Exception:  # noqa: BLE001
        pass

    try:
        for marker_text in UMBRA_COOKIE_BANNER_MARKERS:
            marker = page.get_by_text(
                re.compile(re.escape(marker_text), re.IGNORECASE)
            ).last
            if marker.count() and marker.is_visible():
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _click_cookiebot_reject(page) -> bool:
    """Use Cookiebot's documented opt-out control before any generic text fallback."""
    for selector in COOKIEBOT_REJECT_SELECTORS:
        try:
            control = page.locator(selector).last
            if control.count() and control.is_visible():
                control.click(timeout=10000)
                log.info("Umbra Acque: selezionato il comando Cookiebot di rifiuto")
                return True
        except Exception:  # noqa: BLE001
            continue

    # Some Cookiebot themes change button IDs but preserve the visible Italian label.
    try:
        control = page.get_by_role(
            "button", name=re.compile(r"^\s*Rifiuta\s*$", re.IGNORECASE)
        ).last
        if control.count() and control.is_visible():
            control.click(timeout=10000)
            log.info("Umbra Acque: selezionato il pulsante Cookiebot Rifiuta")
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _wait_for_cookiebot_to_close(page, timeout: int = 10000) -> None:
    dialog = page.locator(COOKIEBOT_DIALOG_SELECTOR)
    if dialog.count():
        dialog.last.wait_for(state="hidden", timeout=timeout)
        return

    for marker_text in UMBRA_COOKIE_BANNER_MARKERS:
        marker = page.get_by_text(re.compile(re.escape(marker_text), re.IGNORECASE)).last
        if marker.count() and marker.is_visible():
            marker.wait_for(state="hidden", timeout=timeout)
            return


def _cookie_preferences_scope(page, origin_url: str, is_popup: bool):
    """Return a safe scope for consent controls; never search the invoice page globally."""
    if is_popup or page.url != origin_url:
        return page

    for selector in (
        '[role="dialog"]',
        'dialog[open]',
        '[aria-modal="true"]',
        '[id*="cookie" i][class*="modal" i]',
        '[class*="cookie" i][class*="modal" i]',
    ):
        try:
            candidates = page.locator(selector)
            for index in range(candidates.count() - 1, -1, -1):
                candidate = candidates.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:  # noqa: BLE001
            continue

    # Some consent managers render a preferences panel without dialog semantics.
    # Only accept the whole page as a scope if it visibly presents preferences UI.
    try:
        body = page.locator("body").inner_text(timeout=3000).casefold()
        has_preferences_heading = any(
            phrase in body
            for phrase in (
                "preferenze cookie",
                "impostazioni cookie",
                "gestisci i cookie",
                "personalizza il consenso",
            )
        )
        has_checkbox = page.locator('input[type="checkbox"]').count() > 0
        if has_preferences_heading and has_checkbox:
            return page
    except Exception:  # noqa: BLE001
        pass
    return None


def _return_to_umbra_invoice_after_cookie_choice(page, origin_url: str) -> None:
    """Return to the original invoice link after saving choices on a same-tab settings route."""
    try:
        if page.url != origin_url:
            page.goto(origin_url, wait_until="domcontentloaded", timeout=30000)
        else:
            page.reload(wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(800)
        _dismiss_umbra_tutorial(page)
    except Exception as exc:  # noqa: BLE001
        raise UmbraAcqueError(
            f"Preferenze cookie salvate, ma impossibile tornare alla pagina fattura ({type(exc).__name__})"
        ) from exc


def _safe_umbra_page_location(page) -> str:
    """Return origin/path only, redacting opaque invoice identifiers and fragments."""
    try:
        parsed = urlsplit(page.url)
        path = parsed.path
        if path.startswith("/mi/"):
            path = "/mi/<id-redacted>"
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:  # noqa: BLE001
        return "<url-unavailable>"


def _collect_umbra_consent_state(context, pages: list) -> str:
    """Collect cookie names/domains and storage key names only; never read values."""
    lines: list[str] = []
    try:
        cookies = context.cookies()
    except Exception as exc:  # noqa: BLE001
        cookies = []
        lines.append(f"cookies_error={type(exc).__name__}")

    lines.append("COOKIES (names/domains/flags only; values deliberately omitted)")
    for cookie in sorted(cookies, key=lambda item: (item.get("domain", ""), item.get("name", ""))):
        lines.append(
            "cookie="
            + repr({
                "name": cookie.get("name", ""),
                "domain": cookie.get("domain", ""),
                "path": cookie.get("path", ""),
                "secure": cookie.get("secure", False),
                "httpOnly": cookie.get("httpOnly", False),
                "sameSite": cookie.get("sameSite", ""),
            })
        )

    for index, page in enumerate(pages):
        if page is None:
            continue
        try:
            if page.is_closed():
                continue
        except Exception:  # noqa: BLE001
            continue

        lines.append(f"PAGE_{index}_LOCATION={_safe_umbra_page_location(page)}")
        try:
            storage = page.evaluate(
                """() => ({
                    local: Object.keys(window.localStorage || {}),
                    session: Object.keys(window.sessionStorage || {})
                })"""
            )
            lines.append(f"PAGE_{index}_LOCAL_STORAGE_KEYS={storage.get('local', [])!r}")
            lines.append(f"PAGE_{index}_SESSION_STORAGE_KEYS={storage.get('session', [])!r}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"PAGE_{index}_STORAGE_KEYS_ERROR={type(exc).__name__}")

        try:
            lines.append(
                f"PAGE_{index}_CUSTOM_BANNER_VISIBLE="
                f"{_visible_umbra_cookie_banner(page)}"
            )
        except Exception:  # noqa: BLE001
            pass

    return "\n".join(lines) + "\n"


def _save_umbra_consent_state(context, pages: list, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "umbra-consent-state.txt"
    path.write_text(
        _collect_umbra_consent_state(context, pages),
        encoding="utf-8",
    )
    log.warning(
        "Diagnostica consenso Umbra Acque salvata in %s "
        "(nomi/domìni cookie e chiavi storage; valori esclusi)",
        path,
    )
    return path


def _manage_umbra_cookie_consent(page, output_dir: Path) -> None:
    """Use Cookiebot's explicit Rifiuta action; never accept all or close the banner."""
    body_text = page.locator("body").inner_text(timeout=10000)
    if not _contains_umbra_cookie_banner_text(body_text):
        log.info("Banner cookie Umbra Acque non presente")
        return

    settings_link = page.get_by_role("link", name=UMBRA_COOKIE_SETTINGS_LINK).last
    if not settings_link.count() or not settings_link.is_visible():
        _save_umbra_diagnostics(page, output_dir, prefix="umbra-cookie-preferences")
        raise UmbraAcqueError(
            "Banner cookie Umbra Acque presente, ma il link 'clicca qui' non è cliccabile. "
            "Nessun consenso è stato prestato."
        )

    try:
        link_info = settings_link.evaluate(
            """el => ({
                target: el.getAttribute("target") || "",
                href: el.getAttribute("href") || ""
            })"""
        )
    except Exception:  # noqa: BLE001
        link_info = {"target": "", "href": ""}

    # URLs can contain an opaque invoice/session token: log only the hostname.
    safe_target = ""
    try:
        parsed_target = urlsplit(str(link_info.get("href", "")))
        safe_target = f"{parsed_target.scheme}://{parsed_target.netloc}" if parsed_target.netloc else ""
    except ValueError:
        pass

    origin_url = page.url
    opened_pages = []
    consent_context = page.context
    page.context.on("page", lambda opened: opened_pages.append(opened))
    log.info(
        "Umbra Acque: apro il collegamento alle preferenze cookie (target=%s, destinazione=%s)",
        link_info.get("target") or "stessa scheda",
        safe_target or "gestita dal sito",
    )
    settings_link.click(timeout=10000)
    page.wait_for_timeout(1200)

    preferences_page = opened_pages[-1] if opened_pages else page
    is_popup = preferences_page is not page
    if is_popup:
        try:
            preferences_page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            pass

    _save_umbra_diagnostics(
        preferences_page, output_dir, prefix="umbra-cookie-preferences"
    )

    # The supplied screenshot shows Cookiebot's three-button banner:
    # "Accetta tutti", "Accetta selezionati", and the explicit "Rifiuta" action.
    # Cookiebot documents the following IDs for the opt-out action.
    if _click_cookiebot_reject(preferences_page):
        try:
            _wait_for_cookiebot_to_close(preferences_page, timeout=10000)
        except PlaywrightTimeoutError as exc:
            _save_umbra_diagnostics(
                preferences_page, output_dir, prefix="umbra-cookie-after-reject"
            )
            raise UmbraAcqueError(
                "Cookiebot ha ricevuto il comando Rifiuta, ma il banner non si è chiuso"
            ) from exc

        if is_popup:
            try:
                preferences_page.close()
            except Exception:  # noqa: BLE001
                pass

        # The "clicca qui" link can have navigated the current tab to the Privacy page.
        # Always return to the original one-time invoice URL after persisting the choice.
        if is_popup or page.url != origin_url:
            _return_to_umbra_invoice_after_cookie_choice(page, origin_url)
        else:
            page.wait_for_timeout(700)

        _save_umbra_consent_state(
            consent_context,
            [page, preferences_page],
            output_dir,
        )
        if _visible_umbra_cookie_banner(page):
            _save_umbra_diagnostics(
                page, output_dir, prefix="umbra-cookie-after-reject"
            )
            raise UmbraAcqueError(
                "Il rifiuto Cookiebot è stato inviato, ma il banner separato del portale "
                "fatture è ancora visibile. Ho salvato umbra-consent-state.txt per verificare "
                "dominio e memorizzazione dei consensi; non procedo al download."
            )

        log.info(
            "Umbra Acque: consenso limitato ai cookie necessari; pagina della fattura ripristinata"
        )
        return

    # Generic CMP fallback: only operate on an actual preferences dialog/screen.
    scope = _cookie_preferences_scope(preferences_page, origin_url, is_popup)
    if scope is not None:
        if _click_first_visible_named_control(scope, UMBRA_COOKIE_REJECT_LABELS):
            preferences_page.wait_for_timeout(700)
            if is_popup:
                try:
                    preferences_page.close()
                except Exception:  # noqa: BLE001
                    pass
            if is_popup or page.url != origin_url:
                _return_to_umbra_invoice_after_cookie_choice(page, origin_url)

            if _visible_umbra_cookie_banner(page):
                _save_umbra_diagnostics(
                    page, output_dir, prefix="umbra-cookie-after-reject"
                )
                raise UmbraAcqueError(
                    "Il comando di rifiuto cookie è stato selezionato, ma il banner è ancora visibile"
                )
            log.info("Umbra Acque: cookie opzionali rifiutati tramite comando esplicito")
            return

        changed, labels = _disable_optional_cookie_checkboxes(scope)
        if changed and _click_first_visible_named_control(scope, UMBRA_COOKIE_SAVE_LABELS):
            preferences_page.wait_for_timeout(700)
            if is_popup:
                try:
                    preferences_page.close()
                except Exception:  # noqa: BLE001
                    pass
            if is_popup or page.url != origin_url:
                _return_to_umbra_invoice_after_cookie_choice(page, origin_url)
            if _visible_umbra_cookie_banner(page):
                _save_umbra_diagnostics(
                    page, output_dir, prefix="umbra-cookie-after-reject"
                )
                raise UmbraAcqueError(
                    "Le categorie opzionali sono state disattivate, ma il banner è ancora visibile"
                )
            log.info(
                "Umbra Acque: disattivate categorie cookie opzionali (%d): %s",
                changed,
                labels,
            )
            return

    _save_umbra_diagnostics(
        preferences_page, output_dir, prefix="umbra-cookie-preferences"
    )
    raise UmbraAcqueError(
        "Non ho trovato un controllo esplicito per rifiutare i cookie opzionali. "
        "Non ho premuto Accetta tutti, Accetta selezionati o Chiudi; "
        "screenshot e testo delle preferenze sono stati salvati."
    )

def _save_umbra_diagnostics(
    page, output_dir: Path, prefix: str = "umbra-error"
) -> None:
    """Save screenshot and rendered text locally; never persist the secret invoice URL."""
    screenshot = output_dir / f"{prefix}.png"
    text_file = output_dir / f"{prefix}.txt"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        body_text = page.locator("body").inner_text(timeout=5000)
        text_file.write_text(body_text, encoding="utf-8")
        log.warning(
            "Diagnostica Umbra Acque salvata: %s e %s",
            screenshot,
            text_file,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Salvataggio diagnostica Umbra Acque non riuscito (%s)",
            type(exc).__name__,
        )


def download_umbra_invoice(
    invoice_url: str,
    output_dir: Path,
    config: dict,
    debug_screenshot: bool = False,
) -> UmbraDownload:
    """Open the single-use email link and click the portal's direct PDF download."""
    parsed = urlsplit(invoice_url)
    if parsed.scheme.lower() != "https" or (parsed.hostname or "").lower() != UMBRA_HOST:
        raise UmbraAcqueError("Il link della fattura non appartiene al dominio Umbra Acque atteso")

    portal_cfg = config.get("umbraacque", {})
    timeout = int(portal_cfg.get("navigation_timeout_ms", 45000))
    download_timeout = int(portal_cfg.get("download_timeout_ms", 45000))
    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=bool(portal_cfg.get("headless", True)),
            args=["--disable-dev-shm-usage"],
        )
        page = None
        try:
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(timeout)
            page.goto(invoice_url, wait_until="domcontentloaded", timeout=timeout)
            # The first visit can display a full-screen tutorial before the invoice page.
            page.wait_for_timeout(1200)
            _dismiss_umbra_tutorial(page)
            _manage_umbra_cookie_consent(page, output_dir)

            current_host = (urlsplit(page.url).hostname or "").lower()
            if current_host != UMBRA_HOST:
                raise UmbraAcqueError(
                    "Il link Umbra Acque ha reindirizzato a un dominio diverso; download interrotto"
                )

            download_link = page.get_by_text(
                re.compile(r"^\s*Scarica\s+la\s+tua\s+bolletta\s+in\s+pdf\s*$", re.IGNORECASE)
            ).last
            download_link.wait_for(state="visible", timeout=timeout)
            page_text = page.locator("body").inner_text(timeout=10000)

            with page.expect_download(timeout=download_timeout) as download_info:
                download_link.click(timeout=15000)
            download = download_info.value

            suggested = Path(download.suggested_filename or "umbra-acque-bolletta.pdf").name
            temporary = output_dir / f"umbra-download-{suggested}"
            try:
                download.save_as(str(temporary))
                content = temporary.read_bytes()
            finally:
                temporary.unlink(missing_ok=True)

            if not content.startswith(b"%PDF-"):
                raise UmbraAcqueError(
                    "Il portale Umbra Acque non ha restituito un PDF valido"
                )
            return UmbraDownload(content, suggested, page_text)

        except Exception as exc:  # noqa: BLE001
            if debug_screenshot and page is not None:
                _save_umbra_diagnostics(page, output_dir)
            if isinstance(exc, UmbraAcqueError):
                raise
            if isinstance(exc, PlaywrightTimeoutError):
                raise UmbraAcqueError(
                    "Timeout durante apertura pagina o download della bolletta Umbra Acque"
                ) from exc
            raise UmbraAcqueError(
                f"Download Umbra Acque non riuscito ({type(exc).__name__})"
            ) from exc
        finally:
            browser.close()
