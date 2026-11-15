# Offline review evaluation

1. Freeze fixture histories, replay responses and baseline provenance at engine main
   `35fbc999dd7fd4bf42251b22c04059dfdbc51c22` before behavior integration.
2. Rebuild exact Git commit pairs in temporary repositories. Keep grader expectations
   outside both source snapshots and all reviewer requests.
3. Replay scripted responses through the existing planner, execution, evidence
   validation and assessment pipeline, without credentials or network calls.
4. Grade discovery separately from confirmation, retaining duplicate counts,
   incomplete coverage and unknown usage. Compare an optional later experiment
   against the frozen baseline; acceptance is explicit and opt-in.
5. Delegate all runnable regressions and baseline artifact generation to the exclusive
   verification runner. This harness verifies offline wiring, never live model quality.

Scope: this directory and dedicated new tests only. Production modules, schemas,
configuration, existing tests and exports need no changes. No user data is loaded.
