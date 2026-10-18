# Assistant evaluation: tune questions

Model `claude-haiku-5-5`. 28 questions. A question passes when every check that applies passes; rough keyword checks (must_include, no_recommendation) are reported separately and not counted.

| Type | Questions | Passed |
|---|---|---|
| data / data | 5 | 5 |
| data / data_abstain | 2 | 2 |
| data / definition | 1 | 0 |
| kb / abstain | 3 | 3 |
| kb / conflict | 1 | 1 |
| kb / fact | 10 | 10 |
| kb / multi_doc | 2 | 1 |
| kb / paraphrase | 3 | 3 |
| kb / superseded | 1 | 1 |
| **All** | 28 | 26 |

| Check | Pass | Fail | Not applicable |
|---|---|---|---|
| retrieval_hit | 17 | 0 | 11 |
| tool_used | 6 | 0 | 22 |
| figures | 5 | 0 | 23 |
| citation | 26 | 0 | 2 |
| abstention | 28 | 0 | 0 |
| must_include (rough) | 15 | 3 | 10 |
| no_recommendation (rough) | 1 | 0 | 27 |
| post_reply_checks | 26 | 2 | 0 |

## Cost (uncached calls only)

- API calls made: 0; served from the cache: 54 (of 54)
- Tokens: 0 in, 0 out
- Cost: £0.0000 (each dollar counted as a pound, D-037)
- From the call log for this run: 0 API calls, 0 tokens in, 0 out, £0.0000. Calls served from the cache here may have been paid for by an earlier, interrupted run; see data/assistant/calls.jsonl or `python -m pipeline.assistant.ask --spend` for all spend.

## Failures

- T04 must_include (rough): missing: ['the two articles disagree', 'KB-0030: an agent registers it with the user']
- T05 must_include (rough): missing: ['30 days']
- T08 must_include (rough): missing: ["the copier's scanning account password has expired or the account is blocked", "or the email service no longer accepts the copier's sign-in method"]
- T17 post_reply_checks: Figures not found in any tool result: 42.
- DT06 post_reply_checks: Cites sources not returned for this question: docs/01_business_processes.md.
