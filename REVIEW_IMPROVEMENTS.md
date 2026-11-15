# Review improvements integration

Integration branch: `fea/review-improvements`.
Base: `35fbc999dd7fd4bf42251b22c04059dfdbc51c22` (`main`).
The original clean checkout on `feat/review-improvements` is preserved.

## Coordination

Each numbered improvement has a dedicated GPT-6.1 Sol / high implementer,
branch and worktree. The coordinator alone writes the integration branch.
No pushes, pull requests or real provider evaluations are authorized.
At most three subagents run concurrently. Only `verification_runner` executes
state/database-backed checks, using isolated temporary config and state roots.

Implementation order: freeze evaluation baseline (7); source-only full-tree
planning (1) before preview (2); evidence policy (4) before symbol navigation (3)
and change attribution (5); Python suggestion checks (6) alongside planning;
opt-in discovery experiment (8) after the baseline and prerequisites.

## Results

All eight implementation branches are integrated. Each implementation and its
confirmed review fixes were committed by its dedicated agent. Merges belong to
the coordinator; the original checkout and `main` remain unchanged.

| Improvement | Implementation and fix commits | Verified result |
| --- | --- | --- |
| 1. Full-tree source once | `832b376` | Same `205b77e` input: 92 to 76 units, 14 to 8 batches, 1,504,223 to 772,626 serialized bytes (48.64% reduction). Legacy plans retain original requests. |
| 2. Scope preview | `92e17a2`, `791791e` | Real planner, text/JSON, no connection/history. Historical `c1b8a2f` reports occupy 26 of 29 units. Focused suite: 173 passed. |
| 3. Symbol navigation | `07d04f7`, `8f26852`, `fb0ac4a` | Bounded pinned literal search, read-before-citation, explicit legacy tool policy. Combined focused suite: 229 passed. |
| 4. Supporting test evidence | `4309c79` | Target selection unchanged; pinned tests readable under shared mandatory/explicit/rename policy. Focused suite: 126 passed. Replay c04 becomes rejected rather than incomplete. |
| 5. Change attribution | `7c84e20`, `9e9e764`, `a3dab1b` | Bounded merge-base requests; legacy contexts retained; changed merge base invalidates automatic confirmation reuse. Focused suite: 51 passed. |
| 6. Python suggestions | `86be366`, `1deca7e` | Invalid unambiguous replacement withheld, confirmed finding retained, initial/retry paths covered. Focused suite: 55 passed. |
| 7. Reproducible evaluation | `3772975`, `737d0a6`, `c7c40af` | Frozen clean-main baseline, five pinned cases, separate expected labels and duplicate/coverage/usage metrics. Focused suite: 10 passed. |
| 8. Optional discovery pass | `a4144fd`, `81501e3` | Off by default, shared analysis budget/deadline, distinct tasks, normal assessment and preserved partial results. Focused suite: 143 passed. |

## Review and verification boundaries

Repository workflow, security and contract reviewers inspected the relevant
changes. Confirmed defects were returned to the responsible agents: false
candidate/confirmation duplicate metrics, and historical confirmation reuse
after the merge base changes. Both received regressions and were corrected.
Test-isolation and interruption-timing fixture defects were also repaired without
weakening runtime guards.

The frozen baseline was executed against clean `35fbc999` before any behavior
integration. Replay scripts exercise real planning, validation and workflow code
with scripted provider responses. They do not measure live model quality.
The experiment's prospective acceptance gate remains closed: offline quality is
unproven, unknown usage remains unknown and incomplete coverage is not waived.

Full integrated pytest, Ruff, packaging, installed-wheel CLI checks and final
replays are the remaining verification step at this commit.
