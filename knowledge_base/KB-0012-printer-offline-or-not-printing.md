---
id: KB-0012
title: "Printer offline or not printing"
type: article
topic_ids: [printing.printer_offline]
author: "Service desk (first line)"
created: 2025-04-08
updated: 2025-07-14
status: current
superseded_by: null
synthetic: true
---
# Printer offline or not printing

> Synthetic document. Northgate IT Services Ltd is a fictional company; this text was written for a portfolio prototype.

## Symptoms

A printer shows as offline for one or more users, or print jobs sit in the queue and never print.

## Is it one user or everyone?

If the printer is offline for everyone, the problem is with the printer or the network. If only one user is affected, the problem is on their computer.

## Offline for everyone

1. Check the printer's own display for error messages such as paper jams or an open tray.
2. Print a configuration page from the printer's menu and note its network address.
3. Compare the address with the one set up on the print server or computers. Printers sometimes pick up a new address after a power cut or router change.
4. If the address has changed, set a reservation for the printer on the router or server so it keeps the same address, and update the printer port if needed.

## Jobs stuck in the queue

1. Open the print queue and cancel stuck jobs.
2. Restart the print spooler service on the computer or print server.
3. Send a test page.

## Still not printing

Switch the printer off and on, then try again. If the printer shows a hardware fault, arrange a visit or contact the printer's maintenance provider if the customer has one.

## Related

- Printer and copier basics (KB-0040)
- Adding a printer to a computer (KB-0014)
