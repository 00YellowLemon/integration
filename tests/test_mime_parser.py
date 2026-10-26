"""Unit tests for MIME parsing, HTML sanitization, and attachment metadata extraction."""
import base64
from datetime import datetime, timezone
import pytest

from app.services.mime_parser import MIMEParser, ParsedMessage


def _encode_b64url(text: str) -> str:
    """Helper to URL-safe base64 encode a string without trailing '=' padding."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8").rstrip("=")


def test_decode_base64url():
    """Verify base64url decoding with and without padding."""
    text = "Hello, world! Special chars: 🚀 & <test>"
    encoded = _encode_b64url(text)
    assert MIMEParser.decode_base64url(encoded) == text
    assert MIMEParser.decode_base64url("") == ""


def test_plain_text_extraction_singlepart():
    """Verify parsing a simple single-part text/plain email."""
    body_text = "This is a single part plain text email."
    encoded_body = _encode_b64url(body_text)

    msg_resource = {
        "id": "msg_001",
        "threadId": "th_001",
        "snippet": "This is a single part...",
        "internalDate": "1729680000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Bob <bob@example.com>"},
                {"name": "Subject", "value": "Meeting Notes"},
                {"name": "Date", "value": "Fri, 23 Oct 2026 10:00:00 +0000"},
            ],
            "body": {
                "size": len(body_text),
                "data": encoded_body,
            },
        },
    }

    parsed = MIMEParser.parse_message(msg_resource)
    assert parsed.gmail_message_id == "msg_001"
    assert parsed.gmail_thread_id == "th_001"
    assert parsed.sender == "Alice <alice@example.com>"
    assert parsed.recipient == "Bob <bob@example.com>"
    assert parsed.subject == "Meeting Notes"
    assert parsed.body_text == body_text
    assert parsed.body_html is None
    assert parsed.has_attachments is False
    assert parsed.date.year == 2026
    assert parsed.date.month == 10
    assert parsed.date.day == 23


def test_multipart_alternative_extraction():
    """Verify parsing multipart/alternative email with both text/plain and text/html."""
    plain = "Plain text body version"
    html = "<p>Html <b>body</b> version</p>"

    msg_resource = {
        "id": "msg_002",
        "threadId": "th_002",
        "snippet": "Plain text body...",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "carol@example.com"},
                {"name": "To", "value": "dave@example.com"},
                {"name": "Subject", "value": "Weekly Newsletter"},
                {"name": "Date", "value": "23 Oct 2026 12:30:00 -0400"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": _encode_b64url(plain), "size": len(plain)},
                },
                {
                    "mimeType": "text/html",
                    "filename": "",
                    "body": {"data": _encode_b64url(html), "size": len(html)},
                },
            ],
        },
    }

    parsed = MIMEParser.parse_message(msg_resource)
    assert parsed.gmail_message_id == "msg_002"
    assert parsed.body_text == plain
    assert "<b>body</b>" in (parsed.body_html or "")
    assert parsed.has_attachments is False


def test_html_only_conversion_to_markdown():
    """Verify that an HTML-only email converts to clean plain markdown text."""
    html = "<h1>Important Notice</h1><p>Please review your <b>account</b> details at <a href='https://example.com'>here</a>.</p>"

    msg_resource = {
        "id": "msg_003",
        "threadId": "th_003",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "admin@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": "HTML Only"},
                {"name": "Date", "value": "Fri, 23 Oct 2026 14:00:00 +0000"},
            ],
            "body": {"data": _encode_b64url(html), "size": len(html)},
        },
    }

    parsed = MIMEParser.parse_message(msg_resource)
    assert parsed.body_text is not None
    assert "Important Notice" in parsed.body_text
    assert "account" in parsed.body_text
    assert parsed.body_html is not None


def test_malicious_html_sanitization():
    """Verify that dangerous script, iframe, and event handler tags are stripped."""
    malicious_html = """
    <div>
        <p>Legitimate content</p>
        <script>alert('xss');</script>
        <iframe src="https://attacker.com/evil"></iframe>
        <a href="javascript:stealCookies()" onclick="maliciousCode()">Click me</a>
        <img src="test.jpg" onerror="exploit()" />
        <style>body { display: none; }</style>
    </div>
    """
    sanitized = MIMEParser.sanitize_html(malicious_html)
    assert "<script" not in sanitized
    assert "alert('xss')" not in sanitized
    assert "<iframe" not in sanitized
    assert "onclick" not in sanitized
    assert "onerror" not in sanitized
    assert "javascript:" not in sanitized
    assert "<style" not in sanitized
    assert "Legitimate content" in sanitized


def test_attachment_metadata_extraction():
    """Verify attachment parts are extracted as metadata without saving raw bytes."""
    msg_resource = {
        "id": "msg_004",
        "threadId": "th_004",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "billing@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Your Invoice"},
                {"name": "Date", "value": "Fri, 23 Oct 2026 09:00:00 +0000"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "body": {"data": _encode_b64url("Please find your invoice attached."), "size": 33},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "invoice_october.pdf",
                    "body": {
                        "attachmentId": "att_blob_987654",
                        "size": 1048576,
                    },
                },
            ],
        },
    }

    parsed = MIMEParser.parse_message(msg_resource)
    assert parsed.has_attachments is True
    assert len(parsed.attachment_names) == 1
    att = parsed.attachment_names[0]
    assert att["filename"] == "invoice_october.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["size"] == 1048576
    assert att["attachment_id"] == "att_blob_987654"


def test_rfc2822_date_parsing():
    """Verify various RFC 2822 date formats and timezone conversions."""
    # Standard UTC
    d1 = MIMEParser.parse_rfc2822_date("Fri, 23 Oct 2026 10:00:00 +0000")
    assert d1.tzinfo == timezone.utc
    assert d1.hour == 10

    # Negative offset (-0400 = EDT, 10:00 EDT = 14:00 UTC)
    d2 = MIMEParser.parse_rfc2822_date("23 Oct 2026 10:00:00 -0400")
    assert d2.tzinfo == timezone.utc
    assert d2.hour == 14

    # Half hour offset (+0530 = IST, 15:30 IST = 10:00 UTC)
    d3 = MIMEParser.parse_rfc2822_date("Fri, 23 Oct 2026 15:30:00 +0530")
    assert d3.tzinfo == timezone.utc
    assert d3.hour == 10

    # Invalid string falling back to internalDate timestamp
    ms = 1792753200000  # 2026-10-23
    d4 = MIMEParser.parse_rfc2822_date("invalid date string", fallback_ms=ms)
    assert d4.tzinfo == timezone.utc
    assert d4.year == 2026

    # Completely empty date falling back to current time
    d5 = MIMEParser.parse_rfc2822_date(None)
    assert d5.tzinfo == timezone.utc
    assert isinstance(d5, datetime)
