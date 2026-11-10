"""Pure deterministic worker selection."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

from .domain import Agent, ExecutionOutcome, ProviderExecution, ProviderSession, Worker
from .errors import ConflictError, NotFoundError


@dataclass(frozen=True)
class WorkerSelection:
    worker: Worker
    continued_session: ProviderSession | None


def select_worker(agent: Agent, workers: Iterable[Worker], *,
                  sessions: Iterable[ProviderSession] = (),
                  previous_execution: ProviderExecution | None = None) -> WorkerSelection:
    if previous_execution and previous_execution.outcome is ExecutionOutcome.UNKNOWN:
        raise ConflictError("una ejecución UNKNOWN requiere reconciliación")
    available = list(workers)
    if agent.pinned_worker_id:
        found = next((w for w in available if w.id == agent.pinned_worker_id), None)
        if found is None:
            raise NotFoundError(f"worker fijado no encontrado: {agent.pinned_worker_id}")
        available = [found]
        pinned = True
    else:
        pinned = False
    available = [w for w in available if w.usable and agent.required_capabilities <= w.verified_capabilities]
    if not available:
        raise ConflictError("worker fijado no utilizable" if pinned else "ningún worker compatible y utilizable")
    session_by_worker = {s.worker_id: s for s in sessions if s.agent_id == agent.id}
    available.sort(key=lambda w: (
        0 if w.id in session_by_worker else 1,
        w.active_executions / w.concurrency_limit,
        w.name.casefold(), w.id,
    ))
    worker = available[0]
    return WorkerSelection(worker, session_by_worker.get(worker.id))
