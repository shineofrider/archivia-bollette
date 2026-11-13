from app.mail import parse_message


def test_parse_multipart_message_and_pdf_attachment():
    raw = b'''From: Alperia <noreply@alperia.eu>\r
To: test@example.com\r
Subject: Nuova fattura disponibile Nr. 12345\r
Message-ID: <invoice-12345@example.test>\r
Date: Fri, 13 Nov 2026 09:00:00 +0100\r
MIME-Version: 1.0\r
Content-Type: multipart/mixed; boundary="BOUNDARY"\r
\r
--BOUNDARY\r
Content-Type: text/plain; charset="utf-8"\r
\r
La fattura e' disponibile.\r
--BOUNDARY\r
Content-Type: application/pdf\r
Content-Disposition: attachment; filename="fattura.pdf"\r
Content-Transfer-Encoding: base64\r
\r
JVBERi0xLjQK\r
--BOUNDARY--\r
'''
    item = parse_message("77", raw)
    assert item.uid == "77"
    assert item.sender == "noreply@alperia.eu"
    assert item.subject == "Nuova fattura disponibile Nr. 12345"
    assert item.message_id == "invoice-12345@example.test"
    assert item.attachments[0][0] == "fattura.pdf"
    assert item.attachments[0][1].startswith(b"%PDF-")
