import imaplib
import os
import sys
from dotenv import load_dotenv

load_dotenv()

address = os.getenv("GMAIL_ADDRESS")
password = os.getenv("GMAIL_APP_PASSWORD")

# --- Credential checks: catch the common mistakes before we call Gmail ---

if not address:
    sys.exit("GMAIL_ADDRESS is missing from .env")

if not password:
    sys.exit("GMAIL_APP_PASSWORD is missing from .env")

if address != address.strip():
    sys.exit("GMAIL_ADDRESS has leading or trailing whitespace in .env")

if address.startswith(("'", '"')) or address.endswith(("'", '"')):
    sys.exit("GMAIL_ADDRESS has quotes around it in .env - remove them")

if password.startswith(("'", '"')) or password.endswith(("'", '"')):
    sys.exit("GMAIL_APP_PASSWORD has quotes around it in .env - remove them")

# Google shows the app password in four groups of four; the spaces are cosmetic.
cleaned = password.replace(" ", "").strip()

if len(cleaned) != 16:
    sys.exit(
        f"App password should be 16 characters, got {len(cleaned)}. "
        "Check you copied the generated app password, not your account password."
    )

print(f"Connecting as {address}")

# --- Connect ---

try:
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(address, cleaned)
except imaplib.IMAP4.error as e:
    sys.exit(
        f"Gmail rejected the login: {e}\n"
        "Most likely causes: wrong account, app password revoked by a "
        "password change, or app passwords disabled on this account."
    )

status, counts = mail.select("INBOX")
print("Status:", status)
print("Messages in inbox:", counts[0].decode())

mail.logout()
print("Logged out cleanly.")
