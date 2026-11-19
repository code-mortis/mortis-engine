"""Bounded local workflow dispatch and Assessment replay ordering."""

import asyncio
from contextvars import ContextVar
from itertools import islice

from .llm_http import _drain

ASSESSMENT_DISPATCH_POLICY = "continuous/v1"
_park = ContextVar("park_dispatch_slot", default=None)


async def without_slot(wait):
    """Let other work run during a wait, then reacquire the current slot."""
    park = _park.get()
    return await (park(wait) if park else wait())


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


async def dispatch(rows, concurrency, select, process, accept, *, continuous, predecessors=None, order=None,
                   on_slot=None):
    """Run eligible rows and enqueue any follow-up rows returned by accept."""
    pending = {row[0]: row for row in rows}
    active = {}
    running = set()
    ready = {}
    completed = []
    changed = asyncio.Event()
    accepted = set()
    predecessors = predecessors or {}
    order = order or {}

    def rank(index):
        return order.get(index, len(order) + index)

    def finished(task):
        completed.append(task)
        changed.set()

    async def run(row):
        index = row[0]

        async def park(wait):
            running.remove(index)
            changed.set()
            if on_slot:
                on_slot(row, False)
            value = await wait()
            resume = ready[index] = asyncio.Event()
            changed.set()
            try:
                await resume.wait()
            finally:
                ready.pop(index, None)
            if on_slot:
                on_slot(row, True)
            return value

        token = _park.set(park if continuous else None)
        try:
            return await process(row)
        finally:
            _park.reset(token)

    try:
        while pending or active:
            changed.clear()
            done = sorted(completed, key=lambda task: active[task][0])
            completed.clear()
            for task in done:
                index = active.pop(task)[0]
                running.discard(index)
                added = accept(task.result())
                if added:
                    pending.update((row[0], row) for row in added)
                accepted.add(index)

            # Parked work keeps its dependencies, but releases its execution capacity.
            available = concurrency - len(running) if continuous or not active else 0
            for index in list(islice(ready, available)):
                running.add(index)
                ready.pop(index).set()
                available -= 1
            if pending and available:
                if select:
                    blocked = {index for index, needs in predecessors.items()
                               if index in pending and not needs <= accepted}
                    waiting = sorted(pending.values(), key=lambda row: rank(row[0]))
                    chosen = select(waiting, list(active.values()), blocked, available, rank)
                else:
                    chosen = list(islice(pending.values(), available))
                for row in chosen:
                    index = row[0]
                    del pending[index]
                    running.add(index)
                    task = asyncio.create_task(run(row))
                    active[task] = row
                    task.add_done_callback(finished)
            if not active:
                if pending:
                    raise ValueError("No eligible assessment candidate can satisfy the saved dependencies.")
            elif continuous:
                await changed.wait()
            else:
                await asyncio.gather(*active)
    finally:
        if active:
            for task in active:
                task.cancel()
            await _drain(asyncio.gather(*active, return_exceptions=True))
