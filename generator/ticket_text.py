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

# Each topic has "cases": a description and the resolutions that fit that description.
# priorities: which priorities this topic can plausibly have.
def _t(category, title, priorities, *cases):
    return {"category": category, "title": title, "priorities": priorities,
            "cases": [{"description": d, "resolutions": list(r)} for d, r in cases]}


TOPICS: dict[str, dict] = {
    # --- Email ---------------------------------------------------------------
    "email.mailbox_full": _t("Email", "Mailbox full or over quota", ["P3", "P4"],
        ("User is getting mailbox full warnings and can't send emails.",
         ["Archived old items to an online archive and emptied deleted items. Mailbox now under quota.",
          "Increased mailbox quota in the admin centre and set up auto-archive."]),
        ("Outlook says the mailbox is over its size limit. Emails bouncing back.",
         ["Archived old items to an online archive and emptied deleted items. Mailbox now under quota.",
          "Increased mailbox quota in the admin centre and set up auto-archive."])),
    "email.outlook_not_opening": _t("Email", "Outlook will not open or keeps crashing", ["P3"],
        ("Outlook won't open on the user's {device}, stuck on loading profile.",
         ["Rebuilt the Outlook profile and re-synced the mailbox."]),
        ("Outlook keeps crashing a few seconds after opening.",
         ["Started Outlook in safe mode, disabled a faulty add-in. Opening normally now.",
          "Repaired the Office installation; Outlook now stays open."])),
    "email.shared_mailbox": _t("Email", "Access to a shared mailbox", ["P4"],
        ("Request to give a user access to the accounts shared mailbox.",
         ["Granted full access and send-as permission; mailbox appeared after Outlook restart."]),
        ("Shared mailbox not showing in Outlook for a user who should have access.",
         ["Added user to the mailbox permissions group and removed a stale cached entry."])),
    "email.not_receiving": _t("Email", "Emails not being received", ["P2", "P3"],
        ("No external emails received since this morning across several users.",
         ["Domain MX record had been changed at the registrar. Restored the correct record."]),
        ("Customer emails not arriving; senders not getting a bounce back.",
         ["Found a mail flow rule sending messages to quarantine. Rule corrected and quarantine released."])),
    # --- Accounts & Access ---------------------------------------------------
    "access.password_reset": _t("Accounts & Access", "Password reset and account lockout", ["P3", "P4"],
        ("User locked out after too many password attempts.",
         ["Unlocked the account and reset the password; user set a new one at sign-in."]),
        ("User has forgotten their password and can't log in to their {device}.",
         ["Verified identity, reset the password and enrolled the user in self-service reset."])),
    "access.new_starter": _t("Accounts & Access", "New starter account setup", ["P4"],
        ("New starter on Monday: needs an account, email and access to the shared drive.",
         ["Created the account, licence and mailbox, and added the standard team groups."]),
        ("Please set up a new user with the same access as their team.",
         ["Account created from the starter template; login details sent to the line manager."])),
    "access.leaver": _t("Accounts & Access", "Leaver account removal", ["P4"],
        ("Staff member leaving on Friday. Please disable their account and forward email.",
         ["Account disabled on the leaving date and email forwarding set up for 30 days."]),
        ("Leaver request: remove access and convert the mailbox to shared.",
         ["Disabled sign-in, removed licences and converted the mailbox to a shared mailbox for the manager."])),
    "access.mfa": _t("Accounts & Access", "Multi-factor authentication problems", ["P3"],
        ("User has a new phone and can't complete the MFA prompt.",
         ["Cleared the old MFA methods and re-registered the authenticator app on the new phone."]),
        ("MFA codes being rejected when signing in.",
         ["Phone clock was wrong, causing code mismatch. Set to automatic time and codes now accepted."])),
    # --- Hardware --------------------------------------------------------------
    "hardware.laptop_wont_boot": _t("Hardware", "Laptop or PC will not start", ["P2", "P3"],
        ("{device} won't power on, no lights at all.",
         ["Faulty power adapter. Replaced from stock and device started normally."]),
        ("{device} stuck on a black screen after the logo.",
         ["Ran startup repair after a failed update; device now boots to Windows."])),
    "hardware.screen_fault": _t("Hardware", "Monitor or screen problems", ["P3", "P4"],
        ("Second monitor not detected on the user's {device}.",
         ["Replaced the faulty display cable and updated the graphics driver."]),
        ("Screen flickering and showing lines.",
         ["Monitor failed; swapped for a spare and logged a warranty claim."])),
    "hardware.new_equipment_setup": _t("Hardware", "Setting up new equipment", ["P4"],
        ("New {device} delivered, needs setting up for a user.",
         ["Enrolled the device, installed standard apps and migrated the user's files."]),
        ("Please set up the new docking station and monitors at {site}.",
         ["Engineer visited, set up the dock and monitors, and tested with the user."])),
    # --- Printing ------------------------------------------------------------------
    "printing.printer_offline": _t("Printing", "Printer offline or not printing", ["P3"],
        ("{printer} shows as offline for everyone.",
         ["Printer had picked up a new IP address. Set a reservation and updated the port."]),
        ("Jobs sent to {printer} sit in the queue and never print.",
         ["Restarted the print spooler and cleared stuck jobs; printing resumed."])),
    "printing.scan_to_email": _t("Printing", "Scan to email not working", ["P3", "P4"],
        ("Scan to email from {printer} has stopped working.",
         ["Updated the copier's SMTP settings to use the authenticated relay.",
          "The scanning account's password had expired. Reset it and updated the copier."]),
        ("Scans not arriving in users' inboxes.",
         ["Updated the copier's SMTP settings to use the authenticated relay.",
          "The scanning account's password had expired. Reset it and updated the copier."])),
    "printing.add_printer": _t("Printing", "Adding a printer to a computer", ["P4"],
        ("User needs {printer} adding to their {device}.",
         ["Deployed the printer via policy and confirmed a test page."]),
        ("Can't see {printer} in the list of printers.",
         ["Installed the driver and added the printer; test page printed."])),
    # --- Network -------------------------------------------------------------------
    "network.internet_down": _t("Network", "Internet down at a site", ["P1", "P2"],
        ("No internet at {site} for all users.",
         ["Router had locked up. Rebooted remotely via the out-of-band connection; service restored.",
          "Carrier fault on the line. Failed over to the backup 4G connection and chased the carrier."]),
        ("Whole office has lost internet connection.",
         ["Router had locked up. Rebooted remotely via the out-of-band connection; service restored.",
          "Carrier fault on the line. Failed over to the backup 4G connection and chased the carrier."])),
    "network.wifi_slow": _t("Network", "Slow or dropping Wi-Fi", ["P3"],
        ("Wi-Fi very slow at {site}.",
         ["Moved access points to less congested channels and updated firmware."]),
        ("Wi-Fi keeps dropping out on users' {device}s.",
         ["One access point had failed. Replaced it and coverage is back to normal."])),
    "network.vpn": _t("Network", "Remote access VPN not connecting", ["P2", "P3"],
        ("User working from home can't connect to the VPN.",
         ["VPN client was out of date. Updated it and the connection works."]),
        ("VPN connects but can't reach the shared drive.",
         ["Added the missing route to the VPN profile; shared drive reachable."])),
    # --- Software -------------------------------------------------------------------
    "software.install_request": _t("Software", "Software installation request", ["P4"],
        ("Please install {app} on a user's {device}.",
         ["Approved by the manager and installed from the software portal."]),
        ("User needs {app} for a new project.",
         ["Licence assigned and {app} installed remotely."])),
    "software.app_crashing": _t("Software", "Application crashing or freezing", ["P2", "P3"],
        ("{app} keeps freezing when saving files.",
         ["Repaired the installation and cleared the local cache."]),
        ("{app} closes with an error as soon as it opens.",
         ["Applied the vendor's latest patch, which fixed the crash."])),
    "software.windows_update": _t("Software", "Windows updates failing", ["P3", "P4"],
        ("Windows updates keep failing on the user's {device}.",
         ["Reset the update components and installed the pending updates."]),
        ("{device} asks to restart for updates every day and never finishes.",
         ["Freed disk space and installed the cumulative update manually."])),
    # --- Security ----------------------------------------------------------------------
    "security.phishing_report": _t("Security", "Suspicious or phishing email", ["P2", "P3"],
        ("User received an email asking them to confirm their password. Looks like phishing.",
         ["Confirmed phishing; removed the message from all mailboxes and blocked the sender.",
          "No credentials entered. Purged the emails and reminded staff of the reporting process."]),
        ("Several staff got a fake invoice email with a link.",
         ["Confirmed phishing; removed the message from all mailboxes and blocked the sender.",
          "No credentials entered. Purged the emails and reminded staff of the reporting process."])),
    "security.compromised_account": _t("Security", "Suspected compromised account", ["P1", "P2"],
        ("Customers report receiving strange emails from one of the user's accounts.",
         ["Revoked sessions, reset the password, removed a malicious inbox rule and enforced MFA."]),
        ("Sign-in alerts from another country for a user account.",
         ["Blocked sign-in, reviewed audit logs, reset credentials and confirmed no data was exported."])),
    "security.malware_alert": _t("Security", "Antivirus or malware alert", ["P1", "P2", "P3"],
        ("Antivirus alert on a user's {device}.",
         ["Isolated the device, ran a full scan and removed the threat. No spread found."]),
        ("Endpoint protection flagged a suspicious file in downloads.",
         ["Isolated the device, ran a full scan and removed the threat. No spread found.",
          "False positive confirmed with the vendor; file restored and exclusion added."])),
    # --- Backup & Restore -----------------------------------------------------------------
    "backup.file_restore": _t("Backup & Restore", "Restoring deleted files", ["P3", "P4"],
        ("User deleted a folder on the shared drive by mistake. Needs it back.",
         ["Restored the folder from last night's backup to its original location."]),
        ("Please restore yesterday's version of a spreadsheet.",
         ["Recovered the previous version from version history."])),
    "backup.job_failed": _t("Backup & Restore", "Backup job failed", ["P2", "P3"],
        ("Nightly backup report shows the server backup failed.",
         ["Backup target was full. Applied retention policy and re-ran the job successfully.",
          "Renewed expired credentials used by the backup agent; job completed."]),
        ("Backup job has failed three nights in a row.",
         ["Backup target was full. Applied retention policy and re-ran the job successfully.",
          "Renewed expired credentials used by the backup agent; job completed."])),
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
    case = topic["cases"][int(rng.integers(len(topic["cases"])))]
    res = case["resolutions"][int(rng.integers(len(case["resolutions"])))]
    return fill(case["description"], slots), fill(res, slots)


def allowed_pairs(topic_id: str) -> set[tuple[str, str]]:
    """Every (description template, resolution template) pair allowed for a topic."""
    return {(c["description"], r) for c in TOPICS[topic_id]["cases"] for r in c["resolutions"]}
