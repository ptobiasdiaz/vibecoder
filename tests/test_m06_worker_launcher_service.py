import unittest
from vibecoder.runtime_protocol import encode_runtime
from vibecoder.worker_launcher_service import receive_frame, validate_request


class WorkerLauncherServiceTests(unittest.TestCase):
    def request(self):
        worker = "12345678-1234-1234-1234-123456789abc"
        return {"version": 1, "operation": "launch", "worker_id": worker, "arguments": {
            "unix_user": "vibecoder-w-123456781234", "module": "vibecoder.fake_plugin_runtime",
            "storage_path": "/var/lib/vibecoder/workers/" + worker, "workspace_path": None,
            "timeout": 30, "runtime_request": {"version": 1, "worker_id": worker}}}

    def test_accepts_fixed_identity_and_storage(self):
        spec, _ = validate_request(self.request()); self.assertEqual(spec.unix_user, "vibecoder-w-123456781234")

    def test_rejects_root_arbitrary_storage_and_module(self):
        for field, value in (("unix_user", "root"), ("storage_path", "/etc"), ("module", "os")):
            request = self.request(); request["arguments"][field] = value
            with self.assertRaises((ValueError, Exception)): validate_request(request)

    def test_receives_fragmented_stream_frame_exactly(self):
        frame = encode_runtime(self.request())
        class FragmentedStream:
            def __init__(self, value): self.value, self.offset = value, 0
            def recv(self, maximum):
                if self.offset == len(self.value): return b""
                result = self.value[self.offset:self.offset + min(maximum, 1)]
                self.offset += len(result); return result
        self.assertEqual(receive_frame(FragmentedStream(frame)), frame)


if __name__ == "__main__": unittest.main()
