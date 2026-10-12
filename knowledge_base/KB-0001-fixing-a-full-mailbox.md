---
id: KB-0001
title: "Fixing a full mailbox"
type: article
topic_ids: [email.mailbox_full]
author: "Service desk (first line)"
created: 2025-04-07
updated: 2026-02-16
status: current
superseded_by: null
synthetic: true
---
# Fixing a full mailbox

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

The user sees a warning in Outlook that their mailbox is full or almost full. Depending on how far over the limit the mailbox is, they may still receive email but be unable to send, or they may stop receiving email altogether. Senders outside the business sometimes receive a bounce message saying the recipient's mailbox is full. On a phone, the mail app may simply stop syncing new messages without showing an obvious error.

## Why it happens

Every mailbox has a storage limit set in the email admin centre. Large attachments, years of sent items and a Deleted Items folder that is never emptied are the usual causes. Shared calendars and meeting invitations with attachments also count towards the limit, which surprises many users.

## Before you start

- Confirm which mailbox is full. Users with access to shared mailboxes sometimes see the warning for the shared mailbox, not their own.
- Check whether the user has an online archive enabled. If they do, the quickest fix is usually to move old mail into it.
- Ask whether the user keeps email locally in a data file on their computer. Do not delete anything stored that way without their agreement.

## Steps

1. In Outlook, open the mailbox properties and check the current size against the limit.
2. Ask the user to empty the Deleted Items folder and the Junk Email folder. This alone often frees enough space.
3. Sort the Sent Items folder by size and help the user remove or archive large messages they no longer need.
4. If an online archive is available, set up an archive policy so older items move there automatically. Explain to the user where the archive appears in the folder list.
5. If the user genuinely needs more space and their manager agrees, raise the quota in the admin centre and record the change on the ticket.
6. Ask the user to send a test message to confirm sending works again.

## If that does not work

If the size does not go down after deleting items, the mailbox may still be holding deleted items for recovery. Wait for the next sync and check again, or ask an engineer to review the recoverable items folder. If the mailbox belongs to someone who has left, follow the leaver article instead of raising the quota.

## Escalate when

Escalate to an engineer if the mailbox has stopped receiving business-critical email and cannot be cleared quickly, or if several mailboxes report the same problem at the same time, which may point to a wider issue.

## Related

- Microsoft account basics (KB-0035)
- Leaver account removal (KB-0007)
