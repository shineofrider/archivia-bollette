from datetime import datetime, timezone
import io

from pypdf import PdfWriter

from app.pdf_utils import infer_period, validate_and_extract_text


def test_infer_period_from_date_range():
    email_date = datetime(2026, 11, 3, tzinfo=timezone.utc)
    year, month, source = infer_period("Periodo di fatturazione 01/10/2026 - 31/10/2026", email_date)
    assert (year, month, source) == (2026, 10, "periodo-fatturazione")


def test_infer_period_from_named_month():
    email_date = datetime(2026, 11, 3, tzinfo=timezone.utc)
    year, month, source = infer_period("Periodo di riferimento: ottobre 2026", email_date)
    assert (year, month, source) == (2026, 10, "mese-testuale")


def test_infer_period_falls_back_to_email_date():
    email_date = datetime(2026, 11, 3, tzinfo=timezone.utc)
    year, month, source = infer_period("Nessun periodo leggibile", email_date)
    assert (year, month, source) == (2026, 11, "data-email-fallback")


def test_validate_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    assert validate_and_extract_text(output.getvalue()) == ""
