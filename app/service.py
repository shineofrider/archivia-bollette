from __future__ import annotations

import logging
import re
from pathlib import Path

from .archive import PdfArchiver
from .database import Database
from .iren import download_iren_invoice
from .mail import GmailImapClient, MailItem
from .notifications import HomeAssistantNotifier

log = logging.getLogger(__name__)


def _safe_name(name: str) -> str:
    return Path(name or "allegato.pdf").name.replace("\x00", "")


def _matching_provider(item: MailItem, providers: dict) -> tuple[str, dict] | None:
    for provider, rule in providers.items():
        if item.sender.lower() != str(rule.get("sender", "")).lower():
            continue
        if provider == "alperia":
            if re.search(str(rule.get("subject_regex", ".*")), item.subject, flags=re.IGNORECASE):
                return provider, rule
        else:
            expected = str(rule.get("subject_exact", "")).strip().lower()
            if item.subject.strip().lower() == expected:
                return provider, rule
    return None


def _archive_pdf(
    content: bytes, filename: str, provider: str, rule: dict, item: MailItem,
    archiver: PdfArchiver, database: Database, notifier: HomeAssistantNotifier,
) -> None:
    result = archiver.save(
        provider=provider,
        subdir=str(rule["archive_subdir"]),
        filename=_safe_name(filename),
        content=content,
        email_date=item.date,
        message_key=item.key,
    )
    database.add_document(
        result.digest, provider, f"{result.year:04d}_{result.month:02d}",
        str(result.path), filename, item.key,
    )
    if result.duplicate:
        log.info("Documento già archiviato: %s", result.path)
        return
    period = f"{result.year:04d}_{result.month:02d}"
    message = f"{provider.capitalize()}: bolletta {period} archiviata in {result.path.name}"
    if result.period_source == "data-email-fallback":
        message += " (periodo non letto dal PDF: utilizzata la data dell'email)"
    notifier.send(
        "Nuova bolletta archiviata", message, provider=provider,
        period=period, file=str(result.path), status="archiviata",
    )
    log.info("Archiviato %s: %s (%s)", provider, result.path, result.period_source)


def process_cycle(config: dict) -> None:
    runtime = config["runtime"]
    providers = config.get("providers", {})
    data_dir: Path = config["paths"]["data"]
    archive_root: Path = config["paths"]["archive"]
    database = Database(data_dir / "bollette.sqlite3")
    archiver = PdfArchiver(archive_root, database, config)
    notifier = HomeAssistantNotifier(runtime.get("ha_webhook_url", ""))

    if not runtime.get("imap_username") or not runtime.get("imap_password"):
        log.warning("Gmail non configurato: compilare IMAP_USERNAME e IMAP_APP_PASSWORD in Environment")
        return

    alperia_rule = providers["alperia"]
    iren_rule = providers["iren"]
    with GmailImapClient(
        runtime["imap_host"], runtime["imap_port"], runtime["imap_username"],
        runtime["imap_password"], runtime["imap_folder"],
    ) as client:
        # Alperia: acquire the PDF attached to the matching email.
        for item in client.search(
            alperia_rule["sender"], "Nuova fattura disponibile",
            int(config.get("mail", {}).get("lookback_days", 730)),
        ):
            if not _matching_provider(item, providers):
                continue
            if database.message_status(item.key) == "done":
                continue
            if not database.retry_due(item.key, 3600):
                continue
            try:
                pdfs = [
                    (name, data) for name, data in item.attachments
                    if name.lower().endswith(".pdf") or data.startswith(b"%PDF-")
                ]
                if not pdfs:
                    raise RuntimeError("Email Alperia riconosciuta ma non contiene allegati PDF")
                for name, data in pdfs:
                    _archive_pdf(data, name, "alperia", alperia_rule, item, archiver, database, notifier)
                database.set_message(item.key, "alperia", "done", "PDF acquisito", item.message_id, item.uid)
            except Exception as exc:  # noqa: BLE001
                log.exception("Errore elaborando email Alperia UID %s", item.uid)
                database.set_message(item.key, "alperia", "error", str(exc), item.message_id, item.uid)
                notifier.send(
                    "Errore acquisizione Alperia", str(exc),
                    provider="alperia", status="errore",
                )

        # Iren: Chromium is not started unless a matching notification email is found.
        for item in client.search(
            iren_rule["sender"], str(iren_rule["subject_exact"]),
            int(config.get("mail", {}).get("lookback_days", 730)),
        ):
            if not _matching_provider(item, providers):
                continue
            status = database.message_status(item.key)
            if status == "done":
                continue
            if status == "waiting_configuration":
                if not runtime.get("iren_enabled"):
                    continue
            elif not database.retry_due(item.key, runtime.get("iren_retry_seconds", 21600)):
                continue
            try:
                # If Iren attaches the PDF to the message, no browser login is needed.
                pdfs = [
                    (name, data) for name, data in item.attachments
                    if name.lower().endswith(".pdf") or data.startswith(b"%PDF-")
                ]
                if pdfs:
                    for name, data in pdfs:
                        _archive_pdf(data, name, "iren", iren_rule, item, archiver, database, notifier)
                    database.set_message(
                        item.key, "iren", "done", "PDF allegato acquisito", item.message_id, item.uid
                    )
                    continue

                if not runtime.get("iren_enabled"):
                    detail = (
                        "Avviso Iren rilevato. Playwright non avviato: "
                        "IREN_AUTOMATION_ENABLED=false. Configurare e verificare il portale prima di abilitarlo."
                    )
                    log.warning(detail)
                    database.set_message(
                        item.key, "iren", "waiting_configuration", detail, item.message_id, item.uid
                    )
                    notifier.send(
                        "Bolletta Iren disponibile", detail,
                        provider="iren", status="in_attesa",
                    )
                    continue

                log.info("Email Iren riconosciuta: avvio Playwright per questa sola fattura")
                content, filename = download_iren_invoice(runtime, config, data_dir / "tmp")
                _archive_pdf(content, filename, "iren", iren_rule, item, archiver, database, notifier)
                database.set_message(
                    item.key, "iren", "done", "PDF scaricato da IrenYou", item.message_id, item.uid
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Errore elaborando email Iren UID %s", item.uid)
                database.set_message(item.key, "iren", "error", str(exc), item.message_id, item.uid)
                notifier.send(
                    "Problema download bolletta Iren", str(exc),
                    provider="iren", status="errore",
                )
