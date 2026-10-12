---
id: KB-0049
title: "Backup and retention policy"
type: policy
topic_ids: []
author: "Operations director"
created: 2025-04-01
updated: 2025-09-15
status: current
superseded_by: null
synthetic: true
---
# Backup and retention policy

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## What is backed up

For customers who take cloud hosting or managed backup, Northgate backs up:

- customer servers we manage
- shared drives
- the cloud file library and mailboxes, where the customer has the cloud backup service

Files saved only on a user's own laptop are not backed up.

## How often, and for how long

Backups run nightly. 30 daily copies are kept, so data can be restored to the state it was in on any night during that period. Data deleted or changed earlier than that cannot be restored from backup.

## Monitoring

The infrastructure team checks backup reports every working day. A failed backup is logged as a ticket and investigated. Repeated failures are escalated.

## Restores

Restores are requested through the service desk. The requester must be the owner of the data or an authorised contact. Where a restore would overwrite newer work, the data is restored to a separate location so the customer can compare.

## Testing

Restores are tested regularly, by restoring sample files and confirming they open correctly, so that we know backups work before they are needed.

## Related

- Restoring deleted files (KB-0024)
- Backup job failed (KB-0025)
- Saving files so they are backed up (KB-0042)
