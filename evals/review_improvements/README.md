# Review improvement evaluation

This is a small reproducible **offline wiring evaluation**, not a model-quality
benchmark. It uses hand-authored mock responses through the actual Mortis planner,
analysis execution, Session usage accounting, candidate assessment, source expansion,
evidence validation and summary workflow. No provider was called to create these
responses. It cannot establish that a model discovers or correctly adjudicates bugs.

The baseline engine is main `35fbc999dd7fd4bf42251b22c04059dfdbc51c22`, before
review-behavior integration. `baseline.json` freezes the source commit pairs and
SHA-256 hashes of the fixtures, replay responses and grader labels. The generated
`baseline-result.json` records the actual baseline replay, source revision and dirty
source flag, full requests, validated reports and metrics. Keep that artifact intact
when running later experiments. The evaluation's own files do not affect the engine
source dirty flag. Hashes are reproducibility checks, not a security signature.

`fixtures/cases.json` has only neutral case IDs, source snapshots, changes and pinned
base/head SHA-1 Git objects. Rebuilding a different object fails. The evaluator never
runs analyzed source, repository instructions, hooks or fixture tests. The test used as
counterevidence is source evidence only; a separate dedicated regression exercises
that behavior in the evaluation's own test module.

| Case | Change | Grader expectation |
| --- | --- | --- |
| c01 | Changes a divisor to zero | Introduced defect |
| c02 | Changes a label next to an existing unsafe division | No introduced defect |
| c03 | Changes a caller to pass zero into an unchanged division | Introduced defect |
| c04 | Documents a guarded division and adds a zero-input regression | Allegation refuted by guard and test |
| c05 | Capitalizes a greeting | Clean change |

`expected.json` belongs exclusively to the grader, outside every Git snapshot and
reviewer request. It is loaded only after all requests finish. Mock responses are
reviewer outputs, not prompts, and never include grader labels in reviewer inputs.
Test counterevidence is in `test_app.py`. The baseline policy excludes that path,
so c04 remains uncertain with incomplete assessment coverage. This records a real
baseline limitation for a later counterevidence experiment, without bypassing policy.
The baseline intentionally fails to discover c03. That illustrates a recorded miss,
not a measured model failure. The c05 mock omits token usage to exercise unknown usage.

Run with the project's existing Python environment and select the engine source by
`PYTHONPATH`. No installation, credentials, configuration or network is needed:

```sh
PYTHONPATH=/path/to/baseline/src /path/to/.venv/bin/python \
  evals/review_improvements/run.py replay --output /tmp/baseline-result.json
PYTHONPATH=/path/to/experiment/src /path/to/.venv/bin/python \
  evals/review_improvements/run.py replay --output /tmp/experiment-result.json
python evals/review_improvements/run.py compare \
  --baseline /tmp/baseline-result.json --experiment /tmp/experiment-result.json
```

The output file must not exist. Replay uses fresh temporary repositories and XDG
state/config/cache paths, then removes them. This is state-backed test execution:
only the repository's designated verification runner may execute it while other
agents are working. Parallel worktrees do not isolate shared state automatically.
The gateway boundary is patched before inference; no real credentials are read.

Metrics are deliberately separate:

- **Discovery recall**: unique expected defects represented in validated candidates,
  divided by all expected defects. Rejected/failed assessments still count as discovered.
- **Confirmed precision**: unique correctly confirmed defects divided by all unique
  published confirmations. No confirmations means unavailable precision, never 100%.
- Duplicate candidates and duplicate published confirmations are counted independently
  of unique defects. Repeated candidate path/line anchors count even for preexisting or
  refuted allegations, independently of grader truth labels and title text.
  False confirmations use the same source anchor plus assessment cause/trigger when
  available, so different finding IDs do not hide duplicates or merge distinct causes.
- Incomplete cases retain failed/waived/missing file coverage, stage errors and uncertain
  assessments. A clean result never erases incomplete coverage.
- Known tokens sum only requests with known consumption. Unknown request counts remain
  visible and force total tokens to null; reservations are never treated as observed usage.

These tiny curated anchors are a shortcut: overlapping defects need explicit manual
matching before adding such cases. This set is too small for statistical conclusions,
is not a blinded holdout, and has no runtime, cost, latency or real-provider acceptance
claim. Reports retain raw requests/results for inspection.

For a future second discovery pass, keep the corpus and grader frozen and produce a
separate experiment artifact. `--recording /tmp/experiment-replay.json` can first test
new pipeline wiring against a modified scripted response set. For example, adding
response index `2` to `discovery.c03` exercises the caller/dependency assessment and
source expansion. This tests grading sensitivity only; it does not implement or prove
a second discovery pass. Do not tune the fixture labels to make the experiment pass.
Compare real runs using the same report/artifact shape, preserving provider/model,
prompts, usage, complete coverage and raw observations separately from grader labels;
this script deliberately supplies no live-provider execution path.

Acceptance is opt-in:

```sh
python evals/review_improvements/run.py compare \
  --baseline /tmp/baseline-result.json --experiment /tmp/experiment-result.json \
  --acceptance evals/review_improvements/acceptance.json
```

It exits 1 unless both artifacts are explicitly real quality evidence, recall improves
by at least 0.10, precision is 1.0, coverage is complete, published duplicates are zero
and consumption is known. Offline replay always sets `quality_evidence=false`, so it
**cannot pass quality acceptance**, even with perfect scripted metrics. These are
prospective minimum criteria, not permission to call providers or acceptance of this
corpus as sufficient real quality evidence. Real comparative evaluation additionally
needs a frozen independent holdout and human-adjudicated matching; changing a boolean
in an offline artifact does not create that evidence.

Dedicated checks, owned by the verifier:

```sh
PYTHONPATH=src /path/to/.venv/bin/python -m pytest -q tests/evals/test_review_improvements.py
/path/to/.venv/bin/ruff check evals/review_improvements tests/evals/test_review_improvements.py
```
