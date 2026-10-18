# Assistant evaluation: tune questions

Model `claude-haiku-5-5`. 28 questions. A question passes when every check that applies passes; rough keyword checks (must_include, no_recommendation) are reported separately and not counted.

| Type | Questions | Passed |
|---|---|---|
| data / data | 5 | 4 |
| data / data_abstain | 2 | 2 |
| data / definition | 1 | 0 |
| kb / abstain | 3 | 3 |
| kb / conflict | 1 | 1 |
| kb / fact | 10 | 8 |
| kb / multi_doc | 2 | 1 |
| kb / paraphrase | 3 | 2 |
| kb / superseded | 1 | 0 |
| **All** | 28 | 21 |

| Check | Pass | Fail | Not applicable |
|---|---|---|---|
| retrieval_hit | 17 | 0 | 11 |
| tool_used | 6 | 0 | 22 |
| figures | 5 | 0 | 23 |
| citation | 23 | 3 | 2 |
| abstention | 27 | 1 | 0 |
| must_include (rough) | 10 | 8 | 10 |
| no_recommendation (rough) | 1 | 0 | 27 |
| post_reply_checks | 22 | 6 | 0 |

## Cost (uncached calls only)

- API calls made: 12; served from the cache: 47 (of 59)
- Tokens: 50,967 in, 1,914 out
- Cost: £0.0061 (each dollar counted as a pound, D-037)

## Failures

- T01 abstention: declined an answerable question
- T01 must_include (rough): missing: ['8 hours']
- T02 must_include (rough): missing: ['start Outlook in safe mode', 'add-ins']
- T02 post_reply_checks: Only one of the conflicting articles KB-0008 and KB-0030 is cited.
- T03 citation: cites must_not_cite ['KB-0026']
- T03 must_include (rough): missing: ['P1 if the whole business is affected']
- T03 post_reply_checks: Cites KB-0026, which is superseded by KB-0015.; Cites articles not returned for this question: KB-0026.
- T04 must_include (rough): missing: ['the two articles disagree', 'KB-0008: the user registers on their own device']
- T07 post_reply_checks: Figures not found in any tool result: 3, 4.
- T08 must_include (rough): missing: ["the copier's scanning account password has expired or the account is blocked", "or the email service no longer accepts the copier's sign-in method"]
- T10 post_reply_checks: Figures not found in any tool result: 12.
- T16 must_include (rough): missing: ['phone the service desk if they clicked a link or entered their password']
- T17 must_include (rough): missing: ['backups are kept for 30 days']
- T20 must_include (rough): missing: ['excluding bank holidays']
- DT03 citation: cites none of ['v_at_risk', 'v_kpi_bq06_summary']; cited ['semantic/metrics.yaml', 'v_at_risk, v_kpi_bq06_summary']
- DT03 post_reply_checks: Cites sources not returned for this question: v_at_risk, v_kpi_bq06_summary.
- DT06 citation: cites none of ['semantic/metrics.yaml']; cited ['semantic/metrics.yaml; docs/01_business_processes.md §1.3']
- DT06 post_reply_checks: Cites sources not returned for this question: semantic/metrics.yaml; docs/01_business_processes.md §1.3.
