"""MIME parsing, character decoding, HTML sanitization, and attachment metadata extraction."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import email.utils
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import dateutil.parser
import html2text


@dataclass
class ParsedMessage:
    """Structured representation of a parsed Gmail message."""

    gmail_message_id: str
    gmail_thread_id: str
    sender: str
    recipient: str
    subject: Optional[str]
    date: datetime
    snippet: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    has_attachments: bool = False
    attachment_names: List[Dict[str, Any]] = field(default_factory=list)
    raw_headers: Dict[str, Any] = field(default_factory=dict)


class MIMEParser:
    """Robust MIME parser and sanitizer for Gmail API message structures."""

    @staticmethod
    def decode_base64url(data: str) -> str:
        """Decode URL-safe Base64 string with automatic padding and encoding fallback."""
        if not data:
            return ""
        # Handle Gmail URL-safe base64 missing padding
        rem = len(data) % 4
        if rem > 0:
            data += "=" * (4 - rem)
        try:
            raw_bytes = base64.urlsafe_b64decode(data.encode("utf-8"))
        except Exception:
            return ""

        for encoding in ("utf-8", "iso-8859-1", "windows-1252"):
            try:
                return raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw_bytes.decode("utf-8", errors="replace")

    @staticmethod
    def parse_rfc2822_date(
        date_str: Optional[str],
        fallback_ms: Optional[str | int] = None,
    ) -> datetime:
        """
        Parse RFC 2822 or ISO date strings with timezone fallback to UTC.
        If date string parsing fails, uses internalDate timestamp if provided.
        """
        if date_str:
            try:
                dt = email.utils.parsedate_to_datetime(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                try:
                    dt = dateutil.parser.parse(date_str)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    pass

        if fallback_ms is not None:
            try:
                ms = int(fallback_ms)
                return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
            except Exception:
                pass

        return datetime.now(timezone.utc)

    @staticmethod
    def parse_headers(headers_list: List[Dict[str, str]]) -> Dict[str, str]:
        """Convert list of header objects into a dictionary with normalized keys."""
        headers = {}
        for item in headers_list:
            name = item.get("name")
            value = item.get("value")
            if name and value is not None:
                headers[name] = value
        return headers

    @classmethod
    def get_header(cls, headers: Dict[str, str], header_name: str, default: str = "") -> str:
        """Case-insensitive header lookup."""
        name_lower = header_name.lower()
        for k, v in headers.items():
            if k.lower() == name_lower:
                return v
        return default

    @classmethod
    def sanitize_html(cls, html_content: str) -> str:
        """
        Sanitize HTML content by removing scripts, styles, frames, embedded objects,
        form inputs, and event handler attributes (XSS prevention).
        """
        if not html_content:
            return ""

        soup = BeautifulSoup(html_content, "html.parser")

        # Decompose dangerous/executable tags
        forbidden_tags = [
            "script", "style", "iframe", "object", "embed", "form",
            "input", "button", "applet", "frame", "frameset", "base",
        ]
        for tag in soup.find_all(forbidden_tags):
            tag.decompose()

        # Remove event handlers and dangerous URI schemes
        for tag in soup.find_all(True):
            attrs = dict(tag.attrs)
            for attr, value in attrs.items():
                attr_lower = attr.lower()
                if attr_lower.startswith("on"):
                    del tag.attrs[attr]
                elif attr_lower in ("href", "src", "action") and isinstance(value, str):
                    val_strip = value.strip().lower()
                    if val_strip.startswith("javascript:") or val_strip.startswith("data:"):
                        del tag.attrs[attr]

        return str(soup)

    @classmethod
    def html_to_markdown(cls, html_content: str) -> str:
        """Convert sanitized HTML into clean plain text / markdown."""
        if not html_content:
            return ""
        sanitized = cls.sanitize_html(html_content)
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.ignore_tables = False
        h.body_width = 0
        return h.handle(sanitized).strip()

    @classmethod
    def extract_parts(
        cls,
        payload: Dict[str, Any],
    ) -> tuple[List[str], List[str], List[Dict[str, Any]]]:
        """
        Recursively extract plain text, HTML parts, and attachment metadata.
        Returns: (plain_texts, html_texts, attachments)
        """
        plain_parts: List[str] = []
        html_parts: List[str] = []
        attachments: List[Dict[str, Any]] = []

        def _walk(part: Dict[str, Any]):
            filename = part.get("filename", "")
            mime_type = part.get("mimeType", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")
            size = body.get("size", 0)

            # Detect attachment
            if filename or attachment_id or (
                size > 0
                and mime_type not in ("text/plain", "text/html")
                and not mime_type.startswith("multipart/")
            ):
                if filename or attachment_id:
                    attachments.append({
                        "filename": filename or "attachment",
                        "mime_type": mime_type or "application/octet-stream",
                        "size": size,
                        "attachment_id": attachment_id,
                    })
                    return

            body_data = body.get("data")
            if body_data:
                decoded = cls.decode_base64url(body_data)
                if mime_type == "text/plain":
                    plain_parts.append(decoded)
                elif mime_type == "text/html":
                    html_parts.append(decoded)

            for sub_part in part.get("parts", []):
                _walk(sub_part)

        _walk(payload)
        return plain_parts, html_parts, attachments

    @classmethod
    def parse_message(cls, msg_resource: Dict[str, Any]) -> ParsedMessage:
        """Parse complete Gmail API message resource into ParsedMessage model."""
        gmail_message_id = msg_resource.get("id", "")
        gmail_thread_id = msg_resource.get("threadId", "")
        snippet = msg_resource.get("snippet")

        payload = msg_resource.get("payload", {})
        headers_list = payload.get("headers", [])
        headers = cls.parse_headers(headers_list)

        sender = cls.get_header(headers, "From", default="unknown")
        recipient = cls.get_header(headers, "To", default="")
        subject = cls.get_header(headers, "Subject", default=None)
        date_str = cls.get_header(headers, "Date", default="")
        internal_date = msg_resource.get("internalDate")
        date = cls.parse_rfc2822_date(date_str, fallback_ms=internal_date)

        plain_parts, html_parts, attachments = cls.extract_parts(payload)

        body_text: Optional[str] = None
        body_html: Optional[str] = None

        if html_parts:
            raw_html = "\n".join(html_parts)
            body_html = cls.sanitize_html(raw_html)

        if plain_parts:
            body_text = "\n\n".join(p.strip() for p in plain_parts if p.strip())
        elif body_html:
            body_text = cls.html_to_markdown(body_html)

        return ParsedMessage(
            gmail_message_id=gmail_message_id,
            gmail_thread_id=gmail_thread_id,
            sender=sender,
            recipient=recipient,
            subject=subject,
            date=date,
            snippet=snippet,
            body_text=body_text,
            body_html=body_html,
            has_attachments=len(attachments) > 0,
            attachment_names=attachments,
            raw_headers=headers,
        )
