from __future__ import annotations
import json
import tempfile
import unittest
import subprocess
from pathlib import Path

from vibecoder.maintenance_v1 import MaintenanceService
from vibecoder.scheduler_v1 import PersistentScheduler
from vibecoder.sqlite import SQLiteRepository
from vibecoder.workflows_v1 import WorkflowService


class Identity:
    def credential_for_internal_use(self): return "secret-test-token", {"github_user_id":7,"login":"owner"}

class API:
    def __init__(self): self.posted=[]
    def issues(self, token, repository):
        return [{"id":101,"number":1,"user":{"id":7},"body":"Build it","created_at":"2026-01-01T00:00:00+00:00"},
                {"id":102,"number":2,"user":{"id":99},"body":"SECRET UNTRUSTED","created_at":"2026-01-01T00:00:00+00:00"}]
    def comments(self, token, repository, number): return []
    def post_comment(self, token, repository, issue_number, body): self.posted.append(body); return {"id":501}
    def create_pull_request(self,*a,**kw): return {"id":900,"number":3}
    def find_pull_request(self,*a,**kw): return None
    def pull_request(self,*a,**kw): return {"merged":False}
    def reviews(self,*a,**kw): return []
    def branch_sha(self,*a,**kw): return None

class Workers:
    def __init__(self, repo): self.repo=repo;self.commit_changes=False
    def list(self): return [{"id":"worker","name":"fake-1","status":"READY","active_executions":0,"concurrency_limit":1}]
    def create_session(self,project_id,agent_id,purpose,context_hash,worker_query,workspace_id=None):
        now="2026-01-01T00:00:00+00:00";sid="session-"+purpose.lower()
        self.repo.execute("INSERT OR IGNORE INTO plugins VALUES('fake','1.0','test','{}',?)",(now,))
        self.repo.execute("INSERT OR IGNORE INTO workers VALUES('worker','fake-1','fake','1.0','READY','{}','/tmp/fake-worker',1,0,NULL,NULL,?,?)",(now,now))
        self.repo.execute("INSERT OR IGNORE INTO provider_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(sid,project_id,agent_id,"worker",workspace_id,purpose,context_hash,"opaque","ACTIVE","{}",now,now));return {"id":sid}
    def execute(self,session_id,key,instructions,**kwargs):
        session=self.repo.query_one("SELECT workspace_id FROM provider_sessions WHERE id=?",(session_id,))
        if session and session["workspace_id"]:
            workspace=self.repo.query_one("SELECT path FROM workspaces WHERE id=?",(session["workspace_id"],))
            path=Path(workspace["path"]);(path/"generated.txt").write_text("generated\n")
            if self.commit_changes:
                subprocess.run(("git","-C",str(path),"add","generated.txt"),check=True)
                subprocess.run(("git","-C",str(path),"-c","user.name=agent","-c","user.email=agent@example.invalid","commit","-m","provider commit"),check=True,stdout=subprocess.DEVNULL)
        plan={"tasks":[{"id":"implement","title":"Implement","description":"Implement feature","agent":"implementer","dependencies":[],"acceptance":["tests pass"]}]}
        execution="execution-"+key.replace(":","-");now="2026-01-01T00:00:00+00:00"
        self.repo.execute("INSERT OR IGNORE INTO provider_executions VALUES(?,?, 'worker',?,'FINISHED','SUCCEEDED',NULL,NULL,?,'{}',?,?)",(execution,session_id,key,json.dumps({"text":json.dumps(plan)}),now,now))
        return {"id":execution,"outcome":"SUCCEEDED","result_json":json.dumps({"text":json.dumps(plan)})}

class OfflineWorkflow(WorkflowService):
    def _push(self,path,branch,token): return None

class M06WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name);self.repo=SQLiteRepository(root/"state"/"db.sqlite")
        now="2026-01-01T00:00:00+00:00";self.repo.execute("INSERT INTO installation VALUES('install',?,?)",(now,now))
        self.repo.execute("INSERT INTO secret_metadata VALUES('secret','GITHUB_PAT','opaque','ACTIVE',?,?)",(now,now))
        self.repo.execute("INSERT INTO github_identity VALUES(1,7,'owner','secret','ONLINE',?,?,'{}',0,NULL)",(now,now))
        self.repo.execute("INSERT INTO github_repositories VALUES('repo',1,NULL,'owner','demo','owner/demo','main',?,?)",(now,now))
        self.repo.execute("INSERT INTO projects VALUES('project','demo','repo','main',?,'ENABLED',?,?)",("a"*40,now,now))
        self.repo.execute("INSERT INTO project_authorized_users VALUES('project',7,'owner',?,NULL)",(now,))
        self.api=API();self.service=OfflineWorkflow(self.repo,Identity(),Workers(self.repo),lambda:self.api)
    def tearDown(self): self.repo.close();self.temp.cleanup()
    def test_filter_plan_publish_and_exact_approval(self):
        result=self.service.poll("project");self.assertEqual(result["issues"],1)
        self.assertEqual(self.repo.query_one("SELECT count(*) n FROM discarded_events")["n"],1)
        self.assertNotIn("SECRET UNTRUSTED",json.dumps(self.repo.query_all("SELECT body FROM authorized_messages")))
        issue=self.repo.query_one("SELECT id FROM issue_workflows");plan=self.service.generate_plan(issue["id"])
        publication=self.service.publish_plan(plan["id"]);self.assertEqual(publication["status"],"SUCCEEDED")
        self.assertEqual(self.service.approve_plan(issue["id"],plan["canonical_hash"],7,"comment-approve"),1)
        self.assertEqual(self.repo.query_one("SELECT count(*) n FROM tasks")["n"],1)
    def test_scheduler_persists_and_obeys_pause(self):
        scheduler=PersistentScheduler(self.repo,self.service);scheduler.ensure_poll_jobs();self.assertIsNotNone(scheduler.cycle())
        now="2026-01-01T00:00:00+00:00";self.repo.execute("INSERT INTO operational_pauses VALUES('p','GLOBAL',NULL,'ALL','test','test',?,NULL)",(now,))
        self.assertIsNone(scheduler.cycle())
    def test_plan_task_ids_are_canonicalized_without_losing_dependencies(self):
        value={"tasks":[
            {"id":"T1","title":"One","description":"First","agent":"main","dependencies":[],"acceptance":["ok"]},
            {"id":"T2","title":"Two","description":"Second","agent":"main","dependencies":["T1"],"acceptance":["ok"]},
        ]}
        tasks=self.service._validate_plan(value)
        self.assertEqual([task.logical_id for task in tasks],["t1","t2"])
        self.assertEqual(tasks[1].dependencies,("t1",))
    def test_explicit_plan_retry_reschedules_unpublished_existing_revision(self):
        self.service.poll("project")
        issue=self.repo.query_one("SELECT id FROM issue_workflows")
        plan=self.service.generate_plan(issue["id"])
        before=self.repo.query_one("SELECT count(*) n FROM scheduled_jobs WHERE kind='PUBLISH_PLAN'")["n"]
        same=self.service.generate_plan(issue["id"])
        after=self.repo.query_one("SELECT count(*) n FROM scheduled_jobs WHERE kind='PUBLISH_PLAN'")["n"]
        self.assertEqual(same["id"],plan["id"])
        self.assertEqual(after,before+1)
    def test_plan_publication_marker_is_not_agent_input(self):
        self.api.comments=lambda token,repository,number:[{"id":700,"user":{"id":7},"body":f"generated {__import__('vibecoder.workflows_v1',fromlist=['MARKER']).MARKER}opaque -->","created_at":"2026-01-01T00:00:01+00:00"}]
        self.service.poll("project")
        bodies=[row["body"] for row in self.repo.query_all("SELECT body FROM authorized_messages")]
        self.assertEqual(bodies,["Build it"])
    def test_partial_approval_is_recovered_without_second_approval_row(self):
        self.service.poll("project")
        issue=self.repo.query_one("SELECT id FROM issue_workflows")
        plan=self.service.generate_plan(issue["id"])
        self.repo.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?)",("partial",plan["id"],plan["canonical_hash"],7,"old-event","2026-01-01T00:00:00+00:00"))
        self.assertEqual(self.service.approve_plan(issue["id"],plan["canonical_hash"],7,"new-event"),1)
        self.assertEqual(self.repo.query_one("SELECT count(*) n FROM approvals")["n"],1)
        self.assertEqual(self.repo.query_one("SELECT status FROM issue_workflows WHERE id=?",(issue["id"],))["status"],"APPROVED")
        self.assertGreater(self.repo.query_one("SELECT count(*) n FROM tasks")["n"],0)
    def test_task_creates_commit_and_pull_request_without_giving_pat_to_worker(self):
        result=self.service.poll("project");issue=self.repo.query_one("SELECT id FROM issue_workflows");plan=self.service.generate_plan(issue["id"]);self.service.approve_plan(issue["id"],plan["canonical_hash"],7,"approval")
        base=Path(self.temp.name)/"state"/"workspaces"/"project";base.mkdir(parents=True)
        subprocess.run(("git","init","-b","main",str(base)),check=True,stdout=subprocess.DEVNULL)
        (base/"README.md").write_text("demo\n");subprocess.run(("git","-C",str(base),"add","README.md"),check=True);subprocess.run(("git","-C",str(base),"-c","user.name=test","-c","user.email=test@example.invalid","commit","-m","initial"),check=True,stdout=subprocess.DEVNULL)
        sha=subprocess.check_output(("git","-C",str(base),"rev-parse","HEAD"),text=True).strip();self.repo.execute("UPDATE projects SET remote_sha=? WHERE id='project'",(sha,));self.repo.execute("INSERT INTO workspaces VALUES('base','project','PROJECT',?,'main',?,'READY',?,?)",(str(base),sha,"2026-01-01T00:00:00+00:00","2026-01-01T00:00:00+00:00"))
        task=self.repo.query_one("SELECT id FROM tasks");run=self.service.execute_task(task["id"]);self.assertEqual(run["status"],"SUCCEEDED");self.assertEqual(self.repo.query_one("SELECT count(*) n FROM pull_requests")["n"],1)

    def test_task_accepts_clean_provider_commit_descended_from_verified_base(self):
        self.service.poll("project");issue=self.repo.query_one("SELECT id FROM issue_workflows");plan=self.service.generate_plan(issue["id"]);self.service.approve_plan(issue["id"],plan["canonical_hash"],7,"approval")
        base=Path(self.temp.name)/"state"/"workspaces"/"project";base.mkdir(parents=True)
        subprocess.run(("git","init","-b","main",str(base)),check=True,stdout=subprocess.DEVNULL)
        (base/"README.md").write_text("demo\n");subprocess.run(("git","-C",str(base),"add","README.md"),check=True);subprocess.run(("git","-C",str(base),"-c","user.name=test","-c","user.email=test@example.invalid","commit","-m","initial"),check=True,stdout=subprocess.DEVNULL)
        base_sha=subprocess.check_output(("git","-C",str(base),"rev-parse","HEAD"),text=True).strip();self.repo.execute("UPDATE projects SET remote_sha=? WHERE id='project'",(base_sha,));self.repo.execute("INSERT INTO workspaces VALUES('base','project','PROJECT',?,'main',?,'READY',?,?)",(str(base),base_sha,"2026-01-01T00:00:00+00:00","2026-01-01T00:00:00+00:00"))
        self.service.workers.commit_changes=True
        task=self.repo.query_one("SELECT id FROM tasks");run=self.service.execute_task(task["id"])
        self.assertEqual(run["status"],"SUCCEEDED");self.assertNotEqual(run["result_sha"],base_sha)
        workspace=Path(self.repo.query_one("SELECT path FROM workspaces WHERE id=?",(run["workspace_id"],))["path"])
        parent=subprocess.check_output(("git","-C",str(workspace),"rev-parse",f"{run['result_sha']}^"),text=True).strip()
        self.assertEqual(parent,base_sha)

    def test_dependent_task_starts_from_verified_predecessor_result(self):
        self.service.poll("project");issue=self.repo.query_one("SELECT id FROM issue_workflows");plan=self.service.generate_plan(issue["id"]);self.service.approve_plan(issue["id"],plan["canonical_hash"],7,"approval")
        base=Path(self.temp.name)/"state"/"workspaces"/"project";base.mkdir(parents=True)
        subprocess.run(("git","init","-b","main",str(base)),check=True,stdout=subprocess.DEVNULL)
        (base/"README.md").write_text("demo\n");subprocess.run(("git","-C",str(base),"add","README.md"),check=True);subprocess.run(("git","-C",str(base),"-c","user.name=test","-c","user.email=test@example.invalid","commit","-m","initial"),check=True,stdout=subprocess.DEVNULL)
        sha=subprocess.check_output(("git","-C",str(base),"rev-parse","HEAD"),text=True).strip();self.repo.execute("UPDATE projects SET remote_sha=? WHERE id='project'",(sha,));self.repo.execute("INSERT INTO workspaces VALUES('base','project','PROJECT',?,'main',?,'READY',?,?)",(str(base),sha,"2026-01-01T00:00:00+00:00","2026-01-01T00:00:00+00:00"))
        first=self.repo.query_one("SELECT id,plan_revision_id,agent_id FROM tasks");run=self.service.execute_task(first["id"])
        second="dependent";now="2026-01-01T00:00:00+00:00";self.repo.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",(second,first["plan_revision_id"],"verify",first["agent_id"],"Verify predecessor","[]","PENDING",now,now));self.repo.execute("INSERT INTO task_dependencies VALUES(?,?)",(second,first["id"]))
        with self.assertRaisesRegex(Exception,"sin producir cambios"):
            self.service.execute_task(second)
        dependent=self.repo.query_one("SELECT tr.base_sha,w.path,tr.status FROM task_runs tr JOIN workspaces w ON w.id=tr.workspace_id WHERE tr.task_id=?",(second,))
        self.assertEqual(dependent["base_sha"],run["result_sha"])
        self.assertEqual(self.repo.query_one("SELECT status FROM tasks WHERE id=?",(second,))["status"],"FAILED")
        self.assertTrue((Path(dependent["path"])/"generated.txt").exists())

        # A pre-fix installation could leave this exact terminal mismatch.
        self.repo.execute("UPDATE tasks SET status='RUNNING' WHERE id=?",(second,));self.repo.execute("UPDATE task_runs SET status='RUNNING' WHERE task_id=?",(second,));self.repo.execute("INSERT INTO scheduled_jobs VALUES('stale','project','EXECUTE_TASK','task',?,'FAILED',20,?,1,'stale-key',?,?)",(second,now,now,now))
        reconciled=self.service.reconcile_incomplete_tasks()
        self.assertEqual(reconciled,{"changed":1,"blocked":0})
        self.assertEqual(self.repo.query_one("SELECT status FROM tasks WHERE id=?",(second,))["status"],"FAILED")
    def test_backup_verify_requires_pause(self):
        now="2026-01-01T00:00:00+00:00";self.repo.execute("INSERT INTO operational_pauses VALUES('p','GLOBAL',NULL,'ALL','test','test',?,NULL)",(now,))
        service=MaintenanceService(self.repo,Path(self.temp.name)/"state");created=service.create_backup("test");verified=service.verify(created["id"])
        self.assertTrue(verified["valid"]);self.assertEqual(verified["schema_version"],1)
        self.repo.execute("INSERT INTO audit_events VALUES('after','test','test','test',NULL,'SUCCEEDED','{}',?)",(now,))
        restored=service.restore(created["id"],"test");self.assertTrue(restored["recovery_barrier"])
        self.assertIsNone(self.repo.query_one("SELECT 1 FROM audit_events WHERE id='after'"))
        self.assertIsNotNone(self.repo.query_one("SELECT 1 FROM recovery_barriers WHERE status='ACTIVE'"))

    def test_backup_delegates_isolated_worker_storage_to_privileged_helper(self):
        now="2026-01-01T00:00:00+00:00";self.repo.execute("INSERT INTO operational_pauses VALUES('p2','GLOBAL',NULL,'ALL','test','test',?,NULL)",(now,))
        workers=Path(self.temp.name)/"state"/"workers";workers.mkdir(parents=True)
        class Helper:
            called=False
            def snapshot_workers(inner,destination):
                inner.called=True;destination.mkdir();(destination/"session.json").write_text("opaque")
        helper=Helper();service=MaintenanceService(self.repo,Path(self.temp.name)/"state",privileged_helper=helper)
        created=service.create_backup("test")
        self.assertTrue(helper.called)
        self.assertTrue(service.verify(created["id"])["valid"])

if __name__ == "__main__": unittest.main()
