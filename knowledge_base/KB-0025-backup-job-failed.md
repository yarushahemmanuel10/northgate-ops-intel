---
id: KB-0025
title: "Backup job failed"
type: article
topic_ids: [backup.job_failed]
author: "Infrastructure team"
created: 2025-05-01
updated: 2026-03-23
status: current
superseded_by: null
synthetic: true
---
# Backup job failed

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## When to use this article

Use it when the backup report shows a failed or partially failed backup for a customer server or cloud service.

## Why it matters

Nightly backups are only useful if they succeed every night. A failure that goes unnoticed for several nights leaves a gap in what can be restored, and backups are kept for 30 days, so repeated failures shorten the period that can actually be recovered.

## Steps

1. Read the error in the backup report. Most failures fall into a few groups: the backup target is full, the backup agent's credentials have expired, the server could not be reached, or files were locked.
2. **Target full:** check the retention policy is applied and old copies are being removed. Free space and re-run the job.
3. **Credentials expired:** renew the credentials used by the backup agent and re-run the job.
4. **Server unreachable:** check the server is on and the network path is working.
5. **Locked files:** check whether an application was still running during the backup window and adjust the schedule if needed.
6. Re-run the job and confirm it completes successfully.

## Priority

One failed night is a P3. Raise it to P2 if the job has failed more than once in a row or if the customer has an urgent restore pending.

## Related

- Backup and retention policy (KB-0049)
- Restoring deleted files (KB-0024)
