import email
import imaplib
import os
import re
from collections import Counter
from email.utils import parseaddr
from email.header import decode_header

from dotenv import load_dotenv

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
ALL_MAIL = '"[Gmail]/All Mail"'
MAX_LIMIT = 50

# summarise_senders scans every matching message's From header. On a large
# mailbox that is a lot of rows, so there is a ceiling: better to report
# "I stopped at 2000" than to hang for a minute.
MAX_SCAN = 2000


def connect(mailbox=ALL_MAIL):
    """Open an authenticated IMAP connection to Gmail and select a mailbox.

    Always selects All Mail by default, and every id this module hands out
    is a UID from that mailbox.

    The reason matters: plain IMAP message numbers are POSITIONS within
    whichever mailbox is selected, so message 5547 in INBOX and message
    5547 in All Mail are different emails, and both shift as mail arrives.
    Searching one mailbox and fetching from another silently returns the
    wrong message. Working in a single mailbox and using UIDs, which are
    stable, removes that whole class of bug.
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
    elif not recipient:
        # Default scope is the inbox. Since we are always selected on All
        # Mail, that has to be expressed as a search term rather than by
        # choosing a mailbox. A label search or a sent-mail search is
        # deliberately allowed to range wider.
        criteria.append('X-GM-RAW "in:inbox"')
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

    Ids returned are UIDs in All Mail - stable, and safe to pass straight
    to get_email. Scope defaults to the inbox; a label filter or a
    recipient filter widens it to all mail, since labelled messages are
    usually archived and sent mail never appears in the inbox at all.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    mail = connect()
    try:
        criteria = build_criteria(query, sender, recipient, after, before,
                                  unread_only, label, has_attachment)
        status, data = mail.uid("SEARCH", None, criteria)

        if status != "OK":
            return []

        ids = data[0].split()
        if not ids:
            return []

        recent = ids[-limit:]
        recent.reverse()

        results = []
        for msg_id in recent:
            status, msg_data = mail.uid(
                "FETCH",
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

    Takes an email_id as returned by search_emails - a UID in All Mail.
    Returns a dict, or None if the id does not resolve to a message.
    """
    mail = connect()
    try:
        # UID FETCH, matching the UIDs search_emails handed out.
        status, msg_data = mail.uid("FETCH", str(email_id), "(BODY.PEEK[])")

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


def root_domain(address):
    """Reduce an address to its registrable-looking domain.

    mailer.remote4africa.com and engage.remote4africa.com both become
    remote4africa.com. This is a heuristic, not a public-suffix lookup:
    it keeps three parts for two-level country domains like .co.ke so
    jumia.co.ke does not collapse to co.ke.
    """
    domain = address.split("@")[-1].lower().strip()
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    if len(parts[-1]) == 2 and len(parts[-2]) <= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def summarise_senders(query=None, sender=None, after=None, before=None,
                      label=None, has_attachment=False):
    """Group matching mail by sender and count it.

    Returns a dict with the total, whether the scan hit its ceiling, and a
    list of senders ordered by how much each one sent.

    This exists because a count you can read beats a list you cannot. Before
    archiving several hundred messages, the useful question is "who are these
    from and how many each", not "what are all their subject lines".
    """
    mail = connect()
    try:
        criteria = build_criteria(query, sender, None, after, before,
                                  False, label, has_attachment)
        status, data = mail.uid("SEARCH", None, criteria)

        if status != "OK" or not data[0]:
            return {"total": 0, "truncated": False, "senders": []}

        ids = data[0].split()
        total = len(ids)

        # Newest first, so a truncated scan shows recent senders rather than
        # whatever happens to be oldest.
        ids = list(reversed(ids))
        truncated = total > MAX_SCAN
        ids = ids[:MAX_SCAN]

        # One FETCH for the whole set rather than one per message. On a few
        # hundred messages that is the difference between seconds and minutes.
        uid_set = b",".join(ids)
        status, msg_data = mail.uid(
            "FETCH", uid_set, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
        )
        if status != "OK":
            return {"total": total, "truncated": truncated, "senders": []}

        counts = Counter()
        names = {}
        samples = {}

        for item in msg_data:
            if not isinstance(item, tuple) or item[1] is None:
                continue
            raw = item[1].decode("utf-8", errors="replace")
            headers = parse_headers(raw)
            from_field = decode_field(headers.get("From"))
            if not from_field:
                continue

            # Group by address, not display name: the same sender often
            # varies its display name between campaigns.
            display, address = parseaddr(from_field)
            address = address.lower() or from_field.lower()
            counts[address] += 1
            if address not in names and display:
                names[address] = display

            # Keep a few real subjects per address. Two addresses on the
            # same domain might be one sender split across subdomains, or
            # genuinely different streams - subjects settle it, and they
            # come free in the fetch we are already doing.
            subject = decode_field(headers.get("Subject"))
            if subject:
                bucket = samples.setdefault(address, [])
                if len(bucket) < 3 and subject not in bucket:
                    bucket.append(subject)

        # Addresses sharing a root domain get flagged as related, so a
        # preview cannot claim a sender is dealt with while a sibling
        # address is left untouched.
        by_root = {}
        for addr in counts:
            by_root.setdefault(root_domain(addr), []).append(addr)

        senders = []
        for addr, n in counts.most_common():
            root = root_domain(addr)
            siblings = [a for a in by_root[root] if a != addr]
            senders.append({
                "address": addr,
                "name": names.get(addr, ""),
                "count": n,
                "domain": root,
                "related_addresses": siblings,
                "sample_subjects": samples.get(addr, []),
            })

        return {"total": total, "truncated": truncated, "senders": senders}
    finally:
        mail.logout()


# Bulk senders reuse a handful of subject templates with the variable bits
# swapped in. Blanking those bits out lets near-identical subjects collapse
# into one group, which is what turns 331 messages into nine readable rows.
SUBJECT_NOISE = [
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), " "),
    # Month names, with or without a day attached: "Mar 2026" and
    # "March 14, 2026" both need to vanish so monthly mail collapses.
    (re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                r"(?:uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\.?"
                r"(?:\s+\d{1,2})?(?:,?\s*\d{4})?\b", re.I), " "),
    (re.compile(r"\b(?:ksh|kes|usd|eur|gbp)\s*[\d,.]+", re.I), " "),
    (re.compile(r"[#№]?\d[\d,.\-]*"), " "),
    (re.compile(r"\s+"), " "),
]


def subject_shape(subject):
    """Reduce a subject to its template by blanking the variable parts."""
    text = subject.lower()
    for pattern, replacement in SUBJECT_NOISE:
        text = pattern.sub(replacement, text)
    return text.strip(" -\u2013\u2014:|.")


def summarise_subjects(sender=None, query=None, after=None, before=None,
                       label=None, min_count=1):
    """Group one sender's mail by subject template.

    Returns the distinct kinds of mail rather than the individual messages,
    so a sender with hundreds of emails becomes a short list of patterns
    with counts and a real example of each.

    This is the unit that actually matters for bulk filing: a bank sends
    statements, promotions and action-required notices from one address,
    and those should not share a fate.
    """
    mail = connect()
    try:
        criteria = build_criteria(query, sender, None, after, before,
                                  False, label, False)
        status, data = mail.uid("SEARCH", None, criteria)

        if status != "OK" or not data[0]:
            return {"total": 0, "truncated": False, "patterns": []}

        ids = data[0].split()
        total = len(ids)
        ids = list(reversed(ids))
        truncated = total > MAX_SCAN
        ids = ids[:MAX_SCAN]

        status, msg_data = mail.uid(
            "FETCH", b",".join(ids),
            "(BODY.PEEK[HEADER.FIELDS (SUBJECT DATE)])"
        )
        if status != "OK":
            return {"total": total, "truncated": truncated, "patterns": []}

        groups = {}
        for item in msg_data:
            if not isinstance(item, tuple) or item[1] is None:
                continue
            headers = parse_headers(item[1].decode("utf-8", errors="replace"))
            subject = decode_field(headers.get("Subject")).strip()
            if not subject:
                subject = "(no subject)"

            shape = subject_shape(subject) or subject.lower()
            entry = groups.setdefault(shape, {"count": 0, "examples": []})
            entry["count"] += 1
            # Keep real subjects, not the blanked version - the example is
            # what makes the row legible.
            if len(entry["examples"]) < 2 and subject not in entry["examples"]:
                entry["examples"].append(subject)

        patterns = sorted(
            (
                {
                    "count": v["count"],
                    "example": v["examples"][0],
                    "also_seen": v["examples"][1:],
                }
                for v in groups.values()
                if v["count"] >= min_count
            ),
            key=lambda p: p["count"],
            reverse=True,
        )

        return {"total": total, "truncated": truncated, "patterns": patterns}
    finally:
        mail.logout()


def scan_inbox(limit=MAX_SCAN, label=None, after=None, before=None):
    """One pass over the mailbox, grouped by sender and subject pattern.

    Returns {address: {name, count, domain, patterns}} where patterns is the
    same shape summarise_subjects produces.

    Doing this per-sender would mean a separate IMAP search and fetch for
    every address. One scan over the whole set and local grouping is a
    single round trip instead of dozens.
    """
    mail = connect()
    try:
        criteria = build_criteria(None, None, None, after, before,
                                  False, label, False)
        status, data = mail.uid("SEARCH", None, criteria)
        if status != "OK" or not data[0]:
            return {"total": 0, "truncated": False, "senders": {}}

        ids = data[0].split()
        total = len(ids)
        ids = list(reversed(ids))
        truncated = total > limit
        ids = ids[:limit]

        status, msg_data = mail.uid(
            "FETCH", b",".join(ids),
            "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])"
        )
        if status != "OK":
            return {"total": total, "truncated": truncated, "senders": {}}

        senders = {}
        for item in msg_data:
            if not isinstance(item, tuple) or item[1] is None:
                continue
            headers = parse_headers(item[1].decode("utf-8", errors="replace"))

            from_field = decode_field(headers.get("From"))
            if not from_field:
                continue
            display, address = parseaddr(from_field)
            address = (address or from_field).lower()

            subject = decode_field(headers.get("Subject")).strip() or "(no subject)"
            shape = subject_shape(subject) or subject.lower()

            entry = senders.setdefault(address, {
                "name": display or "",
                "domain": root_domain(address),
                "count": 0,
                "_shapes": {},
            })
            entry["count"] += 1
            if display and not entry["name"]:
                entry["name"] = display

            group = entry["_shapes"].setdefault(shape, {"count": 0, "example": subject})
            group["count"] += 1

        # Flatten the shape buckets into sorted pattern lists.
        for entry in senders.values():
            entry["patterns"] = sorted(
                ({"count": g["count"], "example": g["example"], "also_seen": []}
                 for g in entry.pop("_shapes").values()),
                key=lambda p: p["count"],
                reverse=True,
            )

        return {"total": total, "truncated": truncated, "senders": senders}
    finally:
        mail.logout()


# Every batch is filed under this parent, so one click in Gmail selects
# everything the agent has ever archived - and one more deletes it.
BULK_PARENT = "Bulk"


def safe_label_name(category=None, sender_name=None):
    """The label every archived batch gets.

    One label, not one per category. The point of labelling was to be able
    to delete a batch in one action; a label per sender-category would mean
    twenty-five separate clean-ups instead of one. Anything narrower is a
    Gmail search away - label:Bulk from:udemy - so the finer grouping costs
    nothing to give up.

    The arguments are ignored. They are kept so callers that pass a
    category and sender keep working.
    """
    return BULK_PARENT



def find_uids_for_patterns(sender, examples, after=None, before=None):
    """Find the messages matching a sender and a set of subject patterns.

    The patterns are shapes computed locally, so IMAP cannot search for
    them. This searches by sender, then filters on the shaped subject -
    which is the same comparison the preview used, so what gets acted on
    is what was shown.
    """
    wanted = {subject_shape(e) or e.lower() for e in examples}

    mail = connect()
    try:
        criteria = build_criteria(None, sender, None, after, before,
                                  False, None, False)
        status, data = mail.uid("SEARCH", None, criteria)
        if status != "OK" or not data[0]:
            return []

        ids = data[0].split()
        status, msg_data = mail.uid(
            "FETCH", b",".join(ids), "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
        )
        if status != "OK":
            return []

        matched = []
        # FETCH responses interleave the uid line with the header payload,
        # so pull the uid out of the response rather than assuming order.
        for item in msg_data:
            if not isinstance(item, tuple) or item[1] is None:
                continue
            marker = item[0].decode("utf-8", errors="replace")
            uid_match = re.search(r"UID (\d+)", marker)
            if not uid_match:
                continue
            headers = parse_headers(item[1].decode("utf-8", errors="replace"))
            subject = decode_field(headers.get("Subject")).strip() or "(no subject)"
            shape = subject_shape(subject) or subject.lower()
            if shape in wanted:
                matched.append(uid_match.group(1))

        return matched
    finally:
        mail.logout()


def archive_batch(uids, label, dry_run=True):
    """Label a set of messages and take them out of the inbox.

    Nothing is deleted. Removing the \\Inbox label is exactly what Gmail's
    own Archive button does: the mail stays in All Mail, stays searchable,
    and now carries a label so the whole batch can be found - or deleted -
    in one go from Gmail itself.

    dry_run defaults to True. A caller has to ask for the write explicitly.
    """
    if not uids:
        return {"label": label, "count": 0, "applied": False}

    if dry_run:
        return {"label": label, "count": len(uids), "applied": False,
                "dry_run": True}

    mail = connect()
    try:
        # Create the label first; Gmail builds the parent automatically.
        # An existing label makes this fail harmlessly, so the error is
        # swallowed rather than treated as a problem.
        try:
            mail.create(f'"{label}"')
        except imaplib.IMAP4.error:
            pass

        uid_set = ",".join(uids)
        status, _ = mail.uid("STORE", uid_set, "+X-GM-LABELS", f'"{label}"')
        if status != "OK":
            return {"label": label, "count": len(uids), "applied": False,
                    "error": "could not apply the label"}

        status, _ = mail.uid("STORE", uid_set, "-X-GM-LABELS", '\\Inbox')
        if status != "OK":
            return {"label": label, "count": len(uids), "applied": False,
                    "error": "labelled, but could not remove from inbox"}

        return {"label": label, "count": len(uids), "applied": True}
    finally:
        mail.logout()


def consolidate_bulk_labels(dry_run=True):
    """Fold existing Bulk/<something> labels into the single Bulk label.

    For each sub-label: apply Bulk to everything under it, remove the
    sub-label from those messages, then delete the now-empty sub-label.
    Deleting a Gmail label does not delete mail - the messages stay in All
    Mail and keep the Bulk label.
    """
    mail = connect()
    try:
        status, data = mail.list()
        if status != "OK":
            return {"moved": 0, "labels": []}

        prefix = BULK_PARENT + "/"
        sub_labels = []
        for line in data:
            if line is None:
                continue
            decoded = line.decode("utf-8", errors="replace")
            parts = decoded.split(' "/" ')
            if len(parts) != 2:
                continue
            name = parts[1].strip().strip('"')
            if name.startswith(prefix):
                sub_labels.append(name)

        if not sub_labels:
            return {"moved": 0, "labels": []}

        if dry_run:
            return {"moved": 0, "labels": sub_labels, "dry_run": True}

        try:
            mail.create(f'"{BULK_PARENT}"')
        except imaplib.IMAP4.error:
            pass

        moved = 0
        done = []
        for name in sub_labels:
            status, _ = mail.select(f'"{name}"')
            if status != "OK":
                continue

            status, data = mail.uid("SEARCH", None, "ALL")
            if status == "OK" and data[0]:
                uids = ",".join(u.decode() for u in data[0].split())
                mail.uid("STORE", uids, "+X-GM-LABELS", f'"{BULK_PARENT}"')
                mail.uid("STORE", uids, "-X-GM-LABELS", f'"{name}"')
                moved += len(data[0].split())

            # Reselect elsewhere before deleting the mailbox we are in.
            mail.select(ALL_MAIL)
            try:
                mail.delete(f'"{name}"')
                done.append(name)
            except imaplib.IMAP4.error:
                pass

        return {"moved": moved, "labels": done}
    finally:
        mail.logout()


def read_categories(cache_path="inbox_categories.json"):
    """Return the cached inbox breakdown, or None if it has not been built.

    Reading the cache rather than recomputing keeps this cheap enough to be
    a tool the agent can call freely, and means the agent describes exactly
    the same grouping that the approval step would act on.
    """
    import json
    import os

    if not os.path.exists(cache_path):
        return None

    with open(cache_path) as handle:
        data = json.load(handle)

    senders = []
    for sender in data.get("senders", []):
        senders.append({
            "name": sender.get("name") or sender["address"],
            "address": sender["address"],
            "count": sender["count"],
            "categories": [
                {
                    "name": c["name"],
                    "count": c["count"],
                    "needs_attention": c["needs_attention"],
                }
                for c in sender.get("categories", [])
            ],
        })

    senders.sort(key=lambda s: s["count"], reverse=True)

    tail = data.get("tail", [])
    return {
        "built_at": data.get("built_at"),
        "scanned": data.get("scanned"),
        "total": data.get("total"),
        "senders": senders,
        "occasional_senders": len(tail),
        "occasional_messages": sum(t["count"] for t in tail),
    }


if __name__ == "__main__":
    import sys

    # Pass an address to break that one sender down by subject pattern.
    if len(sys.argv) > 1:
        who = sys.argv[1]
        result = summarise_subjects(sender=who)
        print(f"--- {who}: {result['total']} messages, "
              f"{len(result['patterns'])} subject patterns ---")
        for p in result["patterns"]:
            print(f"  {p['count']:>4}  {p['example'][:72]}")
            for alt in p["also_seen"]:
                print(f"        e.g. {alt[:66]}")
        sys.exit()

    print("--- Who fills the inbox? ---")
    summary = summarise_senders()
    print(f"{summary['total']} messages in the inbox"
          + (f" (scanned the newest {MAX_SCAN})" if summary["truncated"] else ""))
    for s in summary["senders"][:15]:
        label_text = s["name"] or s["address"]
        print(f"  {s['count']:>5}  {label_text[:38]:<38} {s['address'][:38]}")
        if s["related_addresses"]:
            print(f"         also on {s['domain']}: "
                  + ", ".join(s["related_addresses"]))
        for subj in s["sample_subjects"][:2]:
            print(f"           - {subj[:68]}")

    print()
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
