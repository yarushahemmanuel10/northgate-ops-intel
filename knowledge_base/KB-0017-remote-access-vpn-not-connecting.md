---
id: KB-0017
title: "Remote access VPN not connecting"
type: article
topic_ids: [network.vpn]
author: "Infrastructure team"
created: 2025-04-24
updated: 2026-01-26
status: current
superseded_by: null
synthetic: true
---
# Remote access VPN not connecting

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

A user working away from the office cannot connect to the VPN, or the VPN connects but shared drives and internal systems cannot be reached.

## Cannot connect

1. Check the user has a working internet connection by opening a website.
2. Ask for the exact error message from the VPN client.
3. Check whether the VPN client is up to date. Old client versions are often refused by the VPN gateway after it is updated. Update the client and try again.
4. If the error mentions sign-in or authentication, check the user's account is not locked and that multi-factor authentication completes.
5. Some hotel and public networks block VPN traffic. Ask the user to try a different network, such as a phone hotspot, to rule this out.

## Connects but cannot reach the shared drive

1. Check whether the VPN profile includes a route to the network where the file server sits. A missing route is a common cause after network changes.
2. Add the missing route to the VPN profile and ask the user to reconnect.
3. Check that the user is in the security group that allows VPN access to that server.

## Escalate when

Escalate to the infrastructure team if several remote users cannot connect at the same time, which suggests a problem with the VPN gateway.

## Related

- Working from home checklist (KB-0033)
- Multi-factor authentication problems (KB-0008)
