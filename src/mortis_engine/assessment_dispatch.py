"""Bounded local workflow dispatch and Assessment replay ordering."""

import asyncio

from .llm_http import _drain

ASSESSMENT_DISPATCH_POLICY = "continuous/v1"


def continuous_assessment(plan):
    if plan is None:
        return True
    policy = plan.get("assessment_dispatch")
    if policy not in (None, ASSESSMENT_DISPATCH_POLICY):
        raise ValueError("The saved assessment dispatch policy is unsupported.")
    return policy == ASSESSMENT_DISPATCH_POLICY


def replay_order(session, candidate_ids):
    """Recover the precedence of observations used by already pinned inputs."""
    if not getattr(session, "history", None):
        return {}, {}
    positions = {candidate_id: index for index, candidate_id in candidate_ids.items()}
    predecessors, order = {}, {}
    for candidate_id, dependencies in session.history.assessment_dependencies(session.run_id).items():
        if candidate_id not in positions:
            continue
        index = positions[candidate_id]
        if not positions.keys() >= set(dependencies):
            raise ValueError("A saved assessment depends on an unavailable candidate.")
        predecessors[index] = {positions[identity] for identity in dependencies}
        for identity in (*dependencies, candidate_id):
            order.setdefault(positions[identity], len(order))
    return predecessors, order


async def dispatch(rows, concurrency, select, assess, accept, *, continuous, predecessors=None, order=None):
    pending = {row[0]: row for row in rows}
    active = {}
    accepted = set()
    predecessors = predecessors or {}
    order = order or {}

    def rank(index):
        return order.get(index, len(order) + index)

    try:
        while pending or active:
            available = concurrency - len(active)
            if pending and available:
                blocked = {index for index, needs in predecessors.items()
                           if index in pending and not needs <= accepted}
                waiting = sorted(pending.values(), key=lambda row: rank(row[0]))
                chosen = select(waiting, list(active.values()), blocked, available, rank)
                for row in chosen:
                    del pending[row[0]]
                    active[asyncio.create_task(assess(row))] = row
            if not active:
                raise ValueError("No eligible assessment candidate can satisfy the saved dependencies.")
            if continuous:
                done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.gather(*active)
                done = list(active)
            for task in sorted(done, key=lambda task: active[task][0]):
                row = active.pop(task)
                accept(task.result())
                accepted.add(row[0])
    finally:
        if active:
            for task in active:
                task.cancel()
            await _drain(asyncio.gather(*active, return_exceptions=True))
