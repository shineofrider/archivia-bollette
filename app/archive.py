from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .pdf_utils import infer_period, validate_and_extract_text

log = logging.getLogger(__name__)


@dataclass
class ArchiveResult:
    path: Path
    year: int
    month: int
    digest: str
    duplicate: bool
    period_source: str


class PdfArchiver:
    def __init__(self, archive_root: Path, database: Database, config: dict) -> None:
        self.archive_root = archive_root
        self.database = database
        self.config = config

    def save(
        self, provider: str, subdir: str, filename: str, content: bytes,
        email_date, message_key: str,
    ) -> ArchiveResult:
        text = validate_and_extract_text(content)
        year, month, period_source = infer_period(
            text, email_date,
            fallback_to_email_month=bool(self.config.get("archive", {}).get("fallback_to_email_month", True)),
        )
        digest = hashlib.sha256(content).hexdigest()
        known = self.database.find_document(digest)
        if known:
            return ArchiveResult(Path(known["path"]), year, month, digest, True, period_source)

        target_dir = self.archive_root / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{year:04d}_{month:02d}.pdf"

        if target.exists():
            existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing_digest == digest:
                self.database.add_document(
                    digest, provider, f"{year:04d}_{month:02d}", str(target), filename, message_key
                )
                return ArchiveResult(target, year, month, digest, True, period_source)
            suffix = self.config.get("archive", {}).get(
                "collision_suffix", "_versione_{digest8}"
            ).format(digest8=digest[:8])
            target = target_dir / f"{year:04d}_{month:02d}{suffix}.pdf"
            if target.exists():
                raise FileExistsError(
                    f"Esiste già una versione alternativa per {year:04d}_{month:02d}; nessun file sovrascritto"
                )

        temp = target.with_suffix(target.suffix + ".part")
        try:
            with temp.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

        self.database.add_document(
            digest, provider, f"{year:04d}_{month:02d}", str(target), filename, message_key
        )
        return ArchiveResult(target, year, month, digest, False, period_source)
