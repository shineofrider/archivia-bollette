from datetime import datetime, timezone
from pathlib import Path
import io

from pypdf import PdfWriter

from app.archive import PdfArchiver
from app.database import Database


def pdf_bytes(width: int = 100) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=100)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_archiver_deduplicates_and_does_not_overwrite(tmp_path: Path):
    db = Database(tmp_path / "data" / "db.sqlite3")
    archiver = PdfArchiver(tmp_path / "archive", db, {"archive": {"fallback_to_email_month": True}})
    email_date = datetime(2026, 11, 13, tzinfo=timezone.utc)

    first_content = pdf_bytes(100)
    first = archiver.save("alperia", "Corrente", "bolletta.pdf", first_content, email_date, "msg-1")
    assert first.path.name == "2026_11.pdf"
    assert first.path.read_bytes() == first_content
    assert first.duplicate is False

    duplicate = archiver.save("alperia", "Corrente", "bolletta.pdf", first_content, email_date, "msg-2")
    assert duplicate.duplicate is True
    assert duplicate.path == first.path

    different_content = pdf_bytes(120)
    second = archiver.save("alperia", "Corrente", "bolletta-corretta.pdf", different_content, email_date, "msg-3")
    assert second.path != first.path
    assert second.path.exists()
    assert first.path.read_bytes() == first_content
