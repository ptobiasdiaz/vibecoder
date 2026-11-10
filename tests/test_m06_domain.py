import unittest

from vibecoder.domain import (
    Agent, AgentStatus, AuthenticationMethod, ExecutionOutcome, ExecutionState,
    PluginCapability, PluginDescriptor, Project, ProjectAuthorization,
    ProjectStatus, ProviderExecution, ProviderSession, ProviderSessionStatus,
    Worker, WorkerStatus, worker_transition_allowed,
)
from vibecoder.errors import ValidationError


class DomainTests(unittest.TestCase):
    def test_project_and_authorization_do_not_require_worker(self):
        project = Project("p1", "breakout", 42, "owner/breakout", "main", "a" * 40,
                          ProjectStatus.ENABLED, "now", "now")
        auth = ProjectAuthorization(project.id, 7, "alice", "now")
        self.assertEqual(auth.project_id, project.id)

    def test_agents_are_scoped_to_project_and_need_no_worker(self):
        left = Agent("a1", "p1", "main", "coordinator", (), frozenset(), (), 1, AgentStatus.ACTIVE)
        right = Agent("a2", "p2", "main", "coordinator", (), frozenset(), (), 1, AgentStatus.ACTIVE)
        self.assertEqual(left.name, right.name)
        self.assertIsNone(left.pinned_worker_id)

    def test_plugin_descriptor_is_neutral_and_immutable(self):
        descriptor = PluginDescriptor("provider", "1", "Example", frozenset({"linux-x86_64"}),
            frozenset({AuthenticationMethod.API_KEY}), frozenset({PluginCapability.STRUCTURED_OUTPUT}), {"x": 1})
        with self.assertRaises(TypeError):
            descriptor.metadata["x"] = 2

    def test_session_reference_is_opaque(self):
        session = ProviderSession("s", "p", "a", "implementation", "w", "hash",
                                  ProviderSessionStatus.ACTIVE, provider_reference="anything/provider/owns")
        self.assertEqual(session.provider_reference, "anything/provider/owns")

    def test_execution_separates_running_and_terminal_outcome(self):
        with self.assertRaises(ValidationError):
            ProviderExecution("e", "w", "s", ExecutionState.RUNNING, "key", ExecutionOutcome.SUCCEEDED)
        unknown = ProviderExecution("e", "w", "s", ExecutionState.FINISHED, "key", ExecutionOutcome.UNKNOWN)
        self.assertFalse(unknown.safe_to_retry_as_new)

    def test_worker_is_usable_only_when_ready_and_below_limit(self):
        ready = Worker("w", "one", "p", "1", WorkerStatus.READY, {}, (), "/tmp/w",
                       frozenset(), 1)
        busy = Worker("x", "two", "p", "1", WorkerStatus.READY, {}, (), "/tmp/x",
                      frozenset(), 1, 1)
        self.assertTrue(ready.usable)
        self.assertFalse(busy.usable)

    def test_worker_transitions_are_explicit(self):
        self.assertTrue(worker_transition_allowed(WorkerStatus.INSTALLING, WorkerStatus.VERIFYING))
        self.assertFalse(worker_transition_allowed(WorkerStatus.INSTALLING, WorkerStatus.READY))

