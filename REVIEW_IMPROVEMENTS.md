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

Implementation commits, measured results, review findings and final verification
will be recorded here as each work branch is integrated.
