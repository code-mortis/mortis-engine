# Optional second discovery pass

`mortis review --second-pass` opts into one additional discovery pass. The trusted
committed configuration can also set `[review].second_pass = true`, and Python
callers can supply `overrides={"second_pass": True}`. The default is false. Resume
uses the pinned configuration and saved overrides and rejects new overrides.
Historical plans without this option continue with one discovery pass.

This pass re-examines units whose first discovery completed validation, with the
first candidates supplied as untrusted avoid-repeat context. It has separate task
and provider-session identities and its own bounded evidence/validation rounds.
It is independent of those rounds, assessment evidence expansion and output-limit
recovery. It uses the same analysis token budget, deadline, reservations and
connection capacity as the first pass; it does not allocate another budget.
Unknown consumption blocks additional dispatch and resume never replays that
attempt. Validated cached tasks resume using their original immutable inputs.

Exact repeated allegations are removed before merging new candidates. Semantic
duplicates and all new candidates still follow the ordinary assessment policy.
First-pass findings and coverage survive second-pass failures. Such failures make
the review partial and appear in errors and `second_pass.units`; completed file
coverage still describes validated first-pass analysis. No failed first-pass unit
becomes complete because of the second pass. `second_pass` is absent when disabled.

## Offline experiment

The frozen baseline, corpus and grader under `evals/review_improvements` are
unchanged. `fixtures/second-pass.json` supplies additional scripted discovery of
c03 and repeats c01 deliberately to exercise duplicate suppression. It retains
c05's unknown consumption. The evaluator never calls providers or executes source.

Reproduce the offline experiment with:

```sh
PYTHONPATH=src /path/to/.venv/bin/python evals/review_improvements/run.py replay \
  --second-pass --recording evals/review_improvements/fixtures/second-pass.json \
  --output /tmp/second-pass-result.json
python evals/review_improvements/run.py compare \
  --baseline evals/review_improvements/baseline-result.json \
  --experiment /tmp/second-pass-result.json \
  --acceptance evals/review_improvements/acceptance.json
```

The scripted experiment is expected to increase discovery recall from 0.5 to 1.0,
confirm c03 only after normal assessment reads its unchanged dependency, and add
no duplicate candidates. These numbers test pipeline wiring, not model quality.
The acceptance command must exit 1: offline artifacts have
`quality_evidence=false`, and consumption remains unknown in c05. Prospective live
acceptance requires additional confirmed true defects and checks precision, coverage,
duplicates, total tokens and measured spend on an independent frozen holdout with
human adjudication. The default token and spend ceilings are each twice the baseline.

Dedicated regressions: `tests/review/test_second_discovery.py` and
`tests/evals/test_second_discovery.py`. Live-provider quality, cost and latency
are unmeasured. This is an opt-in experiment, not a recommended default.
