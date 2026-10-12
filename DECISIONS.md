# Design decisions

One entry per design choice: the choice, the alternatives considered, and the reason.

<!-- Template
## D-NNN: Title
- **Choice:**
- **Alternatives:**
- **Reason:**
-->

## D-001: SLA timers use elapsed hours, not business hours
- **Choice:** Measure SLA response and resolution times in elapsed wall-clock hours.
- **Alternatives:** Business hours only (e.g. 08:00–18:00 Mon–Fri, excluding bank holidays); P1 on 24/7 and P2–P4 on business hours.
- **Reason:** Keeps the KPI logic simple and easy to verify by hand within the time available. Business-hours calendars are a well-understood extension and are listed as future work. Breach rates will look slightly worse than under a business-hours rule.
