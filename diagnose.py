"""Find out what mailboxes this Gmail account actually exposes over IMAP.

Folder naming varies: most accounts use [Gmail]/All Mail and [Gmail]/Sent
Mail, but some expose plain Sent and Trash instead. A search against a
mailbox that does not exist returns nothing rather than erroring, which
looks identical to "no matching messages".
"""

import imaplib
import os

from dotenv import load_dotenv

load_dotenv()

address = os.getenv("GMAIL_ADDRESS")
password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(address, password)

print("=== Every mailbox on this account ===")
status, data = mail.list()
names = []
for line in data:
    decoded = line.decode("utf-8", errors="replace")
    print(" ", decoded)
    if ' "/" ' in decoded:
        names.append(decoded.split(' "/" ')[1].strip().strip('"'))

print()
print("=== Which of these can be selected, and how many messages ===")
for candidate in names:
    quoted = f'"{candidate}"'
    try:
        status, counts = mail.select(quoted, readonly=True)
        if status == "OK":
            print(f"  OK       {candidate}: {counts[0].decode()} messages")
        else:
            print(f"  FAILED   {candidate}")
    except imaplib.IMAP4.error as exc:
        print(f"  ERROR    {candidate}: {exc}")

print()
print("=== Searching for jootrh across the whole account ===")
# X-GM-RAW passes Gmail's own search syntax, which spans all mail
# regardless of which mailbox is selected.
for mailbox in names:
    try:
        status, _ = mail.select(f'"{mailbox}"', readonly=True)
        if status != "OK":
            continue
        status, data = mail.search(None, 'X-GM-RAW "jootrh"')
        found = len(data[0].split()) if status == "OK" and data[0] else 0
        if found:
            print(f"  {mailbox}: {found} matches")
    except imaplib.IMAP4.error:
        continue

mail.logout()
