import inspect
import unittest
import vibecoder.domain as domain
import vibecoder.ports as ports
import vibecoder.workers as workers
import vibecoder.daemon as daemon
from vibecoder.ports import CapabilityError


class NeutralityTests(unittest.TestCase):
    def test_core_contracts_do_not_reference_specific_provider(self):
        source = (inspect.getsource(domain) + inspect.getsource(ports) +
                  inspect.getsource(workers)).casefold()
        forbidden = "co" + "dex"
        self.assertNotIn(forbidden, source)
        self.assertNotIn("thread_id", source)
        self.assertNotIn("turn_id", source)

    def test_unsupported_capability_is_typed(self):
        error = CapabilityError("CANCELLATION")
        self.assertEqual(error.capability, "CANCELLATION")

    def test_daemon_does_not_import_codex_protocol_or_runtime(self):
        source = inspect.getsource(daemon)
        self.assertNotIn("codex_app_server", source)
        self.assertNotIn("codex_plugin_runtime", source)

