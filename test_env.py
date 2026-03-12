from dotenv import load_dotenv
import os

load_dotenv()

print('HOST:', repr(os.getenv('IMAP_HOST')))
print('PORT:', repr(os.getenv('IMAP_PORT')))
print('USER:', repr(os.getenv('IMAP_USERNAME')))
print('PASS:', repr(os.getenv('MAILBOX_PASSWORD')))