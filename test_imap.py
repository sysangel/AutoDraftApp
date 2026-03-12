from dotenv import load_dotenv
import os
import imaplib

load_dotenv()

host = os.getenv('IMAP_HOST')
port = int(os.getenv('IMAP_PORT', 993))
username = os.getenv('IMAP_USERNAME')
password = os.getenv('MAILBOX_PASSWORD')

print(f"Connecting to {host}:{port} as {username}...")

conn = imaplib.IMAP4_SSL(host, port)
print("Connected.")

result = conn.login(username, password)
print("Login result:", result)

conn.select("INBOX")
status, data = conn.uid("SEARCH", None, "UNSEEN")
print("Unread UIDs:", data)

conn.logout()
print("Done.")