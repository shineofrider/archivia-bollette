from __future__ import annotations

import email
from email import policy
from email.errors import HeaderParseError
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass
class MailItem:
    uid: str
    message_id: str
    sender: str
    subject: str
    date: datetime
    attachments: list[tuple[str, bytes]]
    raw_message: Message

    @property
    def key(self) -> str:
        return self.message_id or f"uid:{self.uid}:{self.sender}:{self.subject}"


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeDecodeError, HeaderParseError):
        return value


def _message_date(value: str | None) -> datetime:
    try:
        parsed = parsedate_to_datetime(value or "")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc)


def parse_message(uid: str, raw: bytes) -> MailItem:
    msg = email.message_from_bytes(raw, policy=policy.default)
    sender = parseaddr(str(msg.get("From", "")))[1].lower().strip()
    subject = decode_mime_header(str(msg.get("Subject", ""))).strip()
    message_id = str(msg.get("Message-ID", "")).strip().strip("<>")
    attachments: list[tuple[str, bytes]] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            content_disposition = part.get_content_disposition()
            if not filename and content_disposition != "attachment":
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded_name = decode_mime_header(filename or "allegato")
            attachments.append((decoded_name, payload))
    else:
        filename = msg.get_filename()
        payload = msg.get_payload(decode=True)
        if filename and payload:
            attachments.append((decode_mime_header(filename), payload))
    return MailItem(
        uid=uid,
        message_id=message_id,
        sender=sender,
        subject=subject,
        date=_message_date(str(msg.get("Date", ""))),
        attachments=attachments,
        raw_message=msg,
    )


class GmailImapClient:
    def __init__(
        self, host: str, port: int, username: str, password: str, folder: str = "INBOX"
    ) -> None:
        if not username or not password:
            raise ValueError("Configurare IMAP_USERNAME e IMAP_APP_PASSWORD nel campo Environment")
        self.host, self.port = host, port
        self.username, self.password, self.folder = username, password, folder
        self.connection: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "GmailImapClient":
        self.connection = imaplib.IMAP4_SSL(self.host, self.port, timeout=35)
        self.connection.login(self.username, self.password)
        status, _ = self.connection.select(self.folder, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Impossibile aprire la cartella IMAP {self.folder}")
        log.info("Connessione Gmail IMAP riuscita; cartella %s aperta in sola lettura", self.folder)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except imaplib.IMAP4.error:
                pass
            try:
                self.connection.logout()
            except imaplib.IMAP4.error:
                pass
            self.connection = None

    def search(
        self, sender: str, subject_fragment: str, lookback_days: int = 730
    ) -> Iterable[MailItem]:
        if self.connection is None:
            raise RuntimeError("Connessione IMAP non aperta")
        safe_sender = sender.replace('"', "")
        safe_subject = subject_fragment.replace('"', "")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, lookback_days))).strftime("%d-%b-%Y")
        criteria = f'(FROM "{safe_sender}" SUBJECT "{safe_subject}" SINCE {cutoff})'
        status, data = self.connection.uid("SEARCH", None, criteria)
        if status != "OK":
            log.error("Ricerca Gmail fallita per mittente=%s oggetto=%r: status=%s", sender, subject_fragment, status)
            return []
        if not data or not data[0]:
            log.info("Ricerca Gmail: nessuna email per mittente=%s oggetto contenente=%r", sender, subject_fragment)
            return []

        uids = data[0].split()
        if len(uids) > 250:
            log.warning("Ricerca Gmail: %d risultati; analizzo soltanto i 250 più recenti", len(uids))
        selected_uids = uids[-250:]
        log.info(
            "Ricerca Gmail: %d risultati IMAP per mittente=%s oggetto contenente=%r; "
            "analizzo %d UID dal %s",
            len(uids), sender, subject_fragment, len(selected_uids), cutoff,
        )
        items: list[MailItem] = []
        for raw_uid in selected_uids:
            uid = raw_uid.decode("ascii", errors="ignore")
            status, fetched = self.connection.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                log.warning("Impossibile leggere email UID %s", uid)
                continue
            raw_message = next(
                (entry[1] for entry in fetched
                 if isinstance(entry, tuple) and len(entry) > 1 and isinstance(entry[1], bytes)),
                None,
            )
            if raw_message is None:
                continue
            item = parse_message(uid, raw_message)
            if item.sender == sender.lower() and subject_fragment.lower() in item.subject.lower():
                items.append(item)
            else:
                log.debug(
                    "Email UID %s esclusa dal filtro finale: mittente=%r oggetto=%r",
                    uid, item.sender, item.subject,
                )
        return items
