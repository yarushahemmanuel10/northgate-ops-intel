---
id: KB-0004
title: "Emails not being received"
type: article
topic_ids: [email.not_receiving]
author: "Infrastructure team"
created: 2025-04-15
updated: 2026-03-09
status: current
superseded_by: null
synthetic: true
---
# Emails not being received

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

Users report that email from outside the business has stopped arriving. Internal email between colleagues often still works. Senders may receive no bounce message at all, which makes the problem easy to miss until a customer phones to ask why nobody has replied.

## First checks

1. Find out whether the problem affects one user, several users or everyone. One user points to a mailbox rule or a full mailbox; everyone points to the domain or mail flow.
2. Ask a colleague to send a test email from a personal address outside the business and note the exact time.
3. Use the message trace in the email admin centre to search for the test message. The trace shows whether the message reached the service and what happened to it.

## Common causes and fixes

**Messages held in quarantine.** A mail flow rule or the spam filter may be sending legitimate messages to quarantine. Review the quarantine, release genuine messages, and correct the rule that caught them. Record the rule change on the ticket.

**Domain records changed.** If the trace shows no sign of the message, check the domain's mail exchange record at the registrar. Records are sometimes changed by mistake when a website is moved or a new service is set up. Restore the correct record and explain to the customer that the change can take time to spread across the internet.

**Inbox rules.** For a single user, check their inbox rules. A rule that moves or deletes messages can look exactly like email not arriving.

**Full mailbox.** If the user's mailbox is full, follow the full mailbox article.

## Priority

If no external email is reaching the business at all, treat it as a P2 at least and phone the customer to keep them updated. A single user missing some messages is usually a P3.

## Escalate when

Escalate to the infrastructure team straight away if domain records need changing, if the registrar account details are not on file, or if you suspect the domain has been tampered with.

## Related

- Fixing a full mailbox (KB-0001)
- Service level agreement policy (KB-0045)
