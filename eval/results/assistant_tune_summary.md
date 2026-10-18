# Assistant evaluation: tune questions

Model `claude-haiku-5-5`. 28 questions. A question passes when every check that applies passes; rough keyword checks (must_include, no_recommendation) are reported separately and not counted.

| Type | Questions | Passed |
|---|---|---|
| data / data | 5 | 5 |
| data / data_abstain | 2 | 2 |
| data / definition | 1 | 0 |
| kb / abstain | 3 | 3 |
| kb / conflict | 1 | 0 |
| kb / fact | 10 | 9 |
| kb / multi_doc | 2 | 1 |
| kb / paraphrase | 3 | 3 |
| kb / superseded | 1 | 1 |
| **All** | 28 | 24 |

| Check | Pass | Fail | Not applicable |
|---|---|---|---|
| retrieval_hit | 17 | 0 | 11 |
| tool_used | 6 | 0 | 22 |
| figures | 5 | 0 | 23 |
| citation | 24 | 2 | 2 |
| abstention | 27 | 1 | 0 |
| must_include (rough) | 10 | 8 | 10 |
| no_recommendation (rough) | 1 | 0 | 27 |
| post_reply_checks | 26 | 2 | 0 |

## Cost (uncached calls only)

- API calls made: 0; served from the cache: 59 (of 59)
- Tokens: 0 in, 0 out
- Cost: £0.0000 (each dollar counted as a pound, D-037)
- From the call log for this run: 0 API calls, 0 tokens in, 0 out, £0.0000. Calls served from the cache here may have been paid for by an earlier, interrupted run; see data/assistant/calls.jsonl or `python -m pipeline.assistant.ask --spend` for all spend.

## Failures

- T01 citation: cites none of ['KB-0045']; cited nothing
- T01 abstention: declined an answerable question
- T01 must_include (rough): missing: ['8 hours']
- T02 must_include (rough): missing: ['start Outlook in safe mode', 'add-ins']
- T03 must_include (rough): missing: ['P1 if the whole business is affected']
- T04 citation: cites none of ['KB-0008', 'KB-0030']; cited nothing
- T04 must_include (rough): missing: ['the two articles disagree', 'KB-0008: the user registers on their own device']
- T08 must_include (rough): missing: ["the copier's scanning account password has expired or the account is blocked", "or the email service no longer accepts the copier's sign-in method"]
- T10 post_reply_checks: Figures not found in any tool result: 12.
- T16 must_include (rough): missing: ['phone the service desk if they clicked a link or entered their password']
- T17 must_include (rough): missing: ['backups are kept for 30 days']
- T20 must_include (rough): missing: ['excluding bank holidays']
- DT06 post_reply_checks: Cites sources not returned for this question: docs/01_business_processes.md §1.3.
