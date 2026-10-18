from testing_support import cleanup_after_idle
import json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from region_service import RegionService
from nas_agent import Executor,native_hardlink,make_server
from client_api import AgentClient
from credentials import read_token_file
from recovery_store import RecoveryStore
from job_status import classify
class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.src=self.root/"src";self.src.mkdir();self.dst=self.root/"dst";self.state=self.root/"state"
        self.config={"regions":[dict(id="a",name="test",source_root=str(self.src),destination_root=str(self.dst))]}
        self.service=RegionService(self.config,self.state,{"active":True})
        self.selection=dict(region_id="a",source_relative="",dest_relative="",name_bytes=240)
    def tearDown(self):cleanup_after_idle(self,self.temp,self.service.operation_lock)
    def files(self,count=2):
        for i in range(count):(self.src/f"{i}.mp4").write_bytes(b"original")
    def submit(self):
        plan=self.service.preview_selection(self.selection)
        job=self.service.start_selection(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS"))
        return plan,job
    def wait(self,jid):
        for _ in range(300):
            job=self.service.job(jid)
            if job["done"] and not self.service.operation_lock.locked():return self.service.job(jid)
            time.sleep(.01)
        self.fail("timeout")
    def test_stop_finishes_current_then_preserves_remaining(self):
        self.files();entered=threading.Event();release=threading.Event()
        def delayed(src,dest):entered.set();release.wait(5);native_hardlink(src,dest)
        with patch("nas_agent.native_hardlink",delayed):
            plan,job=self.submit()
            try:
                self.assertTrue(entered.wait(3));live=self.service.job(job["job_id"])
                self.assertEqual(live["current_file"],"0.mp4")
                ack=self.service.stop_job(dict(job_id=job["job_id"],confirmed="STOP_AFTER_CURRENT"))
                self.assertTrue(ack["accepted"]);self.assertTrue(ack["job"]["stop_requested"])
            finally:release.set()
            final=self.wait(job["job_id"])
        self.assertEqual((final["created"],final["processed"]),(1,1))
        self.assertEqual((final["status"],final["result_class"]),("stopped","partial"))
        self.assertTrue(os.path.samefile(self.src/"0.mp4",self.dst/"0.mp4"))
        self.assertFalse((self.dst/"1.mp4").exists())
        self.assertEqual((self.src/"1.mp4").read_bytes(),b"original")
        second=self.service.preview_selection(self.selection)
        self.assertEqual(sum(r["status"]=="待创建" for r in second["rows"]),1)
    def test_stop_before_first_creates_no_media(self):
        self.files();entered=threading.Event();release=threading.Event();original=Executor._execute
        def delayed(ex,rows,job):entered.set();release.wait(5);original(ex,rows,job)
        with patch.object(Executor,"_execute",delayed):
            _,job=self.submit()
            try:
                self.assertTrue(entered.wait(3))
                self.service.stop_job(dict(job_id=job["job_id"],confirmed="STOP_AFTER_CURRENT"))
            finally:release.set()
            final=self.wait(job["job_id"])
        self.assertEqual((final["created"],final["result_class"]),(0,"none"));self.assertFalse(self.dst.exists())
    def test_stop_requested_during_last_file_still_reports_complete(self):
        self.files(1);entered=threading.Event();release=threading.Event()
        def delayed(src,dest):entered.set();release.wait(5);native_hardlink(src,dest)
        with patch("nas_agent.native_hardlink",delayed):
            _,job=self.submit()
            try:
                self.assertTrue(entered.wait(3));self.service.stop_job(dict(job_id=job["job_id"],confirmed="STOP_AFTER_CURRENT"))
            finally:release.set()
            final=self.wait(job["job_id"])
        self.assertEqual(final["result_class"],"success")
        self.assertFalse(self.service.stop_job(dict(job_id=job["job_id"],confirmed="STOP_AFTER_CURRENT"))["accepted"])
    def test_partial_failure_and_checkpoint_recovery(self):
        self.files()
        def fail_second(src,dest):
            if src.name=="1.mp4":raise OSError("injected failure")
            native_hardlink(src,dest)
        with patch("nas_agent.native_hardlink",fail_second):
            plan,job=self.submit();final=self.wait(job["job_id"])
        self.assertEqual(final["result_class"],"partial")
        restarted=RegionService(self.config,self.state,{"active":True})
        recovered=restarted.recover_job(dict(preview_id=plan["plan_id"]))
        self.assertTrue(recovered["found"]);self.assertEqual(recovered["job"]["processed"],1)
        self.assertEqual(recovered["job"]["result_class"],"partial")
        events=[json.loads(line) for line in (self.state/"history.jsonl").read_text("utf-8").splitlines()]
        self.assertTrue(any(r.get("done") and r["processed"]==1 for r in events))
    def test_restart_during_execution_is_unknown_not_success(self):
        self.files();entered=threading.Event();release=threading.Event()
        def delayed(src,dest):entered.set();release.wait(5);native_hardlink(src,dest)
        with patch("nas_agent.native_hardlink",delayed):
            plan,job=self.submit()
            try:
                self.assertTrue(entered.wait(3))
                restarted=RegionService(self.config,self.state,{"active":True})
                result=restarted.recover_job(dict(preview_id=plan["plan_id"]))
                self.assertEqual(result["job"]["result_class"],"unknown")
            finally:release.set()
            self.wait(job["job_id"])
    def test_unknown_lookup_has_no_filesystem_effect(self):
        before=list(self.root.iterdir())
        self.assertFalse(self.service.recover_job(dict(preview_id="not-submitted"))["found"])
        self.assertEqual(before,list(self.root.iterdir()))
        with self.assertRaises(ValueError):self.service.stop_job(dict(job_id="no",confirmed="yes"))
    def test_http_recover_without_execute_response_and_stop_auth(self):
        self.files(1);server=make_server(self.service,"x"*32,("127.0.0.1",0));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url="http://127.0.0.1:"+str(server.server_port);client=AgentClient(url,"x"*32)
            plan=client.call("/preview",self.selection)
            client.call("/execute",dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS"))
            recovered=client.call("/recover",dict(preview_id=plan["plan_id"]))
            self.assertTrue(recovered["found"])
            final=self.wait(recovered["job"]["job_id"])
            self.assertEqual(final["result_class"],"success")
            with self.assertRaises(RuntimeError):AgentClient(url,"wrong").call("/stop",dict(job_id=final["job_id"],confirmed="STOP_AFTER_CURRENT"))
            self.assertEqual(len(self.service.history()["jobs"]),1)
        finally:server.shutdown();server.server_close();thread.join()
class LocalCredentialTests(unittest.TestCase):
    def test_token_file_formats_and_secret_free_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/"key";token="x"*32
            for content in (token,"\ufeff"+token+"\n","# comment\nAGENT_TOKEN="+token,"export AGENT_TOKEN = '"+token+"'\nPORT=8787",token+"=="):
                file.write_text(content,encoding="utf-8")
                self.assertIn(read_token_file(file),(token,token+"=="))
            for content in ("short-secret","AGENT_TOKEN="+token+"\nAGENT_TOKEN="+token,"a"*8193,"PASSWORD="+token):
                file.write_text(content,encoding="utf-8")
                with self.assertRaises(ValueError) as error:read_token_file(file)
                self.assertNotIn(content,str(error.exception));self.assertNotIn(token,str(error.exception))
    def test_recovery_receipts_have_no_secrets_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=RecoveryStore(folder);store.remember("http://nas","preview",task_name="收藏")
            store.remember("http://nas","preview","job")
            restored=RecoveryStore(folder).latest("http://nas")
            self.assertEqual(restored["job_id"],"job");self.assertEqual(len(store.load()),1)
            self.assertNotIn("token",store.path.read_text("utf-8"))
            store.path.write_text("broken",encoding="utf-8")
            with self.assertRaises(ValueError):store.remember("http://nas","new")
            self.assertEqual(store.path.read_text("utf-8"),"broken")
    def test_result_categories(self):
        cases=[(dict(done=False),"running"),(dict(done=True,status="completed",processed=2,total=2),"success"),(dict(done=True,status="stopped",created=1),"partial"),(dict(done=True,status="failed",metadata_started=1),"partial"),(dict(done=True,status="failed"),"none"),(dict(done=True,status="unconfirmed",created=1),"unknown"),(dict(done=True,status="completed",processed=1,total=2),"unknown")]
        for job,expected in cases:self.assertEqual(classify(job)[0],expected)
    def test_gui_remembers_only_file_location_and_rechecks_rotation(self):
        import docker_client
        with tempfile.TemporaryDirectory() as folder,patch.object(docker_client,"APP",Path(folder)/"prefs"):
            file=Path(folder)/"token.txt";file.write_text("x"*32,encoding="utf-8")
            app=docker_client.Studio();app.root.withdraw()
            try:
                app.key_file.set(str(file));client,key=app.api(False)
                self.assertEqual(client.token,"x"*32);app.connected=key;app.save_client_settings()
                file.write_text("y"*32,encoding="utf-8")
                with self.assertRaises(ValueError):app.api()
                saved=(docker_client.APP/"agent_settings.json").read_text("utf-8")
                self.assertNotIn("x"*32,saved);self.assertNotIn("y"*32,saved)
                self.assertEqual(json.loads(saved)["key_file"],str(file))
                self.assertEqual(str(app.stopbtn["state"]),"disabled")
            finally:app.root.destroy()

    def test_gui_progress_stop_and_partial_result_label(self):
        import docker_client
        with tempfile.TemporaryDirectory() as folder,patch.object(docker_client,"APP",Path(folder)):
            app=docker_client.Studio();app.root.withdraw()
            try:
                app.monitoring=True;app.active_receipt={"job_id":"test"}
                job=dict(job_id="test",done=False,total=4,processed=2,created=2,status="running",current_file="author/video.mp4",error="")
                app.q.put(("job_progress",job));app.pump()
                self.assertEqual(app.progress_value.get(),50)
                self.assertIn("author/video.mp4",app.progress_text.get())
                self.assertEqual(str(app.stopbtn["state"]),"normal")
                job=dict(job,done=True,status="stopped",current_file="")
                with patch.object(docker_client.messagebox,"showwarning") as warning,patch.object(docker_client.messagebox,"showinfo") as info:
                    app.q.put(("job_finished",job));app.pump()
                    self.assertIn("部分完成",app.status.get());warning.assert_called_once();info.assert_not_called()
                self.assertEqual(str(app.stopbtn["state"]),"disabled")
            finally:app.root.destroy()
