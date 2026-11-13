from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

log = logging.getLogger(__name__)


class IrenPortalError(RuntimeError):
    pass


def download_iren_invoice(runtime: dict, config: dict, output_dir: Path) -> tuple[bytes, str]:
    """Attempt a browser session only after a matching Iren notice is received.

    The authenticated DOM cannot be verified without the account owner. Selectors are
    configurable; CAPTCHA, OTP and MFA are not bypassed.
    """
    if not runtime.get("iren_username") or not runtime.get("iren_password"):
        raise IrenPortalError("Credenziali IrenYou non configurate nel campo Environment")

    selectors = {
        "username": runtime.get("iren_username_selector") or
                    'input[type="email"], input[name*="user" i], input[name*="email" i]',
        "password": runtime.get("iren_password_selector") or 'input[type="password"]',
        "submit": runtime.get("iren_submit_selector") or 'button[type="submit"]',
        "download": runtime.get("iren_download_selector") or
                    'a[href$=".pdf"], a[download], button[aria-label*="download" i]',
    }
    portal_cfg = config.get("iren", {})
    timeout = int(portal_cfg.get("navigation_timeout_ms", 45000))
    download_timeout = int(portal_cfg.get("download_timeout_ms", 45000))
    profile_dir = output_dir / "iren-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

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
            page.goto(runtime.get("iren_login_url"), wait_until="domcontentloaded", timeout=timeout)
            page.wait_for_timeout(2000)

            password_box = page.locator(selectors["password"]).first
            if password_box.count() and password_box.is_visible():
                user_box = page.locator(selectors["username"]).first
                if not user_box.count() or not user_box.is_visible():
                    user_box = page.locator('input[type="text"]').first
                if not user_box.count() or not user_box.is_visible():
                    raise IrenPortalError("Campo username non individuato: configurare IREN_USERNAME_SELECTOR")
                user_box.fill(runtime["iren_username"])
                password_box.fill(runtime["iren_password"])
                submit = page.locator(selectors["submit"]).first
                if not submit.count() or not submit.is_visible():
                    raise IrenPortalError("Pulsante di accesso non individuato: configurare IREN_SUBMIT_SELECTOR")
                submit.click()
                page.wait_for_timeout(4000)

            body_text = page.locator("body").inner_text(timeout=10000).lower()
            if any(token in body_text for token in (
                "codice otp", "codice di verifica", "autenticazione a due", "captcha", "recaptcha"
            )):
                raise IrenPortalError(
                    "IrenYou richiede un controllo aggiuntivo (OTP/CAPTCHA): serve un passaggio manuale"
                )
            if page.locator(selectors["password"]).count() and page.locator(selectors["password"]).first.is_visible():
                raise IrenPortalError("Accesso IrenYou non completato; verificare credenziali o selettori")

            bills_url = runtime.get("iren_bills_url", "").strip()
            if bills_url:
                page.goto(bills_url, wait_until="domcontentloaded", timeout=timeout)
            else:
                bill_link = page.locator("a").filter(
                    has_text=re.compile(r"bollette|fatture|archivio documenti", re.I)
                ).first
                if not bill_link.count():
                    bill_link = page.get_by_role(
                        "button", name=re.compile(r"bollette|fatture|archivio documenti", re.I)
                    ).first
                if not bill_link.count():
                    raise IrenPortalError(
                        "Accesso riuscito ma sezione bollette non individuata; impostare IREN_BILLS_URL"
                    )
                bill_link.click()
                page.wait_for_load_state("domcontentloaded", timeout=timeout)

            download_locator = page.locator(selectors["download"])
            count = download_locator.count()
            if count == 0:
                raise IrenPortalError(
                    "Nessun collegamento di download trovato; verificare IREN_DOWNLOAD_SELECTOR"
                )
            if count > 1:
                raise IrenPortalError(
                    f"Trovati {count} possibili documenti: configurare IREN_DOWNLOAD_SELECTOR "
                    "in modo che individui solo la bolletta più recente"
                )

            output_dir.mkdir(parents=True, exist_ok=True)
            with page.expect_download(timeout=download_timeout) as download_info:
                download_locator.first.click()
            download = download_info.value
            suggested = Path(download.suggested_filename or "iren-bolletta.pdf").name
            target = output_dir / suggested
            download.save_as(str(target))
            content = target.read_bytes()
            target.unlink(missing_ok=True)
            if not content.startswith(b"%PDF-"):
                raise IrenPortalError("Il download Iren non è un PDF valido")
            return content, suggested
        except PlaywrightTimeoutError as exc:
            if runtime.get("iren_debug_screenshot") and browser.pages:
                try:
                    page.screenshot(path=str(output_dir / "iren-error.png"), full_page=True)
                except Exception:  # noqa: BLE001
                    pass
            raise IrenPortalError("Timeout durante l'accesso o il download da IrenYou") from exc
        finally:
            browser.close()
