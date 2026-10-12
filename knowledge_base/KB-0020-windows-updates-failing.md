---
id: KB-0020
title: "Windows updates failing"
type: article
topic_ids: [software.windows_update]
author: "Service desk (first line)"
created: 2025-05-05
updated: 2026-05-18
status: current
superseded_by: null
synthetic: true
---
# Windows updates failing

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

Windows updates fail to install, or the computer asks to restart for updates every day and never finishes.

## Why it matters

Computers that fall behind on updates miss security fixes. Device management reports show which computers are behind; check the report when you pick up this kind of ticket.

## Steps

1. Check free disk space. Updates need space to download and install, and a nearly full disk is the most common cause of repeated failures. Help the user clear temporary files and move large personal files to the shared drive.
2. Run the Windows Update troubleshooter.
3. Restart the computer and let it finish installing. Ask the user to leave it plugged in.
4. If updates still fail, reset the update components using the standard procedure in our engineering notes, then try again.
5. If one cumulative update keeps failing, download it manually from the official update catalogue and install it.

## Before closing

Check the device management report shows the computer as up to date.

## Related

- Keeping software up to date (KB-0043)
- Laptop or PC will not start (KB-0009)
