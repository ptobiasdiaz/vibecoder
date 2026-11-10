import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vibecoder.secret_store import FileSecretStore, SecretStoreError


class SecretStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name) / "secrets"
    def tearDown(self): self.tmp.cleanup()

    def test_permissions_atomic_reference_and_restart(self):
        store = FileSecretStore(self.root)
        reference = store.write("secret")
        self.assertNotEqual(reference, "secret")
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / reference).stat().st_mode), 0o600)
        self.assertEqual(FileSecretStore(self.root).read(reference), "secret")
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_traversal_and_symlink_are_rejected(self):
        store = FileSecretStore(self.root)
        for reference in ("../x", "/tmp/x", "not-a-reference"):
            with self.assertRaises(SecretStoreError): store.read(reference)
        reference = "a" * 32 + ".secret"
        target = Path(self.tmp.name) / "target"; target.write_text("secret")
        (self.root / reference).symlink_to(target)
        with self.assertRaises(SecretStoreError): store.read(reference)
        with self.assertRaises(SecretStoreError): store.delete(reference)

    def test_interrupted_replace_leaves_no_published_secret(self):
        store = FileSecretStore(self.root)
        with patch("vibecoder.secret_store.os.replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError): store.write("secret")
        self.assertFalse(list(self.root.iterdir()))

    def test_cleanup_only_removes_safe_unreferenced_files(self):
        store = FileSecretStore(self.root)
        kept, removed = store.write("one"), store.write("two")
        self.assertEqual(store.cleanup_unreferenced({kept}), [removed])
        self.assertTrue(store.exists(kept)); self.assertFalse(store.exists(removed))

