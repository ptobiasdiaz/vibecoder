import unittest

from vibecoder.domain import (
    Agent, AgentStatus, ExecutionCapacity, ExecutionOutcome, ExecutionState,
    GitHubConnectivity, PluginCapability, ProviderExecution, ProviderSession,
    ProviderSessionStatus, PublicReadiness, Worker, WorkerStatus,
)
from vibecoder.errors import ConflictError
from vibecoder.operational import derive_operational_state
from vibecoder.selection import select_worker


def worker(identifier, *, status=WorkerStatus.READY, active=0, limit=2, capabilities=()):
    return Worker(identifier, identifier, "plugin", "1", status, {}, (), f"/workers/{identifier}",
                  frozenset(capabilities), limit, active)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent("a", "p", "main", "coord", (),
                           frozenset({PluginCapability.STRUCTURED_OUTPUT}), (), 1, AgentStatus.ACTIVE)

    def test_selection_is_deterministic(self):
        selected = select_worker(self.agent, [worker("z", capabilities=(PluginCapability.STRUCTURED_OUTPUT,)),
                                               worker("a", capabilities=(PluginCapability.STRUCTURED_OUTPUT,))])
        self.assertEqual(selected.worker.id, "a")

    def test_continuity_precedes_load(self):
        session = ProviderSession("s", "p", "a", "coord", "z", "h", ProviderSessionStatus.ACTIVE)
        selected = select_worker(self.agent, [worker("a", capabilities=(PluginCapability.STRUCTURED_OUTPUT,)),
                                               worker("z", active=1, capabilities=(PluginCapability.STRUCTURED_OUTPUT,))],
                                 sessions=[session])
        self.assertEqual(selected.worker.id, "z")
        self.assertEqual(selected.continued_session, session)

    def test_pinned_unavailable_is_not_substituted(self):
        agent = Agent(**{**self.agent.__dict__, "pinned_worker_id": "x"})
        with self.assertRaises(ConflictError):
            select_worker(agent, [worker("x", status=WorkerStatus.UNAVAILABLE,
                                         capabilities=(PluginCapability.STRUCTURED_OUTPUT,)),
                                  worker("y", capabilities=(PluginCapability.STRUCTURED_OUTPUT,))])

    def test_insufficient_capability_or_capacity_is_rejected(self):
        with self.assertRaises(ConflictError):
            select_worker(self.agent, [worker("x")])
        with self.assertRaises(ConflictError):
            select_worker(self.agent, [worker("x", active=2, limit=2,
                                                capabilities=(PluginCapability.STRUCTURED_OUTPUT,))])

    def test_unknown_execution_blocks_reassignment(self):
        execution = ProviderExecution("e", "old", "s", ExecutionState.FINISHED, "key", ExecutionOutcome.UNKNOWN)
        with self.assertRaises(ConflictError):
            select_worker(self.agent, [worker("x", capabilities=(PluginCapability.STRUCTURED_OUTPUT,))],
                          previous_execution=execution)


class OperationalTests(unittest.TestCase):
    def test_public_states_and_ready_without_projects(self):
        self.assertEqual(derive_operational_state(GitHubConnectivity.UNCONFIGURED,
            ExecutionCapacity.NO_WORKER).readiness, PublicReadiness.STANDBY)
        self.assertEqual(derive_operational_state(GitHubConnectivity.ONLINE,
            ExecutionCapacity.NO_WORKER).readiness, PublicReadiness.STANDBY)
        ready = derive_operational_state(GitHubConnectivity.ONLINE, ExecutionCapacity.AVAILABLE)
        self.assertEqual(ready.readiness, PublicReadiness.READY)
        self.assertFalse(ready.projects_available)
        self.assertFalse(ready.work_allowed)

    def test_offline_degraded_pause_and_barrier_block_work(self):
        for github, execution, pause, barrier in [
            (GitHubConnectivity.OFFLINE, ExecutionCapacity.AVAILABLE, False, False),
            (GitHubConnectivity.AUTH_FAILED, ExecutionCapacity.AVAILABLE, False, False),
            (GitHubConnectivity.ONLINE, ExecutionCapacity.DEGRADED, False, False),
            (GitHubConnectivity.ONLINE, ExecutionCapacity.AVAILABLE, True, False),
            (GitHubConnectivity.ONLINE, ExecutionCapacity.AVAILABLE, False, True),
        ]:
            state = derive_operational_state(github, execution, administratively_paused=pause,
                recovery_barrier=barrier, projects_available=True, eligible_work=True)
            self.assertFalse(state.work_allowed)

