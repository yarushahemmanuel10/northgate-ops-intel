---
id: KB-0013
title: "Scan to email not working"
type: article
topic_ids: [printing.scan_to_email]
author: "Service desk (first line)"
created: 2025-04-22
updated: 2026-02-02
status: current
superseded_by: null
synthetic: true
---
# Scan to email not working

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

Users press scan to email on the copier but the scan never arrives, or the copier shows a sending error.

## How scan to email works

The copier sends scans through an email account or an authenticated relay set up when the copier was installed. If that account's password expires, or the email service changes its security requirements, scanning stops even though printing still works.

## Steps

1. Ask for the exact error shown on the copier's screen, or the error in the copier's job log.
2. Check whether the scan arrived in the user's junk email folder or in quarantine.
3. Open the copier's web settings page and look at the outgoing email settings.
4. If the copier signs in with a dedicated scanning account, check whether the account's password has expired or the account has been blocked. Reset it if needed and update the password on the copier.
5. If the email service no longer accepts the copier's sign-in method, switch the copier to the customer's authenticated relay. The relay details are in the customer's network documentation.
6. Send a test scan to yourself and to the user.

## Prevention

Record the scanning account on the customer's documentation and note when its password needs changing, so it can be updated before scanning stops.

## Escalate when

Escalate if the copier's firmware does not support the current email security settings. The copier supplier may need to update it.

## Related

- Printer and copier basics (KB-0040)
