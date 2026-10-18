import json,os,socket,tempfile,threading,time,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from nas_agent import Executor,make_server
from region_service import RegionService
from client_api import AgentClient
import safe_fs
class HardlinkReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.src=self.root/"source";self.src.mkdir();self.dst=self.root/"links";self.state=self.root/"state"
    def tearDown(self):self.temp.cleanup()
    def engine(self):return Executor(self.src,self.dst,self.state)
    def wait(self,engine,job):
        for _ in range(300):
            job=engine.job(job["job_id"])
            if job["done"] and not engine.lock.locked():return engine.job(job["job_id"])
            time.sleep(.01)
        self.fail("timeout")
    def test_pair_at_length_boundary_uses_same_stem(self):
        stem="x"*76
        for ext in (".mp4",".jpeg"):(self.src/(stem+ext)).write_bytes(b"data")
        plan=self.engine().preview(80)
        self.assertEqual(len({Path(r["newrel"]).stem for r in plan["rows"]}),1)
        self.assertTrue(all(len(Path(r["newrel"]).name.encode())<=80 for r in plan["rows"]))
    def test_existing_legacy_name_not_renamed_or_silently_mispaired(self):
        stem="x"*76
        for ext in (".mp4",".jpeg"):(self.src/(stem+ext)).write_bytes(b"data")
        self.dst.mkdir();os.link(self.src/(stem+".mp4"),self.dst/(stem+".mp4"))
        plan=self.engine().preview(80)
        movie=next(r for r in plan["rows"] if r["source"].endswith(".mp4"))
        image=next(r for r in plan["rows"] if r["source"].endswith(".jpeg"))
        self.assertEqual(movie["status"],"已有硬链接");self.assertEqual(image["status"],"目标冲突")
        self.assertTrue((self.dst/(stem+".mp4")).exists())
    def test_root_replacement_after_preview_stops_before_writes(self):
        (self.src/"movie.mp4").write_bytes(b"original")
        engine=self.engine();plan=engine.preview();self.src.rename(self.root/"moved");self.src.mkdir();(self.src/"movie.mp4").write_bytes(b"replacement")
        job=self.wait(engine,engine.start(plan["plan_id"],"CREATE_HARDLINKS"))
        self.assertTrue(job["error"]);self.assertEqual(job["created"],0);self.assertFalse(self.dst.exists())
    def test_journal_truncated_tail_warns_and_interior_corruption_blocks(self):
        (self.src/"movie.mp4").write_bytes(b"original");self.state.mkdir();log=self.state/"created.jsonl";log.write_text('{"source":',encoding="utf-8")
        plan=self.engine().preview();self.assertTrue(plan["warnings"]);self.assertFalse(self.dst.exists())
        log.write_text('{"source":\n',encoding="utf-8")
        with self.assertRaises(ValueError):self.engine().preview()
    def test_post_link_identity_failure_is_partial_not_zero_created(self):
        (self.src/"movie.mp4").write_bytes(b"original");engine=self.engine();plan=engine.preview()
        with patch("nas_agent.native_hardlink",side_effect=safe_fs.LinkCreatedError("injected identity failure")):
            job=self.wait(engine,engine.start(plan["plan_id"],"CREATE_HARDLINKS"))
        self.assertEqual(job["created"],1);self.assertEqual(job["result_class"],"partial")
    def test_production_metadata_writes_default_off(self):
        with patch.dict(os.environ,{"SOURCE_ROOT":str(self.src),"DEST_ROOT":str(self.dst),"STATE_ROOT":str(self.state)},clear=True):
            service=RegionService.from_environment({"active":True})
        self.assertFalse(service.health()["features"]["metadata_writes"])
        selection=dict(region_id="legacy",source_relative="",dest_relative="",name_bytes=240)
        with self.assertRaises(ValueError):service.preview_selection(dict(selection,write_nfo=True))
        with self.assertRaises(ValueError):service.metadata_preview(selection)
        self.assertFalse(self.dst.exists())
    def test_source_state_output_physical_alias_detection(self):
        with patch("nas_agent.physical_overlap",return_value=True):
            with self.assertRaises(ValueError):self.engine()
    def test_busy_form_freezes_and_preserves_accepted_preview(self):
        import docker_client
        with patch.object(docker_client,"APP",self.root/"prefs"):
            app=docker_client.Studio();app.root.withdraw()
            try:
                app.freeze_form(True)
                self.assertEqual(str(app.area["state"]),"disabled")
                self.assertEqual(str(app.token_entry["state"]),"disabled")
                app.freeze_form(False)
                self.assertEqual(str(app.area["state"]),"readonly")
                key=app.current_key();plan={"rows":[{"status":"待创建"}],"errors":[]}
                app.preview.accept(plan,key,key);app.refresh_metadata_controls()
                self.assertIs(app.preview.plan,plan)
                self.assertFalse(app.write_nfo.get());self.assertFalse(app.advanced_expanded)
                app.toggle_advanced();self.assertTrue(app.advanced_expanded)
            finally:app.root.destroy()
    def test_directory_picker_does_not_choose_unread_directory(self):
        from directory_picker import DirectoryPicker
        import tkinter as tk
        class FailedClient:
            def call(self,*args):raise RuntimeError("unreadable")
        root=tk.Tk();root.withdraw();chosen=[]
        picker=DirectoryPicker(root,FailedClient(),dict(id="a",name="test",source_display="/source",destination_display="/dest"),"source",chosen.append)
        try:
            self.assertEqual(str(picker.choosebtn["state"]),"disabled");picker.choose();self.assertEqual(chosen,[])
        finally:picker.close();root.destroy()
    @unittest.skipUnless(safe_fs.PINNED,"Requires POSIX directory descriptors")
    def test_pinned_directory_swap_never_writes_to_replacement(self):
        (self.src/"movie.mp4").write_bytes(b"original");self.dst.mkdir();outside=self.root/"outside";outside.mkdir()
        original=os.link;moved=self.root/"moved";swapped=[False]
        def race(src,dest,*args,**kwargs):
            if not swapped[0]:
                swapped[0]=True;self.dst.rename(moved);self.dst.symlink_to(outside,target_is_directory=True)
            return original(src,dest,*args,**kwargs)
        with patch("safe_fs.os.link",side_effect=race):
            with self.assertRaises(safe_fs.LinkCreatedError):safe_fs.link(self.src/"movie.mp4",self.dst/"movie.mp4")
        self.assertFalse((outside/"movie.mp4").exists());self.assertTrue((moved/"movie.mp4").exists())
    @unittest.skipUnless(safe_fs.PINNED,"Requires POSIX directory descriptors")
    def test_pinned_source_swap_is_detected_after_link(self):
        source=self.src/"movie.mp4";source.write_bytes(b"original");self.dst.mkdir();outside=self.root/"external";outside.write_bytes(b"keep")
        original=os.link
        def race(src,dest,*args,**kwargs):
            source.rename(self.src/"old");source.symlink_to(outside)
            return original(src,dest,*args,**kwargs)
        with patch("safe_fs.os.link",side_effect=race):
            with self.assertRaises(safe_fs.LinkCreatedError):safe_fs.link(source,self.dst/"movie.mp4")
        self.assertEqual(outside.read_bytes(),b"keep")
class HttpReviewTests(unittest.TestCase):
    def test_no_redirect_can_forward_bearer_token(self):
        received=[]
        class Target(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                received.append(self.headers.get("Authorization"));self.send_response(200);self.end_headers();self.wfile.write(b"{}")
        target=ThreadingHTTPServer(("127.0.0.1",0),Target)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(302);self.send_header("Location","http://127.0.0.1:"+str(target.server_port)+"/health");self.end_headers()
        redirect=ThreadingHTTPServer(("127.0.0.1",0),Redirect)
        threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (target,redirect)]
        for t in threads:t.start()
        try:
            with self.assertRaisesRegex(RuntimeError,"重定向"):AgentClient("http://127.0.0.1:"+str(redirect.server_port),"secret"*6).call("/health")
            self.assertEqual(received,[])
        finally:
            for s in (target,redirect):s.shutdown();s.server_close()
            for t in threads:t.join()
    def test_excess_connections_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/"src";source.mkdir();engine=Executor(source,root/"dst",root/"state")
            server=make_server(engine,"x"*32,("127.0.0.1",0));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            for _ in range(32):server.slots.acquire()
            try:
                with self.assertRaises(RuntimeError):AgentClient("http://127.0.0.1:"+str(server.server_port),"x"*32).call("/health")
                self.assertFalse((root/"dst").exists())
            finally:
                for _ in range(32):server.slots.release()
                server.shutdown();server.server_close();thread.join()
