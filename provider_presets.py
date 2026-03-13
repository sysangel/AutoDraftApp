"""
Shared provider presets and category defaults for setup/configuration UI.
"""

PROVIDER_PRESETS = {
    "custom": {
        "label": "Custom",
        "imap_host": "",
        "imap_port": "993",
        "drafts_folder": "Drafts",
    },
    "bluehost": {
        "label": "Bluehost",
        "imap_host": "mail.yourdomain.com",
        "imap_port": "993",
        "drafts_folder": "Drafts",
    },
    "gmail": {
        "label": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": "993",
        "drafts_folder": "[Gmail]/Drafts",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "imap_host": "outlook.office365.com",
        "imap_port": "993",
        "drafts_folder": "Drafts",
    },
    "yahoo": {
        "label": "Yahoo",
        "imap_host": "imap.mail.yahoo.com",
        "imap_port": "993",
        "drafts_folder": "Draft",
    },
    "zoho": {
        "label": "Zoho",
        "imap_host": "imappro.zoho.com",
        "imap_port": "993",
        "drafts_folder": "INBOX.Drafts",
    },
}


CATEGORY_OPTIONS = [
    "general",
    "client",
    "sales",
    "support",
    "scheduling",
    "billing",
]
