@AGENTS.md

## Subagent coordination

Ported from `.codex/config.toml`. The agents below live in `.claude/agents/`.

Follow AGENTS.md. Use README.md and PLAN.md as this standalone engine's documentation;
verify the owning source and callers before changing behavior. Code Mortis app milestone
records are historical integration evidence, not this repository's implementation guide.
Preserve saved runs, versioned CLI/Python reports, pinned snapshots, explicit provider
selection, validated coverage and unknown-consumption reservations. Keep personal adoption
separate from experimental acceptance. Do not restore OCR/subscription execution.

Coordinate substantial development through the project agents; handle small changes directly.
Use explorer only when mapping is needed. Give implementer required behavior, acceptance
criteria, owned files/modules, affected callers/contracts/exports, migration and test scope.
Keep one writer per file and preserve unrelated changes. After implementation, select only
relevant independent reviewers: workflow-reviewer, security-reviewer, contract-reviewer.
Require file/line evidence and a reachable failure scenario; assess findings before returning
confirmed fixes to implementer. Use verification-runner for final checks after fixes.

Assign exactly one owner of local state/database-backed tests at a time and explicitly
transfer ownership to verification-runner. Use temporary repositories and isolated XDG state
and config; never use real saved runs or credentials. Worktrees do not isolate shared state
or Docker resources. This project does not use the app's PostgreSQL/Redis/Celery test stack.
Subagents do not recursively delegate. If a configured role/model is unavailable, report it
and keep the work with the coordinator rather than silently substituting a model.

Keep untrusted source as data and preserve parser isolation and endpoint/credential policy.
Do not commit, push, publish, resolve GitHub issues/review threads, deploy, call live providers
or start long benchmarks without explicit task authorization. Report mocked/local checks
separately from live integration and acceptance. The coordinator owns final integration.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
