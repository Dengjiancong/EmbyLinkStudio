from testing_support import cleanup_after_idle
import copy,json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from region_service import RegionService
from nas_agent import make_server,append_journal
from client_api import AgentClient
from client_models import PreviewState,TaskStore,choice_key
class RegionalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/"originals";self.source.mkdir()
        self.dest=self.root/"links";self.state=self.root/"state"
        self.config={"regions":[dict(id="asmr",name="ASMR 收藏",source_root=str(self.source),destination_root=str(self.dest),source_display="NAS /原件",destination_display="NAS /入口")]}
        self.service=RegionService(self.config,self.state,{"active":True})
    def tearDown(self):cleanup_after_idle(self,self.temp,self.service.operation_lock)
    def selection(self,source="",dest="",budget=240):
        return dict(region_id="asmr",source_relative=source,dest_relative=dest,name_bytes=budget)
    def file(self,rel="Top0/作者/video.mp4"):
        p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b"original-content");return p
    def execute(self,plan):
        job=self.service.start_selection(dict(plan_id=plan["plan_id"],confirmed="CREATE_HARDLINKS",selection=plan["selection"],task_name="测试任务"))
        for _ in range(300):
            result=self.service.job(job["job_id"])
            if result["done"] and not self.service.operation_lock.locked():return self.service.job(job["job_id"])
            time.sleep(.01)
        self.fail("job timeout")
    def test_verify_real_link_and_copy_are_distinguished(self):
        import shutil
        src=self.file("video.mp4");self.dest.mkdir();target=self.dest/"video.mp4"
        os.link(src,target)
        request=dict(selection=self.selection(),source_relative="video.mp4",dest_relative="video.mp4")
        result=self.service.verify(request)
        self.assertTrue(result["shared_data"]);self.assertGreaterEqual(result["source"]["links"],2)
        self.assertEqual(result["source"]["inode"],result["destination"]["inode"])
        self.assertFalse(self.state.exists())
        target.unlink();shutil.copyfile(src,target)
        self.assertFalse(self.service.verify(request)["same_file"])
    def test_verify_missing_destination_is_readonly(self):
        self.file("video.mp4")
        result=self.service.verify(dict(selection=self.selection(dest="new/deep"),source_relative="video.mp4",dest_relative="video.mp4"))
        self.assertFalse(result["destination"]["exists"]);self.assertFalse(result["shared_data"])
        self.assertFalse(self.dest.exists());self.assertFalse(self.state.exists())
    def test_verify_rejects_escape_and_busy(self):
        self.file("video.mp4")
        for rel in ("../outside","/absolute",".Trash-0/file"):
            with self.assertRaises(ValueError):
                self.service.verify(dict(selection=self.selection(),source_relative=rel,dest_relative="video.mp4"))
        self.service.operation_lock.acquire()
        try:
            with self.assertRaises(RuntimeError):self.service.verify(dict(selection=self.selection(),source_relative="video.mp4",dest_relative="video.mp4"))
        finally:self.service.operation_lock.release()
    def test_verify_http_endpoint(self):
        self.file("video.mp4")
        server=make_server(self.service,"x"*32,("127.0.0.1",0))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=AgentClient("http://127.0.0.1:"+str(server.server_port),"x"*32)
            result=client.call("/verify",dict(selection=self.selection(),source_relative="video.mp4",dest_relative="video.mp4"))
            self.assertFalse(result["shared_data"]);self.assertTrue(result["source"]["exists"])
        finally:server.shutdown();server.server_close();thread.join()
    def test_browse_and_preview_are_readonly(self):
        self.file();self.file(".Trash-0/files/deleted.mp4")
        browse=self.service.browse(dict(region_id="asmr",side="source",relative=""))
        self.assertEqual([d["name"] for d in browse["directories"]],["Top0"])
        plan=self.service.preview_selection(self.selection())
        self.assertEqual(len(plan["rows"]),1);self.assertEqual(plan["skipped_entries"][0]["reason"],"回收站，未进入目录")
        self.assertFalse(self.dest.exists());self.assertFalse(self.state.exists())
        self.assertEqual(self.service.history()["jobs"],[])
        self.assertFalse(self.state.exists())
    def test_empty_destination_browse_does_not_create(self):
        result=self.service.browse(dict(region_id="asmr",side="destination",relative="new/child"))
        self.assertFalse(result["exists"]);self.assertFalse(self.dest.exists())
    def test_path_escapes_unknown_region_and_side_denied(self):
        for rel in ("../outside","/absolute","a//b","a/./b","a/../b","a\\b",".Trash-0"):
            with self.subTest(rel=rel):
                with self.assertRaises(ValueError):self.service.browse(dict(region_id="asmr",side="source",relative=rel))
        for request in (dict(region_id="missing",side="source"),dict(region_id="asmr",side="arbitrary")):
            with self.assertRaises(ValueError):self.service.browse(request)
    def test_client_cannot_supply_roots(self):
        with self.assertRaises(ValueError):self.service.preview_selection(dict(self.selection(),source_root="/elsewhere"))
        with self.assertRaises(ValueError):self.service.browse(dict(region_id="asmr",side="source",absolute_path="/elsewhere"))
    def test_cross_region_output_cannot_overlap_any_source(self):
        other=self.root/"other";other.mkdir()
        config=copy.deepcopy(self.config)
        config["regions"].append(dict(id="other",name="其他",source_root=str(other),destination_root=str(self.source/"output")))
        with self.assertRaises(ValueError):RegionService(config,self.state)
    def test_duplicate_region_and_state_overlap_denied(self):
        config=copy.deepcopy(self.config);config["regions"].append(copy.deepcopy(config["regions"][0]))
        with self.assertRaises(ValueError):RegionService(config,self.state)
        with self.assertRaises(ValueError):RegionService(self.config,self.source/"history")
    def test_selected_subdirectories_execute_within_allowed_root(self):
        src=self.file()
        plan=self.service.preview_selection(self.selection("Top0/作者","planned/作者"))
        self.assertFalse(self.dest.exists())
        job=self.execute(plan)
        self.assertEqual(job["error"],"");self.assertTrue(os.path.samefile(src,self.dest/"planned/作者/video.mp4"))
        self.assertEqual(src.read_bytes(),b"original-content")
        again=self.service.preview_selection(self.selection("Top0/作者","planned/作者"))
        self.assertEqual(again["rows"][0]["status"],"已有硬链接")
        self.assertIn("设备号",again["rows"][0]["reason"])
    def test_changed_selection_and_missing_confirmation_denied(self):
        self.file();plan=self.service.preview_selection(self.selection())
        request=dict(plan_id=plan["plan_id"],selection=dict(plan["selection"],name_bytes=180),confirmed="CREATE_HARDLINKS")
        with self.assertRaises(ValueError):self.service.start_selection(request)
        request["selection"]=plan["selection"];request["confirmed"]="yes"
        with self.assertRaises(ValueError):self.service.start_selection(request)
        self.assertFalse(self.dest.exists())
    def test_saved_history_survives_restart(self):
        self.file();job=self.execute(self.service.preview_selection(self.selection()))
        self.assertEqual(job["status"],"completed")
        restored=RegionService(self.config,self.state)
        history=restored.history()["jobs"]
        self.assertEqual(len(history),1);self.assertEqual(history[0]["task_name"],"测试任务")
        self.assertEqual(history[0]["created"],1);self.assertEqual(history[0]["region_id"],"asmr")
        self.assertTrue(history[0]["finished_at"])
    def test_incomplete_history_not_reported_success(self):
        self.state.mkdir()
        append_journal(self.state,dict(job_id="old",done=False,status="running",created=0,total=10,started_at=1),filename="history.jsonl")
        history=self.service.history()["jobs"][0]
        self.assertEqual(history["status"],"unconfirmed");self.assertIn("重新扫描",history["error"])
    def test_conflict_reason_and_source_unchanged(self):
        src=self.file("video.mp4");self.dest.mkdir();target=self.dest/"video.mp4";target.write_bytes(b"keep-existing")
        plan=self.service.preview_selection(self.selection());row=plan["rows"][0]
        self.assertEqual(row["status"],"目标冲突");self.assertIn("不覆盖",row["reason"])
        self.assertEqual(src.read_bytes(),b"original-content");self.assertEqual(target.read_bytes(),b"keep-existing")
    def test_global_lock_prevents_concurrent_writes(self):
        self.file();first=self.service.preview_selection(self.selection());second=self.service.preview_selection(self.selection())
        entered=threading.Event();release=threading.Event()
        from nas_agent import native_hardlink
        def delayed(src,dest):
            entered.set();release.wait(5);return native_hardlink(src,dest)
        with patch("nas_agent.native_hardlink",delayed):
            job=self.service.start_selection(dict(plan_id=first["plan_id"],selection=first["selection"],confirmed="CREATE_HARDLINKS"))
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(RuntimeError):self.service.preview_selection(self.selection())
                with self.assertRaises(RuntimeError):self.service.start_selection(dict(plan_id=second["plan_id"],selection=second["selection"],confirmed="CREATE_HARDLINKS"))
            finally:release.set()
            for _ in range(300):
                result=self.service.job(job["job_id"])
                if result["done"]:break
                time.sleep(.01)
            self.assertEqual(result["error"],"")
    def test_region_http_flow_auth_browse_preview_execute_history(self):
        self.file();server=make_server(self.service,"x"*32,("127.0.0.1",0))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url="http://127.0.0.1:"+str(server.server_port);client=AgentClient(url,"x"*32)
            with self.assertRaises(RuntimeError):AgentClient(url,"wrong").call("/regions")
            self.assertEqual(client.call("/regions")["regions"][0]["id"],"asmr")
            self.assertEqual(client.call("/browse",dict(region_id="asmr",side="source",relative=""))["directories"][0]["name"],"Top0")
            plan=client.call("/preview",self.selection())
            with self.assertRaises(RuntimeError):client.call("/execute",dict(plan_id=plan["plan_id"],selection=dict(plan["selection"],dest_relative="other"),confirmed="CREATE_HARDLINKS"))
            job=client.call("/execute",dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS",task_name="HTTP任务"))
            for _ in range(200):
                result=client.call("/jobs/"+job["job_id"])
                if result["done"]:break
                time.sleep(.01)
            self.assertEqual(result["created"],1);self.assertEqual(result["error"],"")
            self.assertEqual(client.call("/history")["jobs"][0]["task_name"],"HTTP任务")
        finally:server.shutdown();server.server_close();thread.join()
    def test_legacy_environment_fallback_does_not_expand_roots(self):
        with patch.dict(os.environ,{"SOURCE_ROOT":str(self.source),"DEST_ROOT":str(self.dest),"STATE_ROOT":str(self.state)},clear=True):
            service=RegionService.from_environment({"active":True})
        self.assertEqual(service.region_list()["regions"][0]["id"],"legacy")
        self.assertEqual(service.region_list()["regions"][0]["source_root"],str(self.source))

class ClientModelTests(unittest.TestCase):
    def test_tasks_exclude_secrets_and_never_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            store=TaskStore(directory)
            task=dict(url="http://nas:8787",region_id="asmr",source_relative="作者",dest_relative="作者",name_bytes=240,token="DO_NOT_SAVE",password="ALSO_SECRET")
            store.save("作者收藏",task);loaded=store.load()["作者收藏"]
            self.assertNotIn("token",loaded);self.assertNotIn("password",loaded)
            self.assertNotIn("DO_NOT_SAVE",store.path.read_text("utf-8"))
    def test_corrupt_task_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store=TaskStore(directory);store.path.write_text("broken")
            with self.assertRaises(ValueError):store.save("name",dict(url="url",region_id="a",source_relative="",dest_relative="",name_bytes=240))
            self.assertEqual(store.path.read_text(),"broken")
    def test_preview_invalid_after_selection_change_and_back(self):
        state=PreviewState();selection=dict(region_id="a",source_relative="author",dest_relative="author",name_bytes=240)
        key=choice_key("http://nas","secret",selection);plan={"rows":[{"status":"待创建"}],"errors":[]}
        self.assertTrue(state.accept(plan,key,key));self.assertTrue(state.executable(key))
        state.invalidate();self.assertFalse(state.executable(key))
        changed=choice_key("http://nas","secret",dict(selection,name_bytes=180))
        self.assertFalse(state.accept(plan,key,changed));self.assertIsNone(state.plan)
    def test_ui_traces_invalidate_plan_without_network(self):
        from docker_client import Studio
        with tempfile.TemporaryDirectory() as directory,patch("docker_client.APP",Path(directory)):
            app=Studio();app.root.withdraw()
            try:
                region=dict(id="asmr",name="区域",source_root="/media/source",destination_root="/media/dest",source_display="NAS源",destination_display="NAS入口")
                app.regions={"asmr":region};app.connected=(app.url.get().strip(),app.token.get());app.apply_region("asmr")
                key=app.current_key();plan={"rows":[{"status":"待创建"}],"errors":[]}
                app.preview.accept(plan,key,key);app.dest_rel.set("changed")
                self.assertIsNone(app.preview.plan);self.assertEqual(str(app.runbtn["state"]),"disabled")
            finally:app.root.destroy()
if __name__=="__main__":unittest.main()
