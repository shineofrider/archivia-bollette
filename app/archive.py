from __future__ import annotations

import hashlib
import logging
import os
import re
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

    @staticmethod
    def _write_atomic(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Il percorso esiste già; nessun file sovrascritto: {target}")
        temp = target.with_suffix(target.suffix + ".part")
        try:
            with temp.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists():
                raise FileExistsError(f"Il percorso è stato creato durante il salvataggio: {target}")
            temp.rename(target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    def save(
        self,
        provider: str,
        subdir: str,
        filename: str,
        content: bytes,
        email_date,
        message_key: str,
        target_filename: str | None = None,
        period_key: str | None = None,
    ) -> ArchiveResult:
        text = validate_and_extract_text(content)
        year, month, period_source = infer_period(
            text,
            email_date,
            fallback_to_email_month=bool(
                self.config.get("archive", {}).get("fallback_to_email_month", True)
            ),
        )
        digest = hashlib.sha256(content).hexdigest()
        stored_period = period_key or f"{year:04d}_{month:02d}"

        if target_filename:
            if (
                Path(target_filename).name != target_filename
                or not target_filename.lower().endswith(".pdf")
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", target_filename)
            ):
                raise ValueError("Nome file di archivio personalizzato non valido")
            base_filename = target_filename
        else:
            filename_format = self.config.get("archive", {}).get(
                "filename_format", "{year_short:02d}_{month:02d}.pdf"
            )
            try:
                base_filename = str(filename_format).format(
                    year=year,
                    year_short=year % 100,
                    month=month,
                )
            except (KeyError, IndexError, ValueError, AttributeError) as exc:
                raise ValueError(
                    "Formato del nome file di archivio non valido: "
                    "usare i campi {year}, {year_short} e {month}"
                ) from exc
            if (
                Path(base_filename).name != base_filename
                or not base_filename.lower().endswith(".pdf")
                or not re.fullmatch(r"[A-Za-z0-9_.-]+", base_filename)
            ):
                raise ValueError("Formato del nome file di archivio non valido")

        known = self.database.find_document(digest)
        if known:
            known_path = Path(known["path"])
            if known_path.is_file():
                existing_digest = hashlib.sha256(known_path.read_bytes()).hexdigest()
                if existing_digest == digest:
                    return ArchiveResult(known_path, year, month, digest, True, period_source)
                raise FileExistsError(
                    f"Il database associa il PDF a {known_path}, ma il contenuto del file è diverso; "
                    "non sovrascrivo il documento."
                )
            if known_path.exists():
                raise FileExistsError(
                    f"Il percorso registrato nel database non è un file regolare: {known_path}"
                )
            log.warning(
                "PDF già registrato nel database ma file assente; ripristino il documento in %s",
                known_path,
            )
            self._write_atomic(known_path, content)
            return ArchiveResult(known_path, year, month, digest, False, period_source)

        target_dir = self.archive_root / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / base_filename

        if target.exists():
            existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if existing_digest == digest:
                self.database.add_document(
                    digest, provider, stored_period, str(target), filename, message_key
                )
                return ArchiveResult(target, year, month, digest, True, period_source)

            suffix = self.config.get("archive", {}).get(
                "collision_suffix", "_versione_{digest8}"
            ).format(digest8=digest[:8])
            target = target_dir / f"{Path(base_filename).stem}{suffix}.pdf"
            if target.exists():
                alternative_digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if alternative_digest == digest:
                    self.database.add_document(
                        digest, provider, stored_period, str(target), filename, message_key
                    )
                    return ArchiveResult(target, year, month, digest, True, period_source)
                raise FileExistsError(
                    f"Esiste già una versione alternativa per {base_filename}; "
                    "nessun file sovrascritto"
                )

        self._write_atomic(target, content)
        self.database.add_document(
            digest, provider, stored_period, str(target), filename, message_key
        )
        return ArchiveResult(target, year, month, digest, False, period_source)
