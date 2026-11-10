#!/usr/bin/python3
"""Build the binary Debian artifact without mutating the host Python."""
from __future__ import annotations
import hashlib
import gzip
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/"builds"/"vibecoder_0.0.1_amd64.deb"
CONTROL="""Package: vibecoder
Version: 0.0.1
Section: admin
Priority: optional
Architecture: amd64
Maintainer: Tobias Diaz <tobias.diaz@uclm.es>
Depends: adduser, ca-certificates, git, python3 (>= 3.11), python3-httpx (>= 0.26), systemd-sysv
Homepage: https://github.com/ptobiasdiaz/vibecoder
Description: provider-neutral GitHub development orchestrator
 Coordinates authorized Issues, human-approved plans, isolated AI workers,
 Git workspaces and human-merged pull requests.
"""
WRAPPERS={
 "vibecoder":"vibecoder.cli",
 "vibecoderd":"vibecoder.daemon",
 "vibecoder-worker-launcherd":"vibecoder.worker_launcher_service",
}
ACTIVE_MODULES=("__init__","approvals","cli","codex_app_server","codex_installation",
 "codex_plugin_runtime","control_protocol","control_service","daemon","domain","errors",
 "github_identity","git_local","maintenance_v1","operational","plugin_registry","projects",
 "resolution","runtime_approval_protocol","runtime_approval_server","runtime_protocol",
 "scheduler_v1","secret_store","selection","sqlite","worker_launcher_service",
 "worker_runtime","workers","workflows_v1")

def copy(source: Path, destination: Path, mode: int | None=None):
    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
    if mode is not None:destination.chmod(mode)

def compress(path: Path) -> None:
    destination=path.with_suffix(path.suffix+".gz")
    with path.open("rb") as source, destination.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,mtime=0) as output:shutil.copyfileobj(source,output)
    path.unlink();destination.chmod(0o644)

def main() -> int:
    OUTPUT.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vibecoder-deb-") as temporary:
        stage=Path(temporary)/"vibecoder_0.0.1_amd64";(stage/"DEBIAN").mkdir(parents=True)
        package=stage/"usr/lib/python3/dist-packages/vibecoder";package.mkdir(parents=True)
        for module in ACTIVE_MODULES:copy(ROOT/"vibecoder"/f"{module}.py",package/f"{module}.py",0o644)
        shutil.copytree(ROOT/"vibecoder/migrations",package/"migrations",ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
        (package/"plugin_descriptors").mkdir()
        copy(ROOT/"vibecoder/plugin_descriptors/codex.json",package/"plugin_descriptors/codex.json",0o644)
        for name,module in WRAPPERS.items():
            target=stage/"usr/bin"/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(f"#!/usr/bin/python3\nfrom {module} import main\nraise SystemExit(main())\n",encoding="utf-8");target.chmod(0o755)
        copy(ROOT/"debian/vibecoder.service",stage/"lib/systemd/system/vibecoder.service",0o644)
        copy(ROOT/"debian/vibecoder-worker-launcher.service",stage/"lib/systemd/system/vibecoder-worker-launcher.service",0o644)
        copy(ROOT/"debian/vibecoder.tmpfiles",stage/"usr/lib/tmpfiles.d/vibecoder.conf",0o644)
        copy(ROOT/"integration/codex/codex-artifacts.json",stage/"usr/share/vibecoder/codex-artifacts.json",0o644)
        copy(ROOT/"debian/vibecoder.1",stage/"usr/share/man/man1/vibecoder.1",0o644)
        copy(ROOT/"README.md",stage/"usr/share/doc/vibecoder/README.md",0o644)
        copy(ROOT/"docs/installation/first-run-0.0.1.md",stage/"usr/share/doc/vibecoder/first-run.md",0o644)
        copy(ROOT/"docs/workflows/issues-to-pr-0.0.1.md",stage/"usr/share/doc/vibecoder/issues-to-pr.md",0o644)
        copy(ROOT/"debian/copyright",stage/"usr/share/doc/vibecoder/copyright",0o644)
        copy(ROOT/"debian/changelog",stage/"usr/share/doc/vibecoder/changelog.Debian",0o644)
        compress(stage/"usr/share/man/man1/vibecoder.1");compress(stage/"usr/share/doc/vibecoder/changelog.Debian")
        for name in ("postinst","prerm","postrm"):
            copy(ROOT/"debian"/name,stage/"DEBIAN"/name,0o755)
        files=[]
        for path in sorted(x for x in stage.rglob("*") if x.is_file() and "DEBIAN" not in x.parts):
            if not (path.stat().st_mode & 0o111):path.chmod(0o644)
            digest=hashlib.md5(path.read_bytes()).hexdigest();files.append(f"{digest}  {path.relative_to(stage).as_posix()}")
            os.utime(path,(1794330000,1794330000),follow_symlinks=False)
        (stage/"DEBIAN/md5sums").write_text("\n".join(files)+"\n",encoding="ascii")
        size=sum(x.stat().st_size for x in stage.rglob("*") if x.is_file())//1024+1
        (stage/"DEBIAN/control").write_text(CONTROL+f"Installed-Size: {size}\n",encoding="utf-8")
        for path in stage.rglob("*"):
            if path.is_dir():path.chmod(0o755)
            os.utime(path,(1794330000,1794330000),follow_symlinks=False)
        for path in (stage/"DEBIAN").iterdir():path.chmod(0o755 if path.name in {"postinst","prerm","postrm"} else 0o644)
        environment=dict(os.environ);environment["SOURCE_DATE_EPOCH"]="1794330000"
        subprocess.run(("dpkg-deb","--root-owner-group","--build",str(stage),str(OUTPUT)),check=True,env=environment)
    print(OUTPUT)
    return 0
if __name__=="__main__":raise SystemExit(main())
