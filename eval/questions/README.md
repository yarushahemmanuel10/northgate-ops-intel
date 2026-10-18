# Knowledge-base evaluation questions

30 questions for evaluating the Phase 5 assistant (BQ-11) on the knowledge base.

- `kb_eval_tune.yaml`: 20 questions. Use these while building and tuning.
- `kb_eval_heldout.yaml`: 10 questions. **Do not open, read or print this file while building
  or tuning.** It is used once, for the final evaluation, and its results are reported as they come out.

## How they were written

Written on 2026-10-18, before any retrieval or assistant code existed, from the 50 articles in
`knowledge_base/` only. They were written at the project author's request by a separate AI reviewer
(a different Claude session from the one building the pipeline), not by the builder, and without
reference to any generator answer key. Report them that way.

## Fields

- `type`: `fact` (one article answers it), `paraphrase` (answerable, but worded differently from the
  article), `multi_doc` (needs two articles), `superseded` (an outdated article would give the wrong
  answer), `conflict` (two current articles disagree), `abstain` (the knowledge base does not answer it).
- `expected_sources`: citing any one of these counts as a correct citation. Empty for `abstain`.
- `must_not_cite`: citing any of these as support is a failure.
- `answer_must_include`: facts a correct answer must contain. Judge meaning, not exact words.
- `correct_behaviour`: what a good answer does, in plain words.
