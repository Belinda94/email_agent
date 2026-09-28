"""Check whether the edit took, and what the To headers actually contain."""

import imaplib
import os

from dotenv import load_dotenv

import email_tools

load_dotenv()

# 1. Did the edit land?
import inspect
source = inspect.getsource(email_tools.search_emails)
if "or recipient" in source:
    print("Edit: PRESENT - recipient now switches to All Mail")
else:
    print("Edit: MISSING - the mailbox line was not changed or not saved")
print()

# 2. What do the To headers look like on sent mail mentioning jootrh?
address = os.getenv("GMAIL_ADDRESS")
password = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")

mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login(address, password)
mail.select('"[Gmail]/Sent Mail"', readonly=True)

status, data = mail.search(None, 'X-GM-RAW "jootrh"')
ids = data[0].split()
print(f"Sent messages mentioning jootrh anywhere: {len(ids)}")
print("Their To headers:")
for msg_id in ids[-8:]:
    status, msg_data = mail.fetch(
        msg_id, "(BODY.PEEK[HEADER.FIELDS (TO SUBJECT)])"
    )
    raw = msg_data[0][1].decode("utf-8", errors="replace")
    for line in raw.splitlines():
        if line.lower().startswith("to:"):
            print("   ", line.strip()[:90])

print()
# 3. Does a header-only TO search find any of them?
status, data = mail.search(None, 'TO "jootrh"')
count = len(data[0].split()) if status == "OK" and data[0] else 0
print(f'Sent messages where TO header contains "jootrh": {count}')

mail.logout()
