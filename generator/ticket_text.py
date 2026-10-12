"""Fixed ticket text templates, one entry per support topic.

Topics are the unit the knowledge base will be written against in step 1.8: every topic
here should have at least one KB article, so `topic_id` (kept in the ground truth) is
what links a ticket to its article. Text is written by hand; no LLM is used.

Slots in braces are filled from SLOTS with the seeded generator.
"""
from __future__ import annotations

import numpy as np

SLOTS = {
    "device": ["laptop", "desktop PC", "work laptop", "PC"],
    "app": ["the accounts package", "the CAD software", "the booking system", "Adobe Acrobat",
            "the stock control app", "Teams"],
    "site": ["the main office", "the warehouse", "the upstairs office", "reception", "the second site"],
    "printer": ["the office printer", "the reception printer", "the label printer", "the main copier"],
}

# priorities: which priorities this topic can plausibly have.
TOPICS: dict[str, dict] = {
    # --- Email ---------------------------------------------------------------
    "email.mailbox_full": {
        "category": "Email", "title": "Mailbox full or over quota", "priorities": ["P3", "P4"],
        "descriptions": ["User is getting mailbox full warnings and can't send emails.",
                         "Outlook says the mailbox is over its size limit. Emails bouncing back."],
        "resolutions": ["Archived old items to an online archive and emptied deleted items. Mailbox now under quota.",
                        "Increased mailbox quota in the admin centre and set up auto-archive."],
    },
    "email.outlook_not_opening": {
        "category": "Email", "title": "Outlook will not open or keeps crashing", "priorities": ["P3"],
        "descriptions": ["Outlook won't open on the user's {device}, stuck on loading profile.",
                         "Outlook keeps crashing a few seconds after opening."],
        "resolutions": ["Started Outlook in safe mode, disabled a faulty add-in. Opening normally now.",
                        "Rebuilt the Outlook profile and re-synced the mailbox."],
    },
    "email.shared_mailbox": {
        "category": "Email", "title": "Access to a shared mailbox", "priorities": ["P4"],
        "descriptions": ["Request to give a user access to the accounts shared mailbox.",
                         "Shared mailbox not showing in Outlook for a user who should have access."],
        "resolutions": ["Granted full access and send-as permission; mailbox appeared after Outlook restart.",
                        "Added user to the mailbox permissions group and removed a stale cached entry."],
    },
    "email.not_receiving": {
        "category": "Email", "title": "Emails not being received", "priorities": ["P2", "P3"],
        "descriptions": ["No external emails received since this morning across several users.",
                         "Customer emails not arriving; senders not getting a bounce back."],
        "resolutions": ["Found a mail flow rule sending messages to quarantine. Rule corrected and quarantine released.",
                        "Domain MX record had been changed at the registrar. Restored the correct record."],
    },
    # --- Accounts & Access ---------------------------------------------------
    "access.password_reset": {
        "category": "Accounts & Access", "title": "Password reset and account lockout", "priorities": ["P3", "P4"],
        "descriptions": ["User locked out after too many password attempts.",
                         "User has forgotten their password and can't log in to their {device}."],
        "resolutions": ["Unlocked the account and reset the password; user set a new one at sign-in.",
                        "Verified identity, reset the password and enrolled the user in self-service reset."],
    },
    "access.new_starter": {
        "category": "Accounts & Access", "title": "New starter account setup", "priorities": ["P4"],
        "descriptions": ["New starter on Monday: needs an account, email and access to the shared drive.",
                         "Please set up a new user with the same access as their team."],
        "resolutions": ["Created the account, licence and mailbox, and added the standard team groups.",
                        "Account created from the starter template; login details sent to the line manager."],
    },
    "access.leaver": {
        "category": "Accounts & Access", "title": "Leaver account removal", "priorities": ["P4"],
        "descriptions": ["Staff member leaving on Friday. Please disable their account and forward email.",
                         "Leaver request: remove access and convert the mailbox to shared."],
        "resolutions": ["Disabled sign-in, removed licences and converted the mailbox to a shared mailbox for the manager.",
                        "Account disabled on the leaving date and email forwarding set up for 30 days."],
    },
    "access.mfa": {
        "category": "Accounts & Access", "title": "Multi-factor authentication problems", "priorities": ["P3"],
        "descriptions": ["User has a new phone and can't complete the MFA prompt.",
                         "MFA codes being rejected when signing in."],
        "resolutions": ["Cleared the old MFA methods and re-registered the authenticator app on the new phone.",
                        "Phone clock was wrong, causing code mismatch. Set to automatic time and codes now accepted."],
    },
    # --- Hardware --------------------------------------------------------------
    "hardware.laptop_wont_boot": {
        "category": "Hardware", "title": "Laptop or PC will not start", "priorities": ["P2", "P3"],
        "descriptions": ["{device} won't power on, no lights at all.",
                         "{device} stuck on a black screen after the logo."],
        "resolutions": ["Faulty power adapter. Replaced from stock and device started normally.",
                        "Ran startup repair after a failed update; device now boots to Windows."],
    },
    "hardware.screen_fault": {
        "category": "Hardware", "title": "Monitor or screen problems", "priorities": ["P3", "P4"],
        "descriptions": ["Second monitor not detected on the user's {device}.",
                         "Screen flickering and showing lines."],
        "resolutions": ["Replaced the faulty display cable and updated the graphics driver.",
                        "Monitor failed; swapped for a spare and logged a warranty claim."],
    },
    "hardware.new_equipment_setup": {
        "category": "Hardware", "title": "Setting up new equipment", "priorities": ["P4"],
        "descriptions": ["New {device} delivered, needs setting up for a user.",
                         "Please set up the new docking station and monitors at {site}."],
        "resolutions": ["Enrolled the device, installed standard apps and migrated the user's files.",
                        "Engineer visited, set up the dock and monitors, and tested with the user."],
    },
    # --- Printing ------------------------------------------------------------------
    "printing.printer_offline": {
        "category": "Printing", "title": "Printer offline or not printing", "priorities": ["P3"],
        "descriptions": ["{printer} shows as offline for everyone.",
                         "Jobs sent to {printer} sit in the queue and never print."],
        "resolutions": ["Printer had picked up a new IP address. Set a reservation and updated the port.",
                        "Restarted the print spooler and cleared stuck jobs; printing resumed."],
    },
    "printing.scan_to_email": {
        "category": "Printing", "title": "Scan to email not working", "priorities": ["P3", "P4"],
        "descriptions": ["Scan to email from {printer} has stopped working.",
                         "Scans not arriving in users' inboxes."],
        "resolutions": ["Updated the copier's SMTP settings to use the authenticated relay.",
                        "The scanning account's password had expired. Reset it and updated the copier."],
    },
    "printing.add_printer": {
        "category": "Printing", "title": "Adding a printer to a computer", "priorities": ["P4"],
        "descriptions": ["User needs {printer} adding to their {device}.",
                         "Can't see {printer} in the list of printers."],
        "resolutions": ["Deployed the printer via policy and confirmed a test page.",
                        "Installed the driver and added the printer; test page printed."],
    },
    # --- Network -------------------------------------------------------------------
    "network.internet_down": {
        "category": "Network", "title": "Internet down at a site", "priorities": ["P1", "P2"],
        "descriptions": ["No internet at {site} for all users.",
                         "Whole office has lost internet connection."],
        "resolutions": ["Router had locked up. Rebooted remotely via the out-of-band connection; service restored.",
                        "Carrier fault on the line. Failed over to the backup 4G connection and chased the carrier."],
    },
    "network.wifi_slow": {
        "category": "Network", "title": "Slow or dropping Wi-Fi", "priorities": ["P3"],
        "descriptions": ["Wi-Fi very slow at {site}.",
                         "Wi-Fi keeps dropping out on users' {device}s."],
        "resolutions": ["Moved access points to less congested channels and updated firmware.",
                        "One access point had failed. Replaced it and coverage is back to normal."],
    },
    "network.vpn": {
        "category": "Network", "title": "Remote access VPN not connecting", "priorities": ["P2", "P3"],
        "descriptions": ["User working from home can't connect to the VPN.",
                         "VPN connects but can't reach the shared drive."],
        "resolutions": ["VPN client was out of date. Updated it and the connection works.",
                        "Added the missing route to the VPN profile; shared drive reachable."],
    },
    # --- Software -------------------------------------------------------------------
    "software.install_request": {
        "category": "Software", "title": "Software installation request", "priorities": ["P4"],
        "descriptions": ["Please install {app} on a user's {device}.",
                         "User needs {app} for a new project."],
        "resolutions": ["Approved by the manager and installed from the software portal.",
                        "Licence assigned and {app} installed remotely."],
    },
    "software.app_crashing": {
        "category": "Software", "title": "Application crashing or freezing", "priorities": ["P2", "P3"],
        "descriptions": ["{app} keeps freezing when saving files.",
                         "{app} closes with an error as soon as it opens."],
        "resolutions": ["Repaired the installation and cleared the local cache.",
                        "Applied the vendor's latest patch, which fixed the crash."],
    },
    "software.windows_update": {
        "category": "Software", "title": "Windows updates failing", "priorities": ["P3", "P4"],
        "descriptions": ["Windows updates keep failing on the user's {device}.",
                         "{device} asks to restart for updates every day and never finishes."],
        "resolutions": ["Reset the update components and installed the pending updates.",
                        "Freed disk space and installed the cumulative update manually."],
    },
    # --- Security ----------------------------------------------------------------------
    "security.phishing_report": {
        "category": "Security", "title": "Suspicious or phishing email", "priorities": ["P2", "P3"],
        "descriptions": ["User received an email asking them to confirm their password. Looks like phishing.",
                         "Several staff got a fake invoice email with a link."],
        "resolutions": ["Confirmed phishing; removed the message from all mailboxes and blocked the sender.",
                        "No credentials entered. Purged the emails and reminded staff of the reporting process."],
    },
    "security.compromised_account": {
        "category": "Security", "title": "Suspected compromised account", "priorities": ["P1", "P2"],
        "descriptions": ["Customers report receiving strange emails from one of the user's accounts.",
                         "Sign-in alerts from another country for a user account."],
        "resolutions": ["Revoked sessions, reset the password, removed a malicious inbox rule and enforced MFA.",
                        "Blocked sign-in, reviewed audit logs, reset credentials and confirmed no data was exported."],
    },
    "security.malware_alert": {
        "category": "Security", "title": "Antivirus or malware alert", "priorities": ["P1", "P2", "P3"],
        "descriptions": ["Antivirus alert on a user's {device}.",
                         "Endpoint protection flagged a suspicious file in downloads."],
        "resolutions": ["Isolated the device, ran a full scan and removed the threat. No spread found.",
                        "False positive confirmed with the vendor; file restored and exclusion added."],
    },
    # --- Backup & Restore -----------------------------------------------------------------
    "backup.file_restore": {
        "category": "Backup & Restore", "title": "Restoring deleted files", "priorities": ["P3", "P4"],
        "descriptions": ["User deleted a folder on the shared drive by mistake. Needs it back.",
                         "Please restore yesterday's version of a spreadsheet."],
        "resolutions": ["Restored the folder from last night's backup to its original location.",
                        "Recovered the previous version from version history."],
    },
    "backup.job_failed": {
        "category": "Backup & Restore", "title": "Backup job failed", "priorities": ["P2", "P3"],
        "descriptions": ["Nightly backup report shows the server backup failed.",
                         "Backup job has failed three nights in a row."],
        "resolutions": ["Backup target was full. Applied retention policy and re-ran the job successfully.",
                        "Renewed expired credentials used by the backup agent; job completed."],
    },
}

CATEGORIES = sorted({t["category"] for t in TOPICS.values()})


def _choose_slots(rng: np.random.Generator) -> dict[str, str]:
    """One value per slot for a whole ticket, so description and resolution agree."""
    return {slot: options[int(rng.integers(len(options)))] for slot, options in SLOTS.items()}


def fill(template: str, slots: dict[str, str]) -> str:
    out = template
    for slot, value in slots.items():
        out = out.replace("{" + slot + "}", value)
    return out[0].upper() + out[1:]


def ticket_text(topic_id: str, rng: np.random.Generator) -> tuple[str, str]:
    topic = TOPICS[topic_id]
    slots = _choose_slots(rng)
    desc = topic["descriptions"][int(rng.integers(len(topic["descriptions"])))]
    res = topic["resolutions"][int(rng.integers(len(topic["resolutions"])))]
    return fill(desc, slots), fill(res, slots)
