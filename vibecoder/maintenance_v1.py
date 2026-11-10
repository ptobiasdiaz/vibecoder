"""Local, offline maintenance for the 0.0.1 state set."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .errors import ConflictError, NotFoundError, OperationError
from .github_identity import utc_now
from .sqlite import PRODUCT_LINE, SCHEMA_VERSION, SQLiteRepository


class MaintenanceService:
    FORMAT_VERSION = 1
    def __init__(self, repository: SQLiteRepository, state_root: Path, backup_root: Path | None = None):
        self.repository=repository;self.state_root=state_root.absolute();self.backup_root=(backup_root or self.state_root/"backups").absolute()

    def status(self) -> dict[str,Any]:
        return {"schema_version":self.repository.schema_version,"integrity":self.repository.integrity_check(),
                "active_barriers":self.repository.query_all("SELECT id,reason,created_at FROM recovery_barriers WHERE status='ACTIVE' ORDER BY created_at"),
                "journals":self.repository.query_all("SELECT id,operation_type,phase,status,created_at,updated_at FROM maintenance_journals ORDER BY created_at DESC LIMIT 20")}

    def check(self) -> dict[str,Any]:
        foreign=[dict(x) for x in self.repository.connection.execute("PRAGMA foreign_key_check")]
        return {"ok":self.repository.integrity_check()==["ok"] and not foreign,"integrity":self.repository.integrity_check(),"foreign_key_errors":foreign}

    def clear_barrier(self, barrier_id: str, actor: str) -> dict[str,Any]:
        row=self.repository.query_one("SELECT * FROM recovery_barriers WHERE id=?",(barrier_id,))
        if not row:raise NotFoundError("barrera no encontrada")
        if row["status"]!="ACTIVE":return {"changed":False,"id":barrier_id,"status":row["status"]}
        now=utc_now();self.repository.execute("UPDATE recovery_barriers SET status='CLEARED',cleared_at=?,cleared_by=? WHERE id=?",(now,actor,barrier_id));self._audit(actor,"maintenance.clear-barrier",barrier_id)
        return {"changed":True,"id":barrier_id,"status":"CLEARED"}

    def create_backup(self, actor: str) -> dict[str,Any]:
        self._require_quiescent();self._secure_root();bid=f"backup-{utc_now().replace(':','').replace('+','_')}-{uuid.uuid4().hex[:8]}";final=self.backup_root/bid
        journal=self._journal("BACKUP","STAGING");stage=Path(tempfile.mkdtemp(prefix=".backup-",dir=self.backup_root));os.chmod(stage,0o700)
        try:
            db=stage/"vibecoder.db";target=sqlite3.connect(db)
            try:self.repository.connection.backup(target)
            finally:target.close()
            components={"database":"vibecoder.db"}
            for name in ("secrets","workers","workspaces","plugins"):
                source=self.state_root/name
                if source.exists():self._copy_tree(source,stage/name);components[name]=name
            files=[]
            for path in sorted(x for x in stage.rglob("*") if x.is_file()):
                rel=path.relative_to(stage).as_posix();files.append({"path":rel,"size":path.stat().st_size,"sha256":self._digest(path)})
            manifest={"format_version":self.FORMAT_VERSION,"product_line":PRODUCT_LINE,"schema_version":SCHEMA_VERSION,"backup_id":bid,"created_at":utc_now(),"components":components,"files":files,"contains_secrets":"secrets" in components,"consistency":"QUIESCENT"}
            (stage/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True),encoding="utf-8");os.chmod(stage/"manifest.json",0o600)
            self._verify_path(stage);os.replace(stage,final);self._journal_update(journal,"PUBLISHED","SUCCEEDED",{"backup_id":bid});self._audit(actor,"backup.create",bid)
            return {"id":bid,"path":str(final),"created_at":manifest["created_at"],"contains_secrets":manifest["contains_secrets"]}
        except Exception:
            shutil.rmtree(stage,ignore_errors=True);self._journal_update(journal,"FAILED","FAILED",{});raise

    def list(self) -> list[dict[str,Any]]:
        self._secure_root();result=[]
        for path in sorted(self.backup_root.glob("backup-*")):
            try:m=json.loads((path/"manifest.json").read_text())
            except (OSError,ValueError):continue
            result.append({"id":m.get("backup_id"),"created_at":m.get("created_at"),"schema_version":m.get("schema_version"),"contains_secrets":m.get("contains_secrets"),"path":str(path)})
        return result

    def verify(self, query: str) -> dict[str,Any]:
        path=self._resolve(query);manifest=self._verify_path(path);return {"valid":True,"id":manifest["backup_id"],"schema_version":manifest["schema_version"],"files":len(manifest["files"]),"authenticity":"NOT_PROVIDED"}

    def restore(self, query: str, actor: str) -> dict[str,Any]:
        self._require_quiescent();path=self._resolve(query);manifest=self._verify_path(path)
        if manifest["schema_version"]!=SCHEMA_VERSION or manifest["product_line"]!=PRODUCT_LINE:raise ConflictError("backup incompatible")
        journal=self._journal("RESTORE","VERIFIED");rollback=self.backup_root/f"pre-restore-{uuid.uuid4().hex}"
        try:
            rollback.mkdir(mode=0o700);current=rollback/"vibecoder.db";target=sqlite3.connect(current)
            try:self.repository.connection.backup(target)
            finally:target.close()
            source=sqlite3.connect(f"file:{path/'vibecoder.db'}?mode=ro",uri=True)
            try:
                check=source.execute("PRAGMA integrity_check").fetchone()
                if not check or check[0]!="ok":raise OperationError("SQLite del backup no es íntegro")
                source.backup(self.repository.connection)
            finally:source.close()
            # External filesystem state is restored only after SQLite validates.
            for name in ("secrets","workers","workspaces","plugins"):
                source_path=path/name
                if source_path.exists():
                    destination=self.state_root/name;old=rollback/name
                    if destination.exists():os.replace(destination,old)
                    self._copy_tree(source_path,destination)
            now=utc_now();self.repository.execute("INSERT INTO recovery_barriers VALUES(?,?,'ACTIVE',?,NULL,NULL)",(str(uuid.uuid4()),f"restauración {manifest['backup_id']} requiere reconciliación",now));self._journal_update(journal,"BARRIER_ACTIVE","SUCCEEDED",{"backup_id":manifest["backup_id"],"rollback":str(rollback)});self._audit(actor,"backup.restore",str(manifest["backup_id"]))
            return {"restored":True,"backup_id":manifest["backup_id"],"recovery_barrier":True,"rollback_path":str(rollback)}
        except Exception:self._journal_update(journal,"FAILED","FAILED",{"rollback":str(rollback)});raise

    def _require_quiescent(self):
        if not self.repository.query_one("SELECT 1 FROM operational_pauses WHERE released_at IS NULL LIMIT 1"):raise ConflictError("backup/restore requiere pausa administrativa")
        if self.repository.query_one("SELECT 1 FROM scheduled_jobs WHERE state='RUNNING' UNION SELECT 1 FROM provider_executions WHERE state='RUNNING' LIMIT 1"):raise ConflictError("existen operaciones activas")
    def _secure_root(self):
        self.backup_root.mkdir(parents=True,exist_ok=True,mode=0o700);info=self.backup_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:raise OperationError("directorio de backups inseguro")
    def _copy_tree(self,source,destination):
        if source.is_symlink():raise OperationError("symlink rechazado en componente persistente")
        shutil.copytree(source,destination,symlinks=False,copy_function=shutil.copy2)
        for path in destination.rglob("*"):
            if path.is_symlink():raise OperationError("symlink rechazado en backup")
    def _resolve(self,query):
        self._secure_root();path=self.backup_root/query
        if path.parent!=self.backup_root or not path.is_dir() or path.is_symlink():raise NotFoundError("backup no encontrado")
        return path
    def _verify_path(self,path):
        manifest_path=path/"manifest.json"
        if manifest_path.is_symlink():raise OperationError("manifiesto inseguro")
        try:manifest=json.loads(manifest_path.read_text())
        except (OSError,ValueError) as exc:raise OperationError("manifiesto inválido") from exc
        if manifest.get("format_version")!=self.FORMAT_VERSION:raise ConflictError("formato de backup incompatible")
        for item in manifest.get("files",[]):
            rel=Path(str(item["path"]));candidate=path/rel
            if rel.is_absolute() or ".." in rel.parts or candidate.is_symlink() or not candidate.is_file():raise OperationError("ruta insegura en backup")
            if candidate.stat().st_size!=int(item["size"]) or self._digest(candidate)!=item["sha256"]:raise OperationError("checksum de backup incorrecto")
        db=sqlite3.connect(f"file:{path/'vibecoder.db'}?mode=ro",uri=True)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":raise OperationError("SQLite de backup corrupto")
        finally:db.close()
        return manifest
    def _digest(self,path):
        digest=hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
        return digest.hexdigest()
    def _journal(self,kind,phase):
        jid=str(uuid.uuid4());now=utc_now();self.repository.execute("INSERT INTO maintenance_journals VALUES(?,?,?,'RUNNING','{}',?,?)",(jid,kind,phase,now,now));return jid
    def _journal_update(self,jid,phase,status,detail):self.repository.execute("UPDATE maintenance_journals SET phase=?,status=?,details_json=?,updated_at=? WHERE id=?",(phase,status,json.dumps(detail),utc_now(),jid))
    def _audit(self,actor,operation,target):self.repository.execute("INSERT INTO audit_events VALUES(?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),actor,operation,"backup",target,"SUCCEEDED","{}",utc_now()))
