from __future__ import annotations

import logging
from pathlib import Path

from .archive import PdfArchiver
from .database import Database
from .iren import sync_iren_invoices
from .mail import GmailImapClient, MailItem
from .notifications import HomeAssistantNotifier

log = logging.getLogger(__name__)
ALPERIA_SUBJECT_PREFIX = "Nuova fattura disponibile"

# A forced test execution is granted once per container process, not once forever
# in SQLite. Restarting the container allows another controlled attempt.
_FORCED_TEST_ATTEMPTS: set[str] = set()


def _claim_test_force_once(message_key: str) -> bool:
    if message_key in _FORCED_TEST_ATTEMPTS:
        return False
    _FORCED_TEST_ATTEMPTS.add(message_key)
    return True


def _safe_name(name: str) -> str:
    return Path(name or "allegato.pdf").name.replace("\x00", "")


def _matching_provider(item: MailItem, providers: dict) -> tuple[str, dict] | None:
    """Return the provider only when sender and subject rules both match."""
    for provider, rule in providers.items():
        if item.sender.strip().casefold() != str(rule.get("sender", "")).strip().casefold():
            continue
        if provider == "alperia":
            prefix = str(rule.get("subject_prefix", ALPERIA_SUBJECT_PREFIX)).strip()
            if item.subject.strip().casefold().startswith(prefix.casefold()):
                return provider, rule
        else:
            expected = str(rule.get("subject_exact", "")).strip()
            if item.subject.strip().casefold() == expected.casefold():
                return provider, rule
    return None


def _pdf_attachments(item: MailItem) -> list[tuple[str, bytes]]:
    return [
        (name, data) for name, data in item.attachments
        if name.lower().endswith(".pdf") or data.startswith(b"%PDF-")
    ]


def _format_notification_date(value: str) -> str:
    """Format an ISO date as DD/MM/YYYY while preserving unknown values."""
    if not value:
        return ""
    try:
        from datetime import date
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _iren_notification_details(invoice) -> tuple[str, dict[str, str]]:
    """Build a human-readable HA message and structured fields for an Iren bill."""
    lines: list[str] = []
    extra: dict[str, str] = {}

    if invoice.amount:
        lines.append(f"Importo da pagare: {invoice.amount}")
        extra["amount"] = invoice.amount
    if invoice.due_date:
        due_date = _format_notification_date(invoice.due_date)
        lines.append(f"Scadenza: {due_date}")
        extra["due_date"] = due_date
    if invoice.issue_date:
        issue_date = _format_notification_date(invoice.issue_date)
        lines.append(f"Data emissione: {issue_date}")
        extra["issue_date"] = issue_date
    if invoice.invoice_number:
        lines.append(f"Numero fattura: {invoice.invoice_number}")
        extra["invoice_number"] = invoice.invoice_number

    if invoice.portal_id:
        extra["portal_invoice_id"] = invoice.portal_id

    return "\\n".join(lines), extra


def _archive_pdf(
    content: bytes, filename: str, provider: str, rule: dict, item: MailItem,
    archiver: PdfArchiver, database: Database, notifier: HomeAssistantNotifier,
    notification_suffix: str = "",
    notification_extra: dict[str, str] | None = None,
) -> Path:
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
        log.info("Documento già archiviato (SHA-256 già presente): %s", result.path)
        return result.path
    period = f"{result.year:04d}_{result.month:02d}"
    message = f"{provider.capitalize()}: bolletta {period} archiviata in {result.path.name}"
    if result.period_source == "data-email-fallback":
        message += " (periodo non letto dal PDF: utilizzata la data dell'email)"
    if notification_suffix:
        message += "\\n" + notification_suffix
    notifier.send(
        "Nuova bolletta archiviata", message, provider=provider,
        period=period, file=str(result.path), status="archiviata",
        **(notification_extra or {}),
    )
    log.info("Archiviato %s: %s (%s)", provider, result.path, result.period_source)
    return result.path


def _log_test_scan(provider: str, item: MailItem, matched: bool, database: Database) -> None:
    pdf_names = [name for name, _ in _pdf_attachments(item)]
    log.info(
        "[TEST SCAN] fornitore=%s uid=%s riconosciuta=%s stato_db=%s "
        "mittente=%r oggetto=%r allegati=%d pdf=%s",
        provider, item.uid, matched, database.message_status(item.key),
        item.sender, item.subject, len(item.attachments), pdf_names,
    )


def _process_iren_portal(
    item: MailItem,
    rule: dict,
    runtime: dict,
    config: dict,
    data_dir: Path,
    archiver: PdfArchiver,
    database: Database,
    notifier: HomeAssistantNotifier,
) -> None:
    baseline_initialized = database.iren_baseline_initialized()
    known_statuses = database.iren_invoice_statuses()
    result = sync_iren_invoices(
        runtime=runtime,
        config=config,
        output_dir=data_dir / "tmp",
        known_statuses=known_statuses,
        baseline_initialized=baseline_initialized,
    )

    if result.first_sync:
        newest_portal_id = result.selected_portal_ids[0] if result.selected_portal_ids else ""
        database.initialize_iren_baseline(
            [invoice.as_db_dict() for invoice in result.invoices],
            newest_portal_id,
        )
        log.info(
            "Storico iniziale Iren registrato: %d fatture precedenti, fattura più recente=%s",
            max(0, len(result.invoices) - 1), newest_portal_id or "nessuna",
        )
    else:
        for invoice in result.invoices:
            database.upsert_iren_invoice(invoice.as_db_dict())

    failures = list(result.failures)
    archived_count = 0

    for downloaded in result.downloads:
        invoice = downloaded.invoice
        try:
            database.upsert_iren_invoice(invoice.as_db_dict())
            notification_suffix, notification_extra = _iren_notification_details(invoice)
            saved_path = _archive_pdf(
                downloaded.content, downloaded.filename, "iren", rule, item,
                archiver, database, notifier,
                notification_suffix=notification_suffix,
                notification_extra=notification_extra,
            )
            database.mark_iren_invoice_downloaded(
                invoice.portal_id, str(saved_path), invoice.invoice_number or None
            )
            archived_count += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Errore archiviando la fattura Iren %s", invoice.portal_id)
            failures.append((invoice.portal_id, f"Archiviazione PDF non riuscita: {exc}"))

    if failures:
        details = "; ".join(f"{portal_id}: {message}" for portal_id, message in failures)
        raise RuntimeError(details)

    if archived_count:
        detail = f"Sincronizzazione Iren completata: scaricate e archiviate {archived_count} nuove bollette"
    elif result.first_sync:
        detail = "Storico Iren inizializzato; nessuna nuova bolletta da scaricare"
    else:
        detail = "Controllo Iren completato: nessuna nuova bolletta disponibile"
    database.set_message(item.key, "iren", "done", detail, item.message_id, item.uid)
    log.info(detail)


def process_cycle(config: dict) -> None:
    runtime = config["runtime"]
    providers = config.get("providers", {})
    data_dir: Path = config["paths"]["data"]
    archive_root: Path = config["paths"]["archive"]
    database = Database(data_dir / "bollette.sqlite3")
    archiver = PdfArchiver(archive_root, database, config)
    notifier = HomeAssistantNotifier(runtime.get("ha_webhook_url", ""))

    test_mode = str(runtime.get("test_mode", "off")).strip().lower()
    test_provider = str(runtime.get("test_provider", "all")).strip().lower()
    test_uid = str(runtime.get("test_uid", "")).strip()
    force_test = bool(runtime.get("test_force_reprocess", False))
    if test_mode not in {"off", "scan", "process"}:
        raise ValueError("TEST_MODE deve essere off, scan oppure process")
    if test_provider not in {"all", "alperia", "iren"}:
        raise ValueError("TEST_PROVIDER deve essere all, alperia oppure iren")
    if test_mode == "process" and not test_uid:
        log.error("TEST_MODE=process richiede TEST_UID: non elaboro nessuna email")
        return

    if not runtime.get("imap_username") or not runtime.get("imap_password"):
        log.warning("Gmail non configurato: compilare IMAP_USERNAME e IMAP_APP_PASSWORD in Environment")
        return

    if test_mode == "scan":
        log.warning("MODALITÀ TEST SCAN attiva: nessun PDF verrà archiviato e nessuna notifica verrà inviata")
    elif test_mode == "process":
        log.warning(
            "MODALITÀ TEST PROCESS attiva: elaborazione limitata a UID %s (fornitore=%s, force=%s)",
            test_uid, test_provider, force_test,
        )
    else:
        log.info("Ciclo controllo Gmail avviato")

    lookback_days = int(config.get("mail", {}).get("lookback_days", 730))
    searches = (
        ("alperia", providers["alperia"], str(providers["alperia"].get("subject_prefix", ALPERIA_SUBJECT_PREFIX))),
        ("iren", providers["iren"], str(providers["iren"]["subject_exact"])),
    )
    seen_test_uid: set[str] = set()

    with GmailImapClient(
        runtime["imap_host"], runtime["imap_port"], runtime["imap_username"],
        runtime["imap_password"], runtime["imap_folder"],
    ) as client:
        for provider, rule, subject_query in searches:
            if test_mode != "off" and test_provider not in {"all", provider}:
                continue

            candidates = list(client.search(rule["sender"], subject_query, lookback_days))
            log.info(
                "Gmail %s: %d email candidate (cartella=%s, finestra=%d giorni)",
                provider.capitalize(), len(candidates), runtime["imap_folder"], lookback_days,
            )

            for item in candidates:
                matched_result = _matching_provider(item, providers)
                matched = matched_result is not None and matched_result[0] == provider

                if test_mode == "scan":
                    if test_uid and item.uid != test_uid:
                        continue
                    _log_test_scan(provider, item, matched, database)
                    continue

                if test_mode == "process":
                    if item.uid != test_uid:
                        continue
                    seen_test_uid.add(item.uid)
                    if not matched:
                        log.error(
                            "[TEST PROCESS] UID %s trovato ma non supera il filtro mittente/oggetto: "
                            "mittente=%r oggetto=%r",
                            item.uid, item.sender, item.subject,
                        )
                        continue
                elif not matched:
                    log.warning(
                        "Email ignorata per %s: UID=%s mittente=%r oggetto=%r "
                        "(non rispetta il filtro del fornitore)",
                        provider, item.uid, item.sender, item.subject,
                    )
                    continue

                status = database.message_status(item.key)
                if provider == "iren" and status == "waiting_configuration" and not runtime.get("iren_enabled"):
                    log.info("Iren UID %s già in attesa di configurazione: nessuna nuova notifica", item.uid)
                    continue

                force_granted = (
                    test_mode == "process" and force_test and _claim_test_force_once(item.key)
                )
                if test_mode == "process" and force_test and not force_granted:
                    log.warning(
                        "TEST_FORCE_REPROCESS già utilizzata per UID %s in questo avvio; "
                        "riavviare il container per un nuovo tentativo controllato",
                        item.uid,
                    )
                if status == "done" and not force_granted:
                    log.info("%s UID %s già completata: salto", provider.capitalize(), item.uid)
                    continue

                retry_seconds = 3600 if provider == "alperia" else int(
                    runtime.get("iren_retry_seconds", 21600)
                )
                if status != "done" and not force_granted:
                    if provider == "iren" and status == "waiting_configuration" and runtime.get("iren_enabled"):
                        pass
                    elif not database.retry_due(item.key, retry_seconds):
                        log.info(
                            "%s UID %s in attesa del prossimo tentativo (intervallo=%d secondi)",
                            provider.capitalize(), item.uid, retry_seconds,
                        )
                        continue

                try:
                    pdfs = _pdf_attachments(item)
                    log.info(
                        "%s UID %s riconosciuta: %d allegati, %d PDF",
                        provider.capitalize(), item.uid, len(item.attachments), len(pdfs),
                    )

                    if provider == "alperia":
                        if not pdfs:
                            raise RuntimeError("Email Alperia riconosciuta ma non contiene allegati PDF")
                        for name, data in pdfs:
                            _archive_pdf(data, name, "alperia", rule, item, archiver, database, notifier)
                        database.set_message(
                            item.key, "alperia", "done", "PDF acquisito", item.message_id, item.uid
                        )
                        log.info("Alperia UID %s completata", item.uid)
                        continue

                    if pdfs:
                        for name, data in pdfs:
                            _archive_pdf(data, name, "iren", rule, item, archiver, database, notifier)
                        database.set_message(
                            item.key, "iren", "done", "PDF allegato acquisito", item.message_id, item.uid
                        )
                        log.info("Iren UID %s completata: PDF presente nell'email", item.uid)
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

                    log.info("Email Iren riconosciuta: avvio sincronizzazione portale e confronto fatture")
                    _process_iren_portal(
                        item, rule, runtime, config, data_dir, archiver, database, notifier
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception("Errore elaborando email %s UID %s", provider.capitalize(), item.uid)
                    database.set_message(item.key, provider, "error", str(exc), item.message_id, item.uid)
                    notifier.send(
                        "Errore acquisizione Alperia" if provider == "alperia" else "Problema download bolletta Iren",
                        str(exc), provider=provider, status="errore",
                    )

        if test_mode == "process" and test_uid not in seen_test_uid:
            log.error(
                "[TEST PROCESS] UID %s non trovato tra i messaggi del fornitore selezionato. "
                "Verificare TEST_PROVIDER, IMAP_FOLDER e che l'email rientri nella ricerca.",
                test_uid,
            )
