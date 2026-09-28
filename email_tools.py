import email
import imaplib
import os
import re
from email.header import decode_header

from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
ALL_MAIL = '"[Gmail]/All Mail"'
MAX_LIMIT = 50


def connect(mailbox="INBOX"):
    """Open an authenticated IMAP connection to Gmail and select a mailbox.

    Defaults to INBOX. Pass ALL_MAIL when a search needs to see archived
    messages as well - a label search restricted to INBOX would miss
    anything already filed away.
    """
    address = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(address, password)
    mail.select(mailbox)
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


def build_criteria(query, sender, recipient, after, before,
                   unread_only, label, has_attachment):
    """Assemble an IMAP search string from the supplied filters.

    Most of these are standard IMAP. Two are Gmail extensions:
    X-GM-LABELS matches a Gmail label, and X-GM-RAW accepts Gmail's own
    search syntax, which is the only reliable way to filter on attachments.
    """
    criteria = []

    if query:
        criteria.append(f'TEXT "{query}"')
    if sender:
        criteria.append(f'FROM "{sender}"')
    if recipient:
        criteria.append(f'TO "{recipient}"')
    if after:
        criteria.append(f'SINCE "{to_imap_date(after)}"')
    if before:
        criteria.append(f'BEFORE "{to_imap_date(before)}"')
    if unread_only:
        criteria.append("UNSEEN")
    if label:
        criteria.append(f'X-GM-LABELS "{label}"')
    if has_attachment:
        criteria.append('X-GM-RAW "has:attachment"')

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


def search_emails(query=None, sender=None, recipient=None, after=None,
                  before=None, unread_only=False, label=None,
                  has_attachment=False, limit=20):
    """Search Gmail and return summaries of the newest matching messages.

    Returns a list of dicts with email_id, subject, sender, and date.
    The body is deliberately not included - use get_email for that.

    Searches INBOX by default. When a label is given, searches All Mail
    instead, since labelled messages are often archived and would otherwise
    be invisible.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    mailbox = ALL_MAIL if label else "INBOX"
    mail = connect(mailbox)
    try:
        criteria = build_criteria(query, sender, recipient, after, before,
                                  unread_only, label, has_attachment)
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


def html_to_text(html):
    """Strip HTML down to readable text.

    Marketing email often has no plain-text part at all, so the fallback is
    a wall of tags, inline CSS and tracking pixels. Feeding that to the model
    wastes context and buries the actual message. This is deliberately crude
    - it is not a parser, just enough to recover the words.
    """
    # Drop whole blocks whose contents are never readable text.
    html = re.sub(r"<(script|style|head)[^>]*>.*?</\1>", " ", html,
                  flags=re.DOTALL | re.IGNORECASE)
    # Turn block-level breaks into newlines so paragraphs survive.
    html = re.sub(r"<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>", "\n", html,
                  flags=re.IGNORECASE)
    # Remove every remaining tag.
    html = re.sub(r"<[^>]+>", " ", html)
    # Unescape the handful of entities that actually show up.
    for entity, char in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"),
                         ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"),
                         ("&mdash;", "-"), ("&ndash;", "-")]:
        html = html.replace(entity, char)
    # Collapse the whitespace the substitutions left behind.
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n\s*\n\s*\n+", "\n\n", html)
    return html.strip()


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
        text = payload.decode(charset, errors="replace")
        if msg.get_content_type() == "text/html":
            text = html_to_text(text)
        return text[:max_chars]

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
    # which happens with marketing email - and strip it first.
    if plain_text:
        return plain_text[:max_chars]
    return html_to_text(html_fallback)[:max_chars]


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


def list_labels():
    """Return the Gmail labels on this account.

    Gmail exposes labels as IMAP mailboxes. The raw response looks like
    b'(\\HasNoChildren) "/" "Receipts"' - the label name is the quoted
    string at the end. Gmail's own system folders live under [Gmail] and
    are filtered out, since they are not labels the user created.
    """
    mail = connect()
    try:
        status, data = mail.list()
        if status != "OK":
            return []

        labels = []
        for line in data:
            if line is None:
                continue
            decoded = line.decode("utf-8", errors="replace")
            parts = decoded.split(' "/" ')
            if len(parts) != 2:
                continue
            name = parts[1].strip().strip('"')
            if name.startswith("[Gmail]"):
                continue
            labels.append(name)
        return labels
    finally:
        mail.logout()


if __name__ == "__main__":
    print("--- Your labels ---")
    print(", ".join(list_labels()) or "none found")

    print()
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

    print()
    print("--- With attachments, newest 3 ---")
    for item in search_emails(has_attachment=True, limit=3):
        print(f"[{item['email_id']}] {item['subject'][:60]}")
