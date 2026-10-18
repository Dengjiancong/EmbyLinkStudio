from testing_support import cleanup_after_idle
import os,time,tempfile,threading,unittest
from pathlib import Path
from xml.etree import ElementTree as ET
from region_service import RegionService
from nas_agent import make_server
from client_api import AgentClient
class IncrementalFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.src=self.root/"src";self.src.mkdir();self.dst=self.root/"dst";self.state=self.root/"state"
        self.service=RegionService({"regions":[dict(id="a",name="test",source_root=str(self.src),destination_root=str(self.dst))]},self.state,{"active":True})
    def tearDown(self):cleanup_after_idle(self,self.temp,self.service.operation_lock)
    def file(self,name,content=b"video",old=True):
        p=self.src/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        if old:os.utime(p,(time.time()-1000,time.time()-1000))
        return p
    def selection(self,**kw):
        return dict(region_id="a",source_relative="",dest_relative="",name_bytes=80,settle_seconds=300,write_nfo=True,**kw)
    def preview(self,**kw):
        selection=self.selection();selection.update(kw)
        return self.service.preview_selection(selection)
    def execute(self,plan):
        job=self.service.start_selection(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS"))
        for _ in range(300):
            result=self.service.job(job["job_id"])
            if result["done"] and not self.service.operation_lock.locked():return self.service.job(job["job_id"])
            time.sleep(.01)
        self.fail("timeout")
    def test_title_nfo_and_incremental_reuse(self):
        name="完整标题"+"长"*35+"&标题.webm";original=self.file(name)
        plan=self.preview();self.assertEqual(len(plan["rows"]),2)
        self.assertEqual(plan["rows"][-1]["action"],"nfo")
        self.assertFalse(self.dst.exists());self.assertFalse(self.state.exists())
        job=self.execute(plan);self.assertEqual(job["error"],"")
        self.assertEqual((job["created"],job["metadata_created"],job["processed"]),(1,1,2))
        nfo=Path(plan["rows"][-1]["dest"])
        self.assertEqual(ET.parse(nfo).findtext("title"),original.stem)
        self.assertEqual(original.read_bytes(),b"video")
        again=self.preview();self.assertFalse(any(r["status"]=="待创建" for r in again["rows"]))
        self.file("新增.mp4")
        increment=self.preview()
        self.assertEqual(sum(r["status"]=="待创建" for r in increment["rows"]),2)
    def test_add_nfo_to_existing_link_without_recreating_link(self):
        self.file("movie.mp4")
        self.assertEqual(self.execute(self.preview(write_nfo=False))["created"],1)
        plan=self.preview();pending=[r for r in plan["rows"] if r["status"]=="待创建"]
        self.assertEqual([r["action"] for r in pending],["nfo"])
        result=self.execute(plan)
        self.assertEqual((result["created"],result["metadata_created"]),(0,1))
    def test_existing_nfo_not_overwritten(self):
        self.file("movie.mp4");self.dst.mkdir();nfo=self.dst/"movie.nfo";nfo.write_bytes(b"manual metadata")
        plan=self.preview();self.assertIn("已有元数据",[r["status"] for r in plan["rows"]])
        self.assertEqual(self.execute(plan)["error"],"");self.assertEqual(nfo.read_bytes(),b"manual metadata")
    def test_original_nfo_used_without_generated_duplicate(self):
        self.file("movie.mp4");self.file("movie.nfo",b"original nfo")
        plan=self.preview()
        self.assertFalse(any(r["action"]=="nfo" for r in plan["rows"]))
        self.assertEqual(self.execute(plan)["created"],2)
        self.assertTrue(os.path.samefile(self.src/"movie.nfo",self.dst/"movie.nfo"))
    def test_recent_group_is_deferred_and_temporary_files_excluded(self):
        self.file("movie.mp4",old=False);self.file("movie.jpg")
        plan=self.preview()
        self.assertTrue(all(r["status"]=="暂缓处理" for r in plan["rows"]))
        self.file("movie.mp4.aria2",b"download")
        plan=self.preview(settle_seconds=0)
        self.assertTrue(all(r["status"]=="暂缓处理" for r in plan["rows"]))
        self.assertTrue(any("临时" in r["reason"] for r in plan["skipped_entries"]))
        self.assertFalse(self.dst.exists())
    def test_change_between_scans_even_preserved_mtime_is_deferred(self):
        p=self.file("movie.mp4");self.preview(write_nfo=False)
        old=p.stat().st_mtime;p.write_bytes(b"changed content");os.utime(p,(old,old))
        self.assertEqual(self.preview()["rows"][0]["status"],"暂缓处理")
    def test_change_after_preview_stops_without_writing(self):
        p=self.file("movie.mp4");plan=self.preview();p.write_bytes(b"changed")
        result=self.execute(plan)
        self.assertTrue(result["error"]);self.assertEqual(result["created"],0)
        self.assertFalse(self.dst.exists())
    def test_nfo_exclusive_create_refuses_race(self):
        self.file("movie.mp4");plan=self.preview()
        self.dst.mkdir();nfo=Path(plan["rows"][-1]["dest"]);nfo.write_bytes(b"external")
        result=self.execute(plan)
        self.assertTrue(result["error"]);self.assertEqual(nfo.read_bytes(),b"external")
    def test_options_are_bound_to_confirmation(self):
        self.file("movie.mp4");plan=self.preview()
        changed=dict(plan["selection"],write_nfo=False)
        with self.assertRaises(ValueError):self.service.start_selection(dict(plan_id=plan["plan_id"],selection=changed,confirmed="CREATE_HARDLINKS"))
        for changes in (dict(settle_seconds=-1),dict(settle_seconds=True),dict(write_nfo="yes")):
            with self.assertRaises(ValueError):self.preview(**changes)
    def test_http_nfo_preview_and_execution(self):
        self.file("movie.mp4")
        server=make_server(self.service,"x"*32,("127.0.0.1",0));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=AgentClient("http://127.0.0.1:"+str(server.server_port),"x"*32)
            plan=client.call("/preview",self.selection())
            job=client.call("/execute",dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS"))
            for _ in range(200):
                job=client.call("/jobs/"+job["job_id"])
                if job["done"]:break
                time.sleep(.01)
            self.assertEqual(job["error"],"");self.assertEqual(job["metadata_created"],1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_ambiguous_nfo_stem_is_not_generated(self):
        self.file("movie.mp4");self.file("movie.mkv")
        plan=self.preview()
        self.assertEqual(sum(r["status"]=="元数据冲突" for r in plan["rows"]),1)
        job=self.execute(plan);self.assertEqual(job["created"],2);self.assertEqual(job["metadata_created"],0)
    def test_existing_uppercase_nfo_is_preserved(self):
        self.file("movie.mp4");self.dst.mkdir();nfo=self.dst/"movie.NFO";nfo.write_bytes(b"manual")
        self.assertEqual(self.execute(self.preview())["metadata_created"],0)
        self.assertEqual(nfo.read_bytes(),b"manual")
    def test_temporary_subdirectory_is_not_visited(self):
        self.file("downloading.part/movie.mp4")
        plan=self.preview();self.assertEqual(plan["rows"],[])
        self.assertEqual(plan["skipped"],1)
    def test_settings_save_new_options_without_secrets(self):
        from client_models import TaskStore
        store=TaskStore(self.root/"settings");task=dict(url="http://nas",**self.selection(),token="never-save")
        store.save("收藏",task);saved=store.load()["收藏"]
        self.assertEqual(saved["settle_seconds"],300);self.assertTrue(saved["write_nfo"])
        self.assertNotIn("never-save",store.path.read_text(encoding="utf-8"))
    def test_new_ui_options_invalidate_preview(self):
        from unittest.mock import patch
        import docker_client
        with patch.object(docker_client,"APP",self.root/"settings"):
            app=docker_client.Studio();app.root.withdraw()
            try:
                region=dict(id="a",name="test",source_root="/media/src",destination_root="/media/dst",source_display="source",destination_display="dest")
                app.regions={"a":region};app.apply_region("a")
                for variable,value in ((app.settle,"600"),(app.write_nfo,True)):
                    key=app.current_key();app.preview.accept({"rows":[{"status":"待创建"}],"errors":[]},key,key)
                    variable.set(value)
                    self.assertIsNone(app.preview.plan);self.assertEqual(str(app.runbtn["state"]),"disabled")
            finally:app.root.destroy()

    def test_download_marker_appearing_after_preview_stops_before_media_write(self):
        self.file("movie.mp4");plan=self.preview()
        self.file("movie.mp4.aria2")
        result=self.execute(plan)
        self.assertTrue(result["error"]);self.assertEqual(result["created"],0)
        self.assertFalse(self.dst.exists())
