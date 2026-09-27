import email
import imaplib
import os
from email.header import decode_header

from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993


def connect():
    """Open an authenticated IMAP connection to Gmail and select the inbox."""
    address = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(address, password)
    mail.select("INBOX")
    return mail


def decode_field(raw):
    """Turn an encoded email header into readable text."""
    if raw is None:
        return ""
    parts = []
    for text, charset in decode_header(raw):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def to_imap_date(date_str):
    """Convert YYYY-MM-DD to IMAP's DD-Mon-YYYY format."""
    from datetime import datetime

    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%b-%Y")


def build_criteria(query, sender, after, before, unread_only):
    """Assemble an IMAP search string from the supplied filters."""
    criteria = []

    if query:
        criteria.append(f'TEXT "{query}"')
    if sender:
        criteria.append(f'FROM "{sender}"')
    if after:
        criteria.append(f'SINCE "{to_imap_date(after)}"')
    if before:
        criteria.append(f'BEFORE "{to_imap_date(before)}"')
    if unread_only:
        criteria.append("UNSEEN")

    if not criteria:
        return "ALL"
    return " ".join(criteria)


def parse_headers(raw):
    """Pull header lines into a dict, handling folded continuation lines."""
    headers = {}
    current_key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and current_key:
            headers[current_key] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            current_key = key.strip()
            headers[current_key] = value.strip()
    return headers


def search_emails(query=None, sender=None, after=None, before=None,
                  unread_only=False, limit=20):
    """Search the inbox and return summaries of the newest matching messages.

    Returns a list of dicts with email_id, subject, sender, and date.
    The body is deliberately not included - use get_email for that.
    """
    mail = connect()
    try:
        criteria = build_criteria(query, sender, after, before, unread_only)
        status, data = mail.search(None, criteria)

        if status != "OK":
            return []

        ids = data[0].split()
        if not ids:
            return []

        recent = ids[-limit:]
        recent.reverse()

        results = []
        for msg_id in recent:
            status, msg_data = mail.fetch(
                msg_id,
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])",
            )
            if status != "OK":
                continue

            raw = msg_data[0][1].decode("utf-8", errors="replace")
            headers = parse_headers(raw)

            results.append({
                "email_id": msg_id.decode(),
                "subject": decode_field(headers.get("Subject")),
                "sender": decode_field(headers.get("From")),
                "date": headers.get("Date", ""),
            })

        return results
    finally:
        mail.logout()


def extract_body(msg, max_chars=4000):
    """Pull readable plain text out of a parsed message.

    Most real emails are multipart: the same content sent twice, once as
    text/plain and once as text/html. We want the plain part. Attachments
    also show up as parts, so we skip anything marked as an attachment.

    Truncated because a long newsletter would otherwise flood the model's
    context window for no benefit.
    """
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")[:max_chars]

    plain_text = ""
    html_fallback = ""

    for part in msg.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "")

        if "attachment" in disposition:
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        charset = part.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")

        if content_type == "text/plain" and not plain_text:
            plain_text = text
        elif content_type == "text/html" and not html_fallback:
            html_fallback = text

    # Prefer plain text. Fall back to HTML only when there is no plain part,
    # which happens with marketing email.
    body = plain_text or html_fallback
    return body[:max_chars]


def list_attachments(msg):
    """Return the filenames of any attachments, without downloading them."""
    names = []
    if not msg.is_multipart():
        return names
    for part in msg.walk():
        disposition = str(part.get("Content-Disposition") or "")
        if "attachment" in disposition:
            filename = part.get_filename()
            if filename:
                names.append(decode_field(filename))
    return names


def get_email(email_id):
    """Fetch one message in full, including its body text.

    Takes an email_id as returned by search_emails. Returns a dict, or None
    if the id does not resolve to a message.
    """
    mail = connect()
    try:
        status, msg_data = mail.fetch(str(email_id), "(BODY.PEEK[])")

        if status != "OK" or not msg_data or msg_data[0] is None:
            return None

        msg = email.message_from_bytes(msg_data[0][1])

        return {
            "email_id": str(email_id),
            "subject": decode_field(msg["Subject"]),
            "sender": decode_field(msg["From"]),
            "recipient": decode_field(msg["To"]),
            "date": msg["Date"] or "",
            "body": extract_body(msg),
            "attachments": list_attachments(msg),
        }
    finally:
        mail.logout()


if __name__ == "__main__":
    print("--- Newest 3 in inbox ---")
    recent = search_emails(limit=3)
    for item in recent:
        print(f"[{item['email_id']}] {item['subject'][:60]}")
        print(f"    from {item['sender'][:60]}")

    if recent:
        print()
        print("--- Full message for the newest one ---")
        full = get_email(recent[0]["email_id"])
        print("Subject:   ", full["subject"])
        print("From:      ", full["sender"])
        print("To:        ", full["recipient"])
        print("Date:      ", full["date"])
        print("Attachments:", full["attachments"] or "none")
        print("Body preview:")
        print(full["body"][:400])
