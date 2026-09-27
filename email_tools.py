import imaplib
import os
from email.header import decode_header
from email.utils import parsedate_to_datetime

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
    """Turn an encoded email header into readable text.

    Subjects arrive as things like '=?UTF-8?B?SGVsbG8=?=' when they contain
    non-ASCII characters. decode_header splits that into parts; each part is
    either bytes (needing a charset) or already-decoded text.
    """
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
    """Assemble an IMAP search string from the supplied filters.

    IMAP search is space-separated criteria, not SQL. Multiple criteria are
    ANDed together. With no filters at all we fall back to ALL.
    """
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

        # IMAP returns ids oldest-first, so the newest are at the end.
        # Reverse after slicing so results come back newest-first.
        recent = ids[-limit:]
        recent.reverse()

        results = []
        for msg_id in recent:
            # BODY.PEEK fetches without marking the message as read.
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


if __name__ == "__main__":
    print("--- Newest 5 in inbox ---")
    for email in search_emails(limit=5):
        print(f"[{email['email_id']}] {email['subject'][:60]}")
        print(f"    from {email['sender'][:60]}")

    print()
    print("--- Unread only ---")
    for email in search_emails(unread_only=True, limit=5):
        print(f"[{email['email_id']}] {email['subject'][:60]}")
