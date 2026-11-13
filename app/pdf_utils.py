from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timezone

from pypdf import PdfReader

log = logging.getLogger(__name__)
MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9,
    "ottobre": 10, "novembre": 11, "dicembre": 12,
}


def validate_and_extract_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise ValueError("L'allegato non ha l'intestazione di un PDF valido")
    reader = PdfReader(io.BytesIO(content), strict=False)
    if not reader.pages:
        raise ValueError("Il PDF non contiene pagine")
    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("Impossibile estrarre testo da una pagina PDF: %s", exc)
    return "\n".join(chunks)


def _parse_date(value: str) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            pass
    return None


def infer_period(
    text: str, email_date: datetime, fallback_to_email_month: bool = True
) -> tuple[int, int, str]:
    normalized = re.sub(r"\s+", " ", text or "")
    date_range = re.search(
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{4})\s*(?:-|–|al|a)\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
        normalized,
        flags=re.IGNORECASE,
    )
    if date_range:
        end_date = _parse_date(date_range.group(2).replace("-", "/"))
        if end_date:
            return end_date.year, end_date.month, "periodo-fatturazione"
        end_date = _parse_date(date_range.group(2))
        if end_date:
            return end_date.year, end_date.month, "periodo-fatturazione"

    month_year = re.search(
        r"(?:periodo|riferimento|fatturazione|consumi|fornitura)[^.!?]{0,100}?\b("
        + "|".join(MONTHS) + r")\s+(20\d{2})\b",
        normalized,
        flags=re.IGNORECASE,
    )
    if month_year:
        return int(month_year.group(2)), MONTHS[month_year.group(1).lower()], "mese-testuale"

    if fallback_to_email_month:
        dt = email_date.astimezone() if email_date.tzinfo else email_date.replace(tzinfo=timezone.utc).astimezone()
        return dt.year, dt.month, "data-email-fallback"
    raise ValueError("Non è stato possibile determinare il periodo della bolletta dal PDF")
