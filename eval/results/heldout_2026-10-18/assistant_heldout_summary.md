# Assistant evaluation: heldout questions

Model `claude-haiku-5-5`. 14 questions. A question passes when every check that applies passes; rough keyword checks (must_include, no_recommendation) are reported separately and not counted.

| Type | Questions | Passed |
|---|---|---|
| data / data | 3 | 2 |
| data / data_abstain | 1 | 1 |
| kb / abstain | 1 | 1 |
| kb / conflict | 1 | 1 |
| kb / fact | 4 | 4 |
| kb / multi_doc | 1 | 1 |
| kb / paraphrase | 2 | 2 |
| kb / superseded | 1 | 1 |
| **All** | 14 | 13 |

| Check | Pass | Fail | Not applicable |
|---|---|---|---|
| retrieval_hit | 9 | 0 | 5 |
| tool_used | 3 | 0 | 11 |
| figures | 3 | 0 | 11 |
| citation | 13 | 0 | 1 |
| abstention | 14 | 0 | 0 |
| must_include (rough) | 5 | 4 | 5 |
| no_recommendation (rough) | 1 | 0 | 13 |
| post_reply_checks | 13 | 1 | 0 |

## Cost (uncached calls only)

- API calls made: 27; served from the cache: 0 (of 27)
- Tokens: 148,163 in, 5,067 out
- Cost: £0.0174 (each dollar counted as a pound, D-037)
- From the call log for this run: 27 API calls, 148,163 tokens in, 5,067 out, £0.0174. Calls served from the cache here may have been paid for by an earlier, interrupted run; see data/assistant/calls.jsonl or `python -m pipeline.assistant.ask --spend` for all spend.

## Failures

- H02 must_include (rough): missing: ['the two articles disagree', 'KB-0008 says yes']
- H03 must_include (rough): missing: ["set the phone's date and time to automatic"]
- H04 must_include (rough): missing: ['office printers are not available from home']
- H08 must_include (rough): missing: ['phone the customer with updates']
- DH01 post_reply_checks: Figures not found in any tool result: 623.
