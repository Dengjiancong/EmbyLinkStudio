from testing_support import cleanup_after_idle
import csv,json,os,tempfile,threading,time,unittest,urllib.parse
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from region_service import RegionService
from nas_agent import make_server
from client_api import AgentClient
from qbit_reader import QbitReader
class AdvancedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.src=self.root/"src";self.src.mkdir();self.dst=self.root/"dst";self.state=self.root/"state"
        self.config={"regions":[dict(id="a",name="test",source_root=str(self.src),destination_root=str(self.dst))]}
        self.service=RegionService(self.config,self.state,{"active":True});self.selection=dict(region_id="a",source_relative="",dest_relative="",name_bytes=240)
    def tearDown(self):cleanup_after_idle(self,self.temp,self.service.operation_lock)
    def video(self,name="movie.mp4"):
        p=self.src/name;p.write_bytes(b"video");return p
    def wait(self,job):
        for _ in range(300):
            result=self.service.job(job["job_id"])
            if result["done"] and not self.service.operation_lock.locked():return self.service.job(job["job_id"])
            time.sleep(.01)
        self.fail("timeout")
    def link(self):
        plan=self.service.preview_selection(self.selection)
        return self.wait(self.service.start_selection(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS")))
    def update(self,plan,ids="ALL_ELIGIBLE"):
        return self.wait(self.service.metadata_execute(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="UPDATE_NFO_TITLES",row_ids=ids,task_name="title update")))
    def test_nfo_diff_update_backup_and_other_fields_preserved(self):
        source=self.video();self.link()
        nfo=self.dst/"movie.nfo";old=b'<?xml version="1.0"?><movie><!--keep--><title>A very long old title</title><originaltitle>old</originaltitle><plot>preserve plot</plot></movie>'
        nfo.write_bytes(old);before=nfo.read_bytes()
        plan=self.service.metadata_preview(self.selection);row=plan["rows"][0]
        self.assertEqual(row["old_title"],"A very long old title");self.assertEqual(row["new_title"],"movie")
        self.assertEqual(nfo.read_bytes(),before);self.assertFalse((self.state/"nfo-backups").exists())
        job=self.update(plan)
        self.assertEqual(job["error"],"");self.assertEqual(job["metadata_updated"],1)
        xml=ET.parse(nfo);self.assertEqual(xml.findtext("title"),"movie");self.assertEqual(xml.findtext("plot"),"preserve plot")
        self.assertIn(b"<!--keep-->",nfo.read_bytes());self.assertEqual(source.read_bytes(),b"video")
        backups=list((self.state/"nfo-backups").glob("*.nfo"));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),old)
        self.assertGreaterEqual(nfo.stat().st_size,len(old))
        self.assertEqual(self.service.metadata_preview(self.selection)["rows"][0]["status"],"已一致")
    def test_shared_original_nfo_is_refused_and_never_modified(self):
        self.video();original=self.src/"movie.nfo";original.write_bytes(b"<movie><title>original</title></movie>");self.link()
        plan=self.service.metadata_preview(self.selection)
        self.assertEqual(plan["rows"][0]["status"],"不可更新");self.assertIn("共用",plan["rows"][0]["reason"])
        with self.assertRaises(ValueError):self.update(plan)
        self.assertEqual(original.read_bytes(),b"<movie><title>original</title></movie>")
    def test_changed_nfo_after_preview_is_not_overwritten(self):
        self.video();self.link();nfo=self.dst/"movie.nfo";nfo.write_bytes(b"<movie><title>old</title></movie>")
        plan=self.service.metadata_preview(self.selection);nfo.write_bytes(b"<movie><title>external</title></movie>")
        job=self.update(plan);self.assertTrue(job["error"]);self.assertEqual(job["metadata_updated"],0)
        self.assertEqual(ET.parse(nfo).findtext("title"),"external")
    def test_update_only_selected_and_explicit_confirmation(self):
        self.video("a.mp4");self.video("b.mp4");self.link()
        for name in ("a","b"):(self.dst/(name+".nfo")).write_bytes(b"<movie><title>old</title></movie>")
        plan=self.service.metadata_preview(self.selection)
        with self.assertRaises(ValueError):self.service.metadata_execute(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS",row_ids="ALL_ELIGIBLE"))
        job=self.update(plan,[plan["rows"][0]["row_id"]]);self.assertEqual(job["metadata_updated"],1)
        self.assertEqual(ET.parse(self.dst/"b.nfo").findtext("title"),"old")
    def test_invalid_xml_and_doctype_are_refused(self):
        self.video();self.link();path=self.dst/"movie.nfo"
        for content in (b"bad xml",b'<!DOCTYPE movie [<!ENTITY x "entity">]><movie><title>&x;</title></movie>','<!DOCTYPE movie [<!ENTITY x "entity">]><movie><title>&x;</title></movie>'.encode("utf-16")):
            path.write_bytes(content);plan=self.service.metadata_preview(self.selection)
            self.assertEqual(plan["rows"][0]["status"],"不可更新");self.assertEqual(path.read_bytes(),content)
    def test_audit_readonly_categories_and_pagination(self):
        self.video("normal.mp4");self.video("missing.mp4");self.video("conflict.mp4");self.dst.mkdir()
        os.link(self.src/"normal.mp4",self.dst/"normal.mp4");(self.dst/"conflict.mp4").write_bytes(b"copy")
        job=self.service.audit_start(self.selection)
        for _ in range(300):
            report=self.service.audit_page(dict(audit_id=job["audit_id"],offset=0,limit=2))
            if report["done"]:break
            time.sleep(.01)
        self.assertEqual(report["counts"],{"正常":1,"缺失":1,"冲突":1});self.assertEqual(len(report["rows"]),2)
        last=self.service.audit_page(dict(audit_id=job["audit_id"],offset=2,limit=2))
        self.assertEqual(len(last["rows"]),1);self.assertFalse(self.state.exists())
        with self.assertRaises(ValueError):self.service.audit_page(dict(audit_id=job["audit_id"],limit=501))
    def test_execution_readiness_and_journal_work_are_bounded(self):
        for i in range(40):self.video(str(i)+".mp4")
        import nas_agent
        with patch("nas_agent.readiness",wraps=nas_agent.readiness) as check:
            result=self.link()
        self.assertEqual(result["created"],40);self.assertLess(check.call_count,10)
        events=(self.state/"history.jsonl").read_text("utf-8").splitlines()
        self.assertLess(len(events),15)
    def test_paged_gui_render_and_selection_indices(self):
        import docker_client
        with patch.object(docker_client,"APP",self.root/"prefs"):
            app=docker_client.Studio();app.root.withdraw()
            try:
                app.rows=[dict(status="待创建",reason="",bytes=1,rel=str(i),newrel=str(i)) for i in range(1200)]
                app.render();self.assertEqual(len(app.tree.get_children()),500)
                app.move_preview_page(1);self.assertEqual(app.tree.get_children()[0],"500")
                app.move_preview_page(1);self.assertEqual(len(app.tree.get_children()),200)
            finally:app.root.destroy()
    def test_missing_qbit_configuration_cannot_be_bypassed(self):
        self.video()
        with self.assertRaises(ValueError):self.service.preview_selection(dict(self.selection,use_downloader=True))
        self.assertFalse(self.dst.exists())
    def test_advanced_http_endpoints_are_authenticated(self):
        self.video();self.link();(self.dst/"movie.nfo").write_bytes(b"<movie><title>old</title></movie>")
        server=make_server(self.service,"x"*32,("127.0.0.1",0));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url="http://127.0.0.1:"+str(server.server_port);client=AgentClient(url,"x"*32)
            with self.assertRaises(RuntimeError):AgentClient(url,"wrong").call("/metadata/preview",self.selection)
            plan=client.call("/metadata/preview",self.selection)
            job=client.call("/metadata/execute",dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="UPDATE_NFO_TITLES",row_ids="ALL_ELIGIBLE"))
            self.assertEqual(self.wait(job)["metadata_updated"],1)
        finally:server.shutdown();server.server_close();thread.join()
    def test_gui_metadata_confirmation_submits_correct_endpoint(self):
        import docker_client
        self.video();self.link();nfo=self.dst/"movie.nfo";nfo.write_bytes(b"<movie><title>old</title></movie>")
        server=make_server(self.service,"x"*32,("127.0.0.1",0));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with patch.object(docker_client,"APP",self.root/"prefs"):
            app=docker_client.Studio();app.root.withdraw()
            try:
                app.url.set("http://127.0.0.1:"+str(server.server_port));app.token.set("x"*32)
                app.regions={r["id"]:r for r in self.service.region_list()["regions"]};app.apply_region("a");app.settle.set("0")
                app.connected=(app.url.get(),app.token.get());client,_=app.api()
                plan=self.service.metadata_preview(app.selection());app.show_metadata_plan(client,plan,app.current_key())
                def children(widget):
                    result=list(widget.winfo_children())
                    for child in list(result):result.extend(children(child))
                    return result
                button=next(w for w in children(app.root) if hasattr(w,"cget") and w.winfo_class()=="TButton" and w.cget("text")=="确认更新全部可更新项")
                with patch.object(docker_client.messagebox,"askyesno",return_value=True),patch.object(docker_client.messagebox,"showinfo"),patch.object(docker_client.messagebox,"showwarning"):
                    button.invoke();deadline=time.monotonic()+7
                    while app.busy and time.monotonic()<deadline:app.root.update();time.sleep(.01)
                self.assertFalse(app.busy);self.assertIn("全部完成",app.status.get())
                self.assertEqual(ET.parse(nfo).findtext("title"),"movie")
            finally:app.root.destroy()
        server.shutdown();server.server_close();thread.join()
class QbitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.src=Path(self.temp.name);self.mode="complete";self.calls=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                owner.calls.append(self.path)
                self.send_response(200);self.end_headers();self.wfile.write(b"Ok.")
            def do_GET(self):
                owner.calls.append(self.path)
                if owner.mode=="offline":self.send_response(503);self.end_headers();return
                if self.path.endswith("/torrents/info"):
                    data=[dict(hash="abc",name="movie.mp4",save_path="/downloads",content_path="/downloads/movie.mp4",state="checkingUP" if owner.mode=="checking" else "uploading",amount_left=1 if owner.mode=="incomplete" else 0,progress=1)]
                else:data=[dict(name="movie.mp4",size=5,progress=0 if owner.mode=="file-incomplete" else 1,priority=1)]
                self.send_response(200);self.end_headers();self.wfile.write(json.dumps(data).encode())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.config=dict(url="http://127.0.0.1:"+str(self.server.server_port),username_env="TEST_QB_USER",password_env="TEST_QB_PASS",path_mappings=[dict(remote="/downloads",local=str(self.src))])
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def test_api_completion_file_progress_and_state_checks(self):
        reader=QbitReader(self.config)
        with patch.dict(os.environ,TEST_QB_USER="user",TEST_QB_PASS="private-password"):
            reader.refresh();self.assertTrue(reader.check(self.src/"movie.mp4",5)[0])
            for mode in ("checking","incomplete","file-incomplete"):
                self.mode=mode;reader.refresh();self.assertFalse(reader.check(self.src/"movie.mp4",5)[0])
            self.mode="complete";reader.refresh();self.assertFalse(reader.check(self.src/"movie.mp4",6)[0])
        self.assertTrue(all("/auth/login" in x or "/torrents/" in x for x in self.calls))
    def test_unmatched_is_fail_closed_and_api_error_has_no_secrets(self):
        reader=QbitReader(self.config);reader.refresh()
        self.assertFalse(reader.check(self.src/"other.mp4",5)[0])
        self.mode="offline"
        with self.assertRaises(RuntimeError) as error:reader.refresh()
        self.assertNotIn("private-password",str(error.exception))
        self.assertFalse(reader.entries)
    def test_path_mapping_no_prefix_escape(self):
        reader=QbitReader(self.config)
        self.assertEqual(reader.mapped("/downloads/movie.mp4"),str(self.src/"movie.mp4"))
        self.assertIsNone(reader.mapped("/downloads-elsewhere/movie.mp4"))
        self.assertIsNone(reader.mapped("/downloads/../secret"))

    def test_service_rechecks_qbit_before_mutation(self):
        media=self.src/"media";media.mkdir();(media/"movie.mp4").write_bytes(b"video")
        dst=self.src/"links";state=self.src/"state"
        cfg=dict(self.config,path_mappings=[dict(remote="/downloads",local=str(media))])
        service=RegionService({"downloaders":{"qb":cfg},"regions":[dict(id="a",name="test",source_root=str(media),destination_root=str(dst),downloader="qb")]},state,{"active":True})
        selection=dict(region_id="a",source_relative="",dest_relative="",name_bytes=240,use_downloader=True)
        plan=service.preview_selection(selection);self.assertEqual(plan["rows"][0]["status"],"待创建")
        self.mode="incomplete"
        job=service.start_selection(dict(plan_id=plan["plan_id"],selection=plan["selection"],confirmed="CREATE_HARDLINKS"))
        for _ in range(300):
            job=service.job(job["job_id"])
            if job["done"]:break
            time.sleep(.01)
        self.assertTrue(job["error"]);self.assertEqual(job["created"],0);self.assertFalse(dst.exists())
        self.assertEqual(service.preview_selection(selection)["rows"][0]["status"],"暂缓处理")
