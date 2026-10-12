---
id: KB-0008
title: "Multi-factor authentication problems"
type: article
topic_ids: [access.mfa]
author: "Service desk (first line)"
created: 2025-04-16
updated: 2025-09-29
status: current
superseded_by: null
synthetic: true
---
# Multi-factor authentication problems

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

The user cannot complete the multi-factor authentication prompt when signing in. Common situations are a new or replaced phone, an authenticator app that was deleted, or codes that are rejected as wrong.

## Verify the user first

Clearing someone's authentication methods gives access to their account, so verify the caller the same way as for a password reset before changing anything.

## New phone or deleted app

1. In the admin centre, open the user's authentication methods.
2. Delete the old authenticator registration.
3. Require the user to register again.
4. Stay on the phone while the user signs in. They will be guided through registering the authenticator app on their new phone. The user completes the registration themselves on their own device; the service desk does not need to handle the phone.

## Codes rejected

Authenticator codes depend on the phone's clock. If codes are rejected, ask the user to check that the phone's date and time are set automatically. Once the clock is correct, codes are accepted again without any change on our side.

## Repeated prompts nobody asked for

If the user receives prompts they did not trigger, someone may know their password. Tell the user not to approve the prompt, reset the password, and follow the compromised account article.

## Related

- Suspected compromised account (KB-0022)
- Working from home checklist (KB-0033)
- Account and password security policy (KB-0050)
