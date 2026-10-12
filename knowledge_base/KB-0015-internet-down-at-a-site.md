---
id: KB-0015
title: "Internet down at a site"
type: article
topic_ids: [network.internet_down]
author: "Infrastructure team"
created: 2025-11-03
updated: 2026-06-15
status: current
superseded_by: null
synthetic: true
---
# Internet down at a site

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## When to use this article

Use it when a whole office or site has lost its internet connection. This replaces the earlier version of this article (KB-0026), which told customers to report the problem by email and to restart the router themselves. Both instructions have changed.

## How the customer should report it

A site with no internet is a P1 if the whole business is affected, or a P2 if a smaller part of it is. P1 and P2 problems are reported **by phone**, not by email or the portal: an email sent from a site with no internet may never arrive. Out of hours, P1 calls go to the on-call engineer.

## Ask the customer not to restart the router

Do not ask the customer to switch the router off and on. Our managed routers keep logs that help the carrier find the fault, and restarting them clears those logs. Many sites also have a backup mobile connection that takes over automatically; restarting the router can interrupt the switch to it.

## Steps for the service desk

1. Log the ticket as P1 or P2 and note the time the customer noticed the problem.
2. Check the router's status in the monitoring console. If the router is unreachable, check whether the backup mobile connection has taken over.
3. If the router is reachable but the line is down, raise a fault with the carrier and give the customer the carrier's reference.
4. If the router is locked up and the out-of-band connection is available, restart it remotely after saving its logs.
5. Phone the customer back with an update, and again when the service returns.

## Escalate when

Any P1 still open 2 hours after it was opened is reported to the operations director. If the carrier cannot give a repair time, escalate to the infrastructure team so they can arrange a temporary connection.

## Related

- Office network and Wi-Fi basics (KB-0041)
- Support hours and contact routes (KB-0046)
- Escalation policy (KB-0047)
