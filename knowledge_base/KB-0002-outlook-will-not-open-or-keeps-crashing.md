---
id: KB-0002
title: "Outlook will not open or keeps crashing"
type: article
topic_ids: [email.outlook_not_opening]
author: "Service desk (first line)"
created: 2025-04-08
updated: 2025-12-01
status: current
superseded_by: null
synthetic: true
---
# Outlook will not open or keeps crashing

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

Outlook either never finishes opening, staying on the "Loading profile" screen, or it opens and then closes a few seconds later. Sometimes a message appears saying Outlook cannot start or the set of folders cannot be opened. Webmail usually still works, which tells you the mailbox itself is fine and the problem is on the computer.

## Common causes

- A faulty or out-of-date add-in that loads when Outlook starts.
- A damaged Outlook profile, often after a password change or an interrupted update.
- A damaged installation of the office apps.
- A local data file that is very large or corrupted.

## Steps

1. Ask the user to sign in to webmail. If webmail works, carry on with the steps below. If webmail also fails, treat it as an email delivery or account problem instead.
2. Start Outlook in safe mode. On Windows, hold down the Ctrl key while clicking the Outlook icon and confirm the prompt. Safe mode starts Outlook without add-ins.
3. If Outlook opens in safe mode, go to the add-ins list and switch off add-ins one by one, restarting Outlook normally each time, until you find the one causing the crash. Leave that add-in switched off and note its name on the ticket.
4. If Outlook does not open even in safe mode, create a new Outlook profile from the Mail settings in Control Panel, set it as the default, and let it download the mailbox again. Warn the user that the first sync can take a while on a large mailbox.
5. If a new profile does not help, run the online repair for the office apps from Apps and Features. This reinstalls the apps without touching the user's files.

## After the fix

Ask the user to check that their calendar, signature and any shared mailboxes are present. A new profile does not always bring back the email signature, so help the user restore it if needed.

## Escalate when

Escalate if the online repair fails, if several users on the same site report the same crash after an update, or if the user's data file appears damaged and contains mail that exists nowhere else.

## Related

- Remote support sessions (KB-0032)
