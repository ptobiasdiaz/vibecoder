"""Provider-neutral Issues -> plans -> tasks -> pull requests workflow.

Every remote effect is preceded by an ``external_operations`` intention.  The
module deliberately exposes no merge operation and filters untrusted text
before it can reach a worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

import httpx

from .errors import ConflictError, NotFoundError, OperationError, ValidationError
from .github_identity import GitHubVerificationError, GitHubFailureCategory, utc_now
from .sqlite import SQLiteRepository

MARKER = "<!-- vibecoder:plan:"
APPROVE_RE = re.compile(r"^/vibecoder\s+approve\s+([0-9a-f]{64})\s*$", re.I)
PLAN_RE = re.compile(r"^/vibecoder\s+plan\s*$", re.I)
LOGICAL_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")


class GitHubWorkflowAPI(Protocol):
    def issues(self, token: str, repository: str) -> list[Mapping[str, Any]]: ...
    def comments(self, token: str, repository: str, issue_number: int) -> list[Mapping[str, Any]]: ...
    def post_comment(self, token: str, repository: str, issue_number: int, body: str) -> Mapping[str, Any]: ...
    def create_pull_request(self, token: str, repository: str, *, title: str, head: str, base: str, body: str) -> Mapping[str, Any]: ...
    def find_pull_request(self, token: str, repository: str, *, head: str, base: str) -> Mapping[str, Any] | None: ...
    def pull_request(self, token: str, repository: str, number: int) -> Mapping[str, Any]: ...
    def reviews(self, token: str, repository: str, number: int) -> list[Mapping[str, Any]]: ...
    def branch_sha(self, token: str, repository: str, branch: str) -> str | None: ...


class HttpGitHubWorkflowAPI:
    def __init__(self, *, api_url: str = "https://api.github.com", timeout: float = 15.0,
                 transport: httpx.BaseTransport | None = None):
        self.api_url, self.timeout, self.transport = api_url.rstrip("/"), timeout, transport

    def issues(self, token: str, repository: str) -> list[Mapping[str, Any]]:
        return self._pages(token,f"/repos/{quote(repository,safe='/')}/issues",{"state":"open"})

    def comments(self, token: str, repository: str, issue_number: int) -> list[Mapping[str, Any]]:
        return self._pages(token,f"/repos/{quote(repository,safe='/')}/issues/{issue_number}/comments",{})

    def post_comment(self, token: str, repository: str, issue_number: int, body: str) -> Mapping[str, Any]:
        return self._request(token, "POST", f"/repos/{quote(repository, safe='/')}/issues/{issue_number}/comments", json_body={"body":body})

    def create_pull_request(self, token: str, repository: str, *, title: str, head: str, base: str, body: str) -> Mapping[str, Any]:
        return self._request(token, "POST", f"/repos/{quote(repository, safe='/')}/pulls", json_body={"title":title,"head":head,"base":base,"body":body})

    def find_pull_request(self, token: str, repository: str, *, head: str, base: str) -> Mapping[str, Any] | None:
        owner=repository.split("/",1)[0];values=self._request(token,"GET",f"/repos/{quote(repository,safe='/')}/pulls",params={"state":"all","head":f"{owner}:{head}","base":base,"per_page":10})
        return values[0] if values else None

    def pull_request(self, token: str, repository: str, number: int) -> Mapping[str, Any]:
        return self._request(token, "GET", f"/repos/{quote(repository, safe='/')}/pulls/{number}")

    def reviews(self, token: str, repository: str, number: int) -> list[Mapping[str, Any]]:
        result = self._pages(token,f"/repos/{quote(repository,safe='/')}/pulls/{number}/reviews",{})
        result += self._pages(token,f"/repos/{quote(repository,safe='/')}/pulls/{number}/comments",{})
        return result

    def branch_sha(self, token: str, repository: str, branch: str) -> str | None:
        try:value=self._request(token,"GET",f"/repos/{quote(repository,safe='/')}/git/ref/heads/{quote(branch,safe='')}")
        except GitHubVerificationError as exc:
            if exc.category is GitHubFailureCategory.NOT_FOUND:return None
            raise
        sha=value.get("object",{}).get("sha") if isinstance(value,dict) else None
        return str(sha) if isinstance(sha,str) else None

    def _request(self, token: str, method: str, path: str, *, params=None, json_body=None):
        headers = {"Accept":"application/vnd.github+json","Authorization":f"Bearer {token}",
                   "X-GitHub-Api-Version":"2022-11-28","User-Agent":"vibecoder/0.0.1"}
        try:
            with httpx.Client(base_url=self.api_url, headers=headers, timeout=self.timeout, transport=self.transport) as client:
                response = client.request(method, path, params=params, json=json_body)
        except httpx.TimeoutException as exc:
            raise GitHubVerificationError(GitHubFailureCategory.TIMEOUT, "GitHub no respondió antes del timeout") from exc
        except httpx.NetworkError as exc:
            raise GitHubVerificationError(GitHubFailureCategory.NETWORK, "no se pudo conectar con GitHub") from exc
        accepted = tuple(x.strip() for x in response.headers.get("x-accepted-github-permissions", "").split(",") if x.strip())
        if response.status_code == 401: raise GitHubVerificationError(GitHubFailureCategory.TOKEN_REJECTED, "GitHub rechazó el PAT", status_code=401)
        if response.status_code == 403: raise GitHubVerificationError(GitHubFailureCategory.ACCESS_DENIED, "GitHub denegó la operación", status_code=403, accepted_permissions=accepted)
        if response.status_code == 404: raise GitHubVerificationError(GitHubFailureCategory.NOT_FOUND, "recurso GitHub no encontrado o fuera del alcance del PAT", status_code=404, accepted_permissions=accepted)
        if response.status_code >= 500: raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR, "GitHub no está disponible", status_code=response.status_code)
        if response.status_code not in {200,201}: raise GitHubVerificationError(GitHubFailureCategory.GITHUB_ERROR, f"GitHub devolvió HTTP {response.status_code}", status_code=response.status_code)
        value = response.json()
        if not isinstance(value, (dict, list)): raise OperationError("respuesta GitHub inesperada")
        return value

    def _pages(self,token,path,params):
        result=[]
        for page in range(1,101):
            values=self._request(token,"GET",path,params={**params,"per_page":100,"page":page})
            if not isinstance(values,list):raise OperationError("GitHub devolvió una colección inesperada")
            result.extend(values)
            if len(values)<100:return result
        raise OperationError("la paginación GitHub excede el límite seguro")


@dataclass(frozen=True)
class PlanTask:
    logical_id: str
    title: str
    description: str
    agent: str
    dependencies: tuple[str, ...]
    acceptance: tuple[str, ...]


class WorkflowService:
    def __init__(self, repository: SQLiteRepository, identity: Any, workers: Any,
                 api_factory: Callable[[], GitHubWorkflowAPI] = HttpGitHubWorkflowAPI):
        self.repository, self.identity, self.workers, self.api_factory = repository, identity, workers, api_factory

    def poll(self, project_id: str) -> dict[str, int]:
        project = self._project(project_id); token, identity = self.identity.credential_for_internal_use(); api = self.api_factory()
        authorized = {int(x["github_user_id"]) for x in self.repository.query_all("SELECT github_user_id FROM project_authorized_users WHERE project_id=?", (project_id,))}
        counts = {"issues":0,"messages":0,"discarded":0,"approvals":0,"merges":0}
        for item in api.issues(token, project["canonical_name"]):
            if "pull_request" in item: continue
            remote_id, number = int(item["id"]), int(item["number"]); author = int(item["user"]["id"])
            issue_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"github-issue:{remote_id}")); now = str(item.get("created_at") or utc_now())
            if author not in authorized:
                counts["discarded"] += self._discard(project_id,"ISSUE",str(remote_id),author,now,"UNAUTHORIZED_AUTHOR")
                continue
            if not self.repository.query_one("SELECT 1 FROM issue_workflows WHERE id=?", (issue_id,)):
                self.repository.execute("INSERT INTO issue_workflows VALUES(?,?,?,?,?,?,?)", (issue_id,project_id,remote_id,number,"NEW",now,utc_now())); counts["issues"] += 1
                self._message(issue_id,"ISSUE",str(remote_id),author,str(item.get("body") or ""),now)
                self._schedule(project_id,"GENERATE_PLAN","issue",issue_id,f"plan:{issue_id}",50)
            for comment in api.comments(token, project["canonical_name"], number):
                cid, cauthor = str(comment["id"]), int(comment["user"]["id"]); created = str(comment.get("created_at") or utc_now())
                if cauthor not in authorized:
                    counts["discarded"] += self._discard(project_id,"ISSUE_COMMENT",cid,cauthor,created,"UNAUTHORIZED_AUTHOR"); continue
                body = str(comment.get("body") or "")
                if self._message(issue_id,"ISSUE_COMMENT",cid,cauthor,body,created):
                    counts["messages"] += 1
                    match = APPROVE_RE.fullmatch(body.strip())
                    if match: counts["approvals"] += self.approve_plan(issue_id, match.group(1), cauthor, cid)
                    elif PLAN_RE.fullmatch(body.strip()): self._schedule(project_id,"GENERATE_PLAN","issue",issue_id,f"plan:{issue_id}:{cid}",50)
        counts["merges"] += self._poll_pull_requests(project, token, api, authorized)
        now = utc_now(); self.repository.execute("INSERT INTO workflow_cursors VALUES(?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET last_poll_at=excluded.last_poll_at,last_success_at=excluded.last_success_at,last_error=NULL,updated_at=excluded.updated_at", (project_id,now,now,None,now))
        return counts

    def generate_plan(self, issue_id: str) -> dict[str, Any]:
        issue = self._issue(issue_id); project = self._project(str(issue["project_id"])); agent = self._ensure_agent(project["id"], "main", "orquestador principal")
        messages = self.repository.query_all("SELECT body FROM authorized_messages WHERE issue_id=? ORDER BY created_at,id", (issue_id,))
        prompt = "Genera un plan de implementación. Devuelve únicamente JSON: {\"tasks\":[{\"id\",\"title\",\"description\",\"agent\",\"dependencies\",\"acceptance\"}]}. Entradas autorizadas:\n" + "\n".join(str(x["body"]) for x in messages)
        worker = self._ready_worker()
        session = self.workers.create_session(project["id"], agent, "PLAN", hashlib.sha256(prompt.encode()).hexdigest(), str(worker["id"]))
        execution = self.workers.execute(str(session["id"]), f"plan:{issue_id}:{len(messages)}", prompt)
        if execution.get("outcome") == "UNKNOWN": self._barrier(f"ejecución de plan incierta {execution['id']}"); raise ConflictError("la generación del plan quedó UNKNOWN")
        if execution.get("outcome") != "SUCCEEDED": raise OperationError("el worker no pudo generar el plan")
        raw = json.loads(str(execution.get("result_json") or "{}")); value = raw.get("text", raw)
        if isinstance(value, str):
            text=value.strip()
            if text.startswith("```json") and text.endswith("```"):text=text[7:-3].strip()
            elif text.startswith("```") and text.endswith("```"):text=text[3:-3].strip()
            value = json.loads(text)
        tasks = self._validate_plan(value)
        canonical = {"tasks":[{"id":t.logical_id,"title":t.title,"description":t.description,"agent":t.agent,"dependencies":list(t.dependencies),"acceptance":list(t.acceptance)} for t in tasks]}
        encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",",":")); digest = hashlib.sha256(encoded.encode()).hexdigest()
        existing = self.repository.query_one("SELECT * FROM plan_revisions WHERE issue_id=? AND canonical_hash=?", (issue_id,digest))
        if existing: return existing
        revision = int(self.repository.query_one("SELECT coalesce(max(revision),0)+1 AS n FROM plan_revisions WHERE issue_id=?", (issue_id,))["n"])
        plan_id, now = str(uuid.uuid4()), utc_now(); self.repository.execute("INSERT INTO plan_revisions VALUES(?,?,?,?,?,?,?)", (plan_id,issue_id,revision,"PROPOSED",digest,encoded,now))
        self._schedule(project["id"],"PUBLISH_PLAN","plan",plan_id,f"publish-plan:{plan_id}",45)
        return self.repository.query_one("SELECT * FROM plan_revisions WHERE id=?", (plan_id,)) or {}

    def publish_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.repository.query_one("SELECT p.*,i.issue_number,i.project_id,r.canonical_name FROM plan_revisions p JOIN issue_workflows i ON i.id=p.issue_id JOIN projects pr ON pr.id=i.project_id JOIN github_repositories r ON r.id=pr.repository_id WHERE p.id=?", (plan_id,))
        if not plan: raise NotFoundError("plan no encontrado")
        publication = self.repository.query_one("SELECT * FROM workflow_publications WHERE plan_revision_id=?", (plan_id,))
        if publication and publication["status"] == "SUCCEEDED": return publication
        body = self._render_plan(plan); key = f"github:plan-comment:{plan_id}"; operation = self._external("GITHUB_COMMENT","plan",plan_id,key,body)
        token, _ = self.identity.credential_for_internal_use()
        for comment in self.api_factory().comments(token,str(plan["canonical_name"]),int(plan["issue_number"])):
            if f"{MARKER}{plan_id} -->" in str(comment.get("body") or ""):
                comment_id=int(comment["id"]);now=utc_now();self._external_status(operation,"SUCCEEDED",str(comment_id))
                if not publication:self.repository.execute("INSERT INTO workflow_publications VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),plan_id,operation,comment_id,"SUCCEEDED",now,now))
                return self.repository.query_one("SELECT * FROM workflow_publications WHERE plan_revision_id=?",(plan_id,)) or {}
        try:
            response = self.api_factory().post_comment(token,str(plan["canonical_name"]),int(plan["issue_number"]),body)
        except Exception:
            self._external_status(operation,"UNKNOWN"); self._barrier(f"publicación de plan incierta {operation}"); raise
        comment_id = int(response["id"]); now=utc_now(); self._external_status(operation,"SUCCEEDED",str(comment_id))
        if publication: self.repository.execute("UPDATE workflow_publications SET github_comment_id=?,status='SUCCEEDED',updated_at=? WHERE id=?", (comment_id,now,publication["id"]))
        else: self.repository.execute("INSERT INTO workflow_publications VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()),plan_id,operation,comment_id,"SUCCEEDED",now,now))
        return self.repository.query_one("SELECT * FROM workflow_publications WHERE plan_revision_id=?", (plan_id,)) or {}

    def execute_task(self, task_id: str) -> dict[str, Any]:
        task=self.repository.query_one("SELECT t.*,p.issue_id,i.project_id,i.issue_number,pr.base_branch,pr.remote_sha,r.canonical_name FROM tasks t JOIN plan_revisions p ON p.id=t.plan_revision_id JOIN issue_workflows i ON i.id=p.issue_id JOIN projects pr ON pr.id=i.project_id JOIN github_repositories r ON r.id=pr.repository_id WHERE t.id=?",(task_id,))
        if not task:raise NotFoundError("tarea no encontrada")
        blocked=self.repository.query_one("SELECT 1 FROM task_dependencies d JOIN tasks t ON t.id=d.depends_on_task_id WHERE d.task_id=? AND t.status NOT IN ('COMPLETED','MERGED')",(task_id,))
        if blocked:raise ConflictError("dependencias de tarea incompletas")
        existing=self.repository.query_one("SELECT * FROM task_runs WHERE task_id=?",(task_id,))
        if existing and existing["status"] in {"SUCCEEDED","UNKNOWN"}:return existing
        project_workspace=self.repository.query_one("SELECT * FROM workspaces WHERE project_id=? AND purpose='PROJECT'",(task["project_id"],))
        if not project_workspace:raise OperationError("workspace base no disponible")
        branch=self._task_branch(int(task["issue_number"]),str(task["logical_id"])); workspace_id=str(uuid.uuid5(uuid.NAMESPACE_URL,f"task-workspace:{task_id}")); root=Path(str(project_workspace["path"])).parent; destination=root/workspace_id
        if destination.parent!=root or destination.is_symlink():raise OperationError("ruta de tarea insegura")
        run_id=str(existing["id"]) if existing else str(uuid.uuid4());now=utc_now()
        if not existing:self.repository.execute("INSERT INTO task_runs VALUES(?,?,NULL,NULL,NULL,?,?,NULL,'PREPARING',?,?)",(run_id,task_id,branch,task["remote_sha"],now,now))
        if not destination.exists():
            result=subprocess.run(("git","clone","--no-hardlinks","--no-checkout","--",str(project_workspace["path"]),str(destination)),text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=False,shell=False,env={"PATH":"/usr/bin:/bin","LANG":"C","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null"})
            if result.returncode:raise OperationError("no se pudo crear el workspace de tarea")
            self._git(destination,"checkout","--detach",str(task["remote_sha"]));self._git(destination,"switch","-c",branch,str(task["remote_sha"]));self._git(destination,"remote","set-url","origin",f"https://github.com/{task['canonical_name']}.git")
        if not self.repository.query_one("SELECT 1 FROM workspaces WHERE id=?",(workspace_id,)):
            self.repository.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,?,?,?,?)",(workspace_id,task["project_id"],"TASK",str(destination),task["base_branch"],task["remote_sha"],"READY",now,now))
        worker=self._ready_worker(); prompt=f"Implementa exclusivamente la tarea {task['logical_id']}: {task['description']}. Criterios: {task['acceptance_json']}. Trabaja en el workspace asignado. No uses GitHub, no hagas push ni merge."
        session=self.workers.create_session(str(task["project_id"]),str(task["agent_id"]),"TASK",hashlib.sha256(prompt.encode()).hexdigest(),str(worker["id"]),workspace_id)
        self.repository.execute("UPDATE task_runs SET workspace_id=?,session_id=?,status='RUNNING',updated_at=? WHERE id=?",(workspace_id,session["id"],utc_now(),run_id));self.repository.execute("UPDATE tasks SET status='RUNNING',updated_at=? WHERE id=?",(utc_now(),task_id))
        execution=self.workers.execute(str(session["id"]),f"task:{task_id}",prompt)
        self.repository.execute("UPDATE task_runs SET execution_id=?,updated_at=? WHERE id=?",(execution["id"],utc_now(),run_id))
        if execution.get("outcome")=="UNKNOWN":
            self.repository.execute("UPDATE task_runs SET status='UNKNOWN',updated_at=? WHERE id=?",(utc_now(),run_id));self.repository.execute("UPDATE tasks SET status='BLOCKED',updated_at=? WHERE id=?",(utc_now(),task_id));self._barrier(f"ejecución de tarea incierta {execution['id']}");return self.repository.query_one("SELECT * FROM task_runs WHERE id=?",(run_id,)) or {}
        if execution.get("outcome")!="SUCCEEDED":
            self.repository.execute("UPDATE task_runs SET status='FAILED',updated_at=? WHERE id=?",(utc_now(),run_id));self.repository.execute("UPDATE tasks SET status='FAILED',updated_at=? WHERE id=?",(utc_now(),task_id));raise OperationError("la ejecución de tarea falló")
        status=self._git(destination,"status","--porcelain=v1","--untracked-files=all").strip()
        if not status:raise OperationError("la tarea finalizó sin producir cambios")
        self._git(destination,"add","--all",":/");self._git(destination,"-c","user.name=vibecoder","-c","user.email=vibecoder@localhost","-c","core.hooksPath=/dev/null","commit","--no-gpg-sign","--no-verify","-m",f"feat: {task['logical_id']}")
        sha=self._git(destination,"rev-parse","HEAD").strip();token,_=self.identity.credential_for_internal_use();push_op=self._external("GIT_PUSH","task",task_id,f"push:{task_id}:{sha}",sha)
        try:self._push(destination,branch,token)
        except Exception:self._external_status(push_op,"UNKNOWN");self._barrier(f"push incierto {push_op}");raise
        self._external_status(push_op,"SUCCEEDED",sha);pr_op=self._external("GITHUB_PULL_REQUEST","task",task_id,f"pr:{task_id}",sha)
        try:
            remote=self.api_factory().find_pull_request(token,str(task["canonical_name"]),head=branch,base=str(task["base_branch"]))
            if remote is None:remote=self.api_factory().create_pull_request(token,str(task["canonical_name"]),title=f"{task['logical_id']}: {task['description'][:120]}",head=branch,base=str(task["base_branch"]),body=f"Closes #{task['issue_number']}\n\nGenerado por vibecoder; revisión y merge exclusivamente humanos.")
        except Exception:self._external_status(pr_op,"UNKNOWN");self._barrier(f"creación de PR incierta {pr_op}");raise
        self._external_status(pr_op,"SUCCEEDED",str(remote["id"]));prid=str(uuid.uuid4());done=utc_now();self.repository.execute("INSERT INTO pull_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",(prid,task_id,int(remote["id"]),int(remote["number"]),branch,task["base_branch"],sha,"OPEN",None,done,done));self.repository.execute("UPDATE task_runs SET result_sha=?,status='SUCCEEDED',updated_at=? WHERE id=?",(sha,done,run_id));self.repository.execute("UPDATE tasks SET status='COMPLETED',updated_at=? WHERE id=?",(done,task_id))
        for dependent in self.repository.query_all("SELECT t.id FROM tasks t JOIN task_dependencies d ON d.task_id=t.id WHERE d.depends_on_task_id=? AND t.status='PENDING' AND NOT EXISTS(SELECT 1 FROM task_dependencies x JOIN tasks prerequisite ON prerequisite.id=x.depends_on_task_id WHERE x.task_id=t.id AND prerequisite.status NOT IN ('COMPLETED','MERGED'))",(task_id,)):
            self._schedule(str(task["project_id"]),"EXECUTE_TASK","task",str(dependent["id"]),f"execute-task:{dependent['id']}",20)
        return self.repository.query_one("SELECT * FROM task_runs WHERE id=?",(run_id,)) or {}

    def apply_correction(self, review_id: str) -> dict[str, Any]:
        # Review text is already authorized and filtered by poll(). Reuse the
        # original task session/workspace, never an untrusted GitHub identity.
        row=self.repository.query_one("SELECT rv.*,pr.task_id,pr.head_ref,tr.session_id,tr.workspace_id,tr.result_sha,w.path FROM review_events rv JOIN pull_requests pr ON pr.id=rv.pull_request_id JOIN task_runs tr ON tr.task_id=pr.task_id JOIN workspaces w ON w.id=tr.workspace_id WHERE rv.id=?",(review_id,))
        if not row:raise NotFoundError("revisión no encontrada")
        existing=self.repository.query_one("SELECT * FROM corrections WHERE source_review_id=?",(review_id,))
        if existing and existing["status"]=="SUCCEEDED":return existing
        execution=self.workers.execute(str(row["session_id"]),f"correction:{review_id}",f"Aplica únicamente este feedback autorizado de revisión: {row['body']}. No publiques ni hagas merge.")
        if execution.get("outcome")=="UNKNOWN":self._barrier(f"corrección incierta {execution['id']}");raise ConflictError("corrección UNKNOWN")
        if execution.get("outcome")!="SUCCEEDED":raise OperationError("corrección fallida")
        path=Path(str(row["path"]));self._git(path,"add","--all",":/");self._git(path,"-c","user.name=vibecoder","-c","user.email=vibecoder@localhost","-c","core.hooksPath=/dev/null","commit","--no-gpg-sign","--no-verify","-m","fix: address review feedback");sha=self._git(path,"rev-parse","HEAD").strip();token,_=self.identity.credential_for_internal_use();op=self._external("GIT_PUSH","review",review_id,f"correction-push:{review_id}:{sha}",sha)
        try:self._push(path,str(row["head_ref"]),token)
        except Exception:self._external_status(op,"UNKNOWN");self._barrier(f"push de corrección incierto {op}");raise
        self._external_status(op,"SUCCEEDED",sha);now=utc_now()
        if existing:self.repository.execute("UPDATE corrections SET status='SUCCEEDED',new_sha=?,updated_at=? WHERE id=?",(sha,now,existing["id"]))
        else:self.repository.execute("INSERT INTO corrections VALUES(?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),row["pull_request_id"],review_id,"AUTHORIZED_REVIEW","SUCCEEDED",row["result_sha"],sha,now,now))
        return self.repository.query_one("SELECT * FROM corrections WHERE source_review_id=?",(review_id,)) or {}

    def approve_plan(self, issue_id: str, digest: str, github_user_id: int, remote_event_id: str) -> int:
        plan = self.repository.query_one("SELECT * FROM plan_revisions WHERE issue_id=? AND canonical_hash=?", (issue_id,digest))
        if not plan: return 0
        if self.repository.query_one("SELECT 1 FROM approvals WHERE remote_event_id=?", (remote_event_id,)): return 0
        now=utc_now(); self.repository.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)", (str(uuid.uuid4()),plan["id"],digest,github_user_id,remote_event_id,now))
        self._materialize_tasks(plan)
        issue=self._issue(issue_id); self.repository.execute("UPDATE issue_workflows SET status='APPROVED',updated_at=? WHERE id=?", (now,issue_id))
        for task in self.repository.query_all("SELECT t.id,t.logical_id FROM tasks t WHERE t.plan_revision_id=? AND NOT EXISTS(SELECT 1 FROM task_dependencies d WHERE d.task_id=t.id)", (plan["id"],)):
            self._schedule(str(issue["project_id"]),"EXECUTE_TASK","task",str(task["id"]),f"execute-task:{task['id']}",20)
        return 1

    def reconcile_unknown(self) -> dict[str,int]:
        token,_=self.identity.credential_for_internal_use();api=self.api_factory();confirmed=0
        for operation in self.repository.query_all("SELECT * FROM external_operations WHERE status='UNKNOWN' ORDER BY created_at"):
            if operation["kind"]=="GITHUB_COMMENT":
                plan=self.repository.query_one("SELECT p.id,i.issue_number,r.canonical_name FROM plan_revisions p JOIN issue_workflows i ON i.id=p.issue_id JOIN projects pr ON pr.id=i.project_id JOIN github_repositories r ON r.id=pr.repository_id WHERE p.id=?",(operation["source_id"],))
                if plan:
                    for comment in api.comments(token,str(plan["canonical_name"]),int(plan["issue_number"])):
                        if f"{MARKER}{plan['id']} -->" in str(comment.get("body") or ""):
                            self._external_status(str(operation["id"]),"SUCCEEDED",str(comment["id"]));confirmed+=1;break
            elif operation["kind"] in {"GIT_PUSH","GITHUB_PULL_REQUEST"} and operation["source_type"]=="task":
                task=self.repository.query_one("SELECT tr.branch_name,tr.result_sha,t.id,i.issue_number,pr.base_branch,r.canonical_name FROM task_runs tr JOIN tasks t ON t.id=tr.task_id JOIN plan_revisions p ON p.id=t.plan_revision_id JOIN issue_workflows i ON i.id=p.issue_id JOIN projects pr ON pr.id=i.project_id JOIN github_repositories r ON r.id=pr.repository_id WHERE t.id=?",(operation["source_id"],))
                if not task:continue
                if operation["kind"]=="GIT_PUSH" and api.branch_sha(token,str(task["canonical_name"]),str(task["branch_name"]))==task["result_sha"]:
                    self._external_status(str(operation["id"]),"SUCCEEDED",str(task["result_sha"]));confirmed+=1
                if operation["kind"]=="GITHUB_PULL_REQUEST":
                    remote=api.find_pull_request(token,str(task["canonical_name"]),head=str(task["branch_name"]),base=str(task["base_branch"]))
                    if remote:
                        self._external_status(str(operation["id"]),"SUCCEEDED",str(remote["id"]));confirmed+=1
                        if not self.repository.query_one("SELECT 1 FROM pull_requests WHERE task_id=?",(task["id"],)):
                            now=utc_now();self.repository.execute("INSERT INTO pull_requests VALUES(?,?,?,?,?,?,?,?,?,?,?)",(str(uuid.uuid4()),task["id"],int(remote["id"]),int(remote["number"]),task["branch_name"],task["base_branch"],task["result_sha"],"OPEN",None,now,now))
        return {"confirmed":confirmed}

    def _materialize_tasks(self, plan: Mapping[str, Any]) -> None:
        tasks=self._validate_plan(json.loads(str(plan["plan_json"]))); ids={t.logical_id:str(uuid.uuid5(uuid.UUID(str(plan["id"])),t.logical_id)) for t in tasks}; now=utc_now()
        with self.repository.transaction():
            for task in tasks:
                agent=self._ensure_agent(self._issue(str(plan["issue_id"]))["project_id"],task.agent,task.agent)
                self.repository.execute("INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?,?,?,?)", (ids[task.logical_id],plan["id"],task.logical_id,agent,task.description,json.dumps(task.acceptance),"PENDING",now,now))
            for task in tasks:
                for dependency in task.dependencies: self.repository.execute("INSERT OR IGNORE INTO task_dependencies VALUES(?,?)", (ids[task.logical_id],ids[dependency]))

    def _poll_pull_requests(self, project, token, api, authorized) -> int:
        merged=0
        for row in self.repository.query_all("SELECT pr.*,t.plan_revision_id FROM pull_requests pr JOIN tasks t ON t.id=pr.task_id JOIN plan_revisions p ON p.id=t.plan_revision_id JOIN issue_workflows i ON i.id=p.issue_id WHERE i.project_id=?", (project["id"],)):
            remote=api.pull_request(token,project["canonical_name"],int(row["number"]))
            if bool(remote.get("merged")) and not self.repository.query_one("SELECT 1 FROM verified_merges WHERE pull_request_id=?", (row["id"],)):
                sha=str(remote.get("merge_commit_sha") or ""); self.repository.execute("INSERT INTO verified_merges VALUES(?,?,?,?,?)", (str(uuid.uuid4()),row["id"],sha,"HUMAN",utc_now())); self.repository.execute("UPDATE pull_requests SET status='MERGED',merge_commit_sha=?,updated_at=? WHERE id=?", (sha,utc_now(),row["id"])); merged+=1
            incoming=list(api.reviews(token,project["canonical_name"],int(row["number"])))+list(api.comments(token,project["canonical_name"],int(row["number"])))
            for review in incoming:
                rid=str(review["id"]); author=int(review["user"]["id"]); created=str(review.get("submitted_at") or review.get("created_at") or utc_now())
                kind="PR_REVIEW_COMMENT" if review.get("pull_request_review_id") else "PR_REVIEW" if "state" in review else "PR_COMMENT"
                if author not in authorized: self._discard(project["id"],kind,rid,author,created,"UNAUTHORIZED_AUTHOR"); continue
                if self.repository.query_one("SELECT 1 FROM review_events WHERE remote_kind=? AND remote_id=?", (kind,rid)): continue
                body=str(review.get("body") or ""); review_id=str(uuid.uuid4());self.repository.execute("INSERT INTO review_events VALUES(?,?,?,?,?,?,?,?)", (review_id,row["id"],kind,rid,author,body,review.get("state"),created))
                if body.strip():self._schedule(project["id"],"APPLY_CORRECTION","review",review_id,f"correction:{review_id}",15)
        return merged

    def _validate_plan(self, value: Any) -> tuple[PlanTask,...]:
        if not isinstance(value,dict) or not isinstance(value.get("tasks"),list) or not value["tasks"]: raise ValidationError("plan estructurado inválido")
        result=[]; names=set()
        for raw in value["tasks"]:
            if not isinstance(raw,dict) or set(raw)!={"id","title","description","agent","dependencies","acceptance"}: raise ValidationError("tarea de plan inválida")
            logical=str(raw["id"]); deps=tuple(str(x) for x in raw["dependencies"]); acceptance=tuple(str(x) for x in raw["acceptance"])
            if not LOGICAL_ID.fullmatch(logical) or logical in names or not isinstance(raw["dependencies"],list) or not isinstance(raw["acceptance"],list): raise ValidationError("identificador o campos de tarea inválidos")
            names.add(logical); result.append(PlanTask(logical,str(raw["title"]),str(raw["description"]),str(raw["agent"]),deps,acceptance))
        if any(d not in names for t in result for d in t.dependencies): raise ValidationError("dependencia inexistente")
        visiting=set(); done=set(); by={t.logical_id:t for t in result}
        def visit(name):
            if name in visiting: raise ValidationError("el DAG contiene un ciclo")
            if name in done:return
            visiting.add(name)
            for dep in by[name].dependencies:visit(dep)
            visiting.remove(name);done.add(name)
        for name in names:visit(name)
        return tuple(result)

    def _project(self, project_id):
        row=self.repository.query_one("SELECT p.*,r.canonical_name FROM projects p JOIN github_repositories r ON r.id=p.repository_id WHERE p.id=?",(project_id,))
        if not row:raise NotFoundError("proyecto no encontrado")
        return row
    def _issue(self, issue_id):
        row=self.repository.query_one("SELECT * FROM issue_workflows WHERE id=?",(issue_id,))
        if not row:raise NotFoundError("issue no encontrado")
        return row
    def _message(self, issue,kind,remote,author,body,created):
        if self.repository.query_one("SELECT 1 FROM authorized_messages WHERE remote_kind=? AND remote_id=?",(kind,remote)):return 0
        self.repository.execute("INSERT INTO authorized_messages VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),issue,kind,remote,author,body,created));return 1
    def _discard(self, project,kind,remote,author,created,reason):
        if self.repository.query_one("SELECT 1 FROM discarded_events WHERE remote_kind=? AND remote_id=?",(kind,remote)):return 0
        self.repository.execute("INSERT INTO discarded_events VALUES(?,?,?,?,?,?,?)",(str(uuid.uuid4()),project,kind,remote,author,created,reason));return 1
    def _ensure_agent(self, project_id,name,role):
        row=self.repository.query_one("SELECT id FROM agents WHERE project_id=? AND name=? COLLATE NOCASE",(project_id,name))
        if row:return str(row["id"])
        aid=str(uuid.uuid4());rid=str(uuid.uuid4());now=utc_now();instructions=f"Actúa como agente {name} ({role}) del proyecto. Limítate al workspace asignado."
        with self.repository.transaction():
            self.repository.execute("INSERT INTO agents VALUES(?,?,?,?,?,?,?,?,?)",(aid,project_id,name,role,"ACTIVE",1,None,now,now))
            self.repository.execute("INSERT INTO agent_revisions VALUES(?,?,?,?,?,?,?,?,?)",(rid,aid,1,json.dumps([role]),"[]",json.dumps(["workspace_only","no_github"]),instructions,hashlib.sha256(instructions.encode()).hexdigest(),now))
        return aid
    def _schedule(self,project,kind,source_type,source_id,key,priority):
        now=utc_now();self.repository.execute("INSERT OR IGNORE INTO scheduled_jobs VALUES(?,?,?,?,?,'PENDING',?,?,0,?,?,?)",(str(uuid.uuid4()),project,kind,source_type,source_id,priority,now,key,now,now))
    def _external(self,kind,source_type,source_id,key,payload):
        row=self.repository.query_one("SELECT id FROM external_operations WHERE idempotency_key=?",(key,))
        if row:return str(row["id"])
        oid=str(uuid.uuid4());now=utc_now();self.repository.execute("INSERT INTO external_operations VALUES(?,?,?,?,?,?,'INTENDED',NULL,'{}',?,?)",(oid,kind,source_type,source_id,key,hashlib.sha256(payload.encode()).hexdigest(),now,now));return oid
    def _external_status(self,oid,status,remote=None):self.repository.execute("UPDATE external_operations SET status=?,remote_reference=?,updated_at=? WHERE id=?",(status,remote,utc_now(),oid))
    def _barrier(self,reason):self.repository.execute("INSERT INTO recovery_barriers VALUES(?,?, 'ACTIVE',?,NULL,NULL)",(str(uuid.uuid4()),reason,utc_now()))
    def _render_plan(self,plan):
        data=json.loads(str(plan["plan_json"]));lines=[f"## Plan vibecoder — revisión {plan['revision']}","",f"Hash: `{plan['canonical_hash']}`",""]
        for task in data["tasks"]:lines += [f"- **{task['id']} — {task['title']}**: {task['description']}"]
        lines += ["",f"Para aprobar exactamente esta revisión:",f"`/vibecoder approve {plan['canonical_hash']}`","",f"{MARKER}{plan['id']} -->"]
        return "\n".join(lines)
    def _ready_worker(self):
        rows=self.workers.list(); ready=sorted((x for x in rows if x["status"]=="READY" and int(x["active_executions"])<int(x["concurrency_limit"])),key=lambda x:(str(x["name"]).casefold(),str(x["id"])))
        if not ready:raise ConflictError("no hay un worker READY disponible")
        return ready[0]
    def _task_branch(self,issue,logical):
        value=f"vibecoder/issue-{issue}/{logical}";return value[:120]
    def _git(self,path,*args):
        result=subprocess.run(("git",*args),cwd=path,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=False,shell=False,env={"PATH":"/usr/bin:/bin","LANG":"C","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null","GIT_TERMINAL_PROMPT":"0"})
        if result.returncode:raise OperationError("operación Git local fallida")
        return result.stdout
    def _push(self,path,branch,token):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="askpass-") as directory:
            helper=Path(directory)/"askpass";helper.write_text("#!/bin/sh\ncase \"$1\" in *Username*) printf '%s\\n' x-access-token;; *) printf '%s\\n' \"$VIBECODER_GIT_TOKEN\";; esac\n");helper.chmod(0o700)
            env={"PATH":"/usr/bin:/bin","LANG":"C","GIT_CONFIG_NOSYSTEM":"1","GIT_CONFIG_GLOBAL":"/dev/null","GIT_TERMINAL_PROMPT":"0","GIT_ASKPASS":str(helper),"VIBECODER_GIT_TOKEN":token}
            result=subprocess.run(("git","-c","credential.helper=","-c","core.hooksPath=/dev/null","push","--porcelain","--no-verify","origin",f"{branch}:refs/heads/{branch}"),cwd=path,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120,check=False,shell=False,env=env)
            if result.returncode:raise OperationError("push Git fallido")

