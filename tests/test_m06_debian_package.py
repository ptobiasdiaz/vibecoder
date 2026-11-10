from __future__ import annotations
import subprocess
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class DebianPackageTests(unittest.TestCase):
    def test_maintainer_scripts_and_units(self):
        for name in ("postinst","prerm","postrm"):
            subprocess.run(("/bin/sh","-n",str(ROOT/"debian"/name)),check=True)
        daemon=(ROOT/"debian/vibecoder.service").read_text()
        launcher=(ROOT/"debian/vibecoder-worker-launcher.service").read_text()
        self.assertIn("User=vibecoder",daemon);self.assertIn("ProtectSystem=strict",daemon)
        self.assertIn("ExecStart=/usr/bin/vibecoder-worker-launcherd",launcher)
    def test_final_artifact_metadata_and_contents(self):
        artifact=ROOT/"builds/vibecoder_0.0.1_amd64.deb";self.assertTrue(artifact.is_file())
        fields=subprocess.check_output(("dpkg-deb","-f",str(artifact),"Package","Version","Architecture"),text=True).splitlines()
        self.assertEqual(fields,["Package: vibecoder","Version: 0.0.1","Architecture: amd64"])
        contents=subprocess.check_output(("dpkg-deb","-c",str(artifact)),text=True)
        for path in ("./usr/bin/vibecoder","./usr/bin/vibecoderd","./usr/bin/vibecoder-worker-launcherd","./lib/systemd/system/vibecoder.service","./lib/systemd/system/vibecoder-worker-launcher.service","./usr/share/vibecoder/codex-artifacts.json","./usr/share/man/man1/vibecoder.1.gz","./usr/lib/python3/dist-packages/vibecoder/migrations/0001_schema.sql"):
            self.assertIn(path,contents)
        self.assertNotIn("vibecoder-admin",contents);self.assertNotIn("supervisor.json",contents)

if __name__=="__main__":unittest.main()
